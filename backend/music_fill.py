"""Music repair: fills the holes that are left in the kept background music.

When the original voices speak, the dubbed voice takes their place and the background is silenced there
(bg_duck.mute_speech). Where the separator did not leave any usable music in those stretches, the music has a hole
in it. This module finds those holes and asks a music inpainting model on fal.ai (Stable Audio 3 small, music,
audio inpainting) to continue the music across them, using the music before and after as the context.

Only the hole itself is replaced, with a short crossfade at both edges and the level matched to the music around it.
Nothing here raises: fill() always returns an info dict, and when anything is wrong the background stays exactly as
it was (silence, or the music that was kept). A hole is only filled when there is real music next to it, so a video
without any music costs nothing.

Settings (environment variables):
  MUSIC_FILL=0            switch the feature off for everybody
  MUSIC_FILL_MAX_GAP_SEC  longest hole that is filled (default 20; longer holes are left as they are)
  MUSIC_FILL_MAX_GAPS     most holes filled per job (default 200: no practical limit, the user pays per hole)
  MUSIC_FILL_MAX_SEC      most seconds of holes filled per job (default 3600)
  MUSIC_FILL_MAX_WALL_SEC time allowed for all holes of one job together (default 900)
  MUSIC_FILL_STEPS        sampling steps sent to the model (default: the model's own default)
"""
import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

ENABLED = os.environ.get("MUSIC_FILL", "1").strip().lower() not in ("0", "off", "no", "false")
MODEL = "fal-ai/stable-audio-3/small/music/base/audio-inpainting"

RATE = 44100
CH = 2
HOP_SEC = 0.1

MIN_GAP_SEC = 1.0              # shorter holes are not worth a call
MAX_GAP_SEC = float(os.environ.get("MUSIC_FILL_MAX_GAP_SEC", "20") or 20)
MAX_GAPS = int(float(os.environ.get("MUSIC_FILL_MAX_GAPS", "200") or 200))
MAX_TOTAL_SEC = float(os.environ.get("MUSIC_FILL_MAX_SEC", "3600") or 3600)
# Fal's request history (fal_request_history.py) shows that every clean result of this model was requested with
# guidance_scale 1, 50 steps and prompt expansion off, and that every request that left them out, so the model's own
# defaults applied, came back saturated. They are sent explicitly.
STEPS = os.environ.get("MUSIC_FILL_STEPS", "50").strip()
GUIDANCE = os.environ.get("MUSIC_FILL_GUIDANCE", "1").strip()
OUTPUT_FORMAT = os.environ.get("MUSIC_FILL_FORMAT", "wav").strip().lower() or "wav"

REF_FLOOR_DB = -50.0           # quieter than this is not music
GAP_BELOW_REF_DB = 30.0        # a hole is this far under the typical music level (digital silence, a hard mute)
CTX_MUSIC_REL_DB = 12.0        # context frames must be within this of the typical level to count as music
CTX_NEED_SEC = 1.0             # a one-second clean musical reference is enough to attempt continuation
CTX_LOOK_SEC = 6.0
MIN_MUSIC_SEC = 1.0            # measured clean music; never use the removed speech as a reference
GAP_PAD_SEC = 0.1
JOIN_HOLES_SEC = 0.5           # two holes closer than this are one hole
CONTEXT_SEC = 10.0             # music sent to the model on each side of a hole
MIN_WINDOW_SEC = 12.0
MAX_WINDOW_SEC = 60.0
XFADE_SEC = 0.2
TARGET_BELOW_CTX_DB = 2.0      # the filled music is this much under the music around it
GAIN_MAX_DB = 6.0                        # amplification limit; attenuation matches the measured context
GEN_MIN_DB = -60.0             # a result quieter than this is silence
MAX_CLIPPED_SHARE = 0.10       # reject severe saturation before attenuation can hide its level
SEAM_MAX_DB = 9.0              # level difference allowed between a seam and the music beside it
MAX_FILE_SEC = 3600.0
CALL_TIMEOUT_SEC = 240
MAX_WALL_SEC = float(os.environ.get("MUSIC_FILL_MAX_WALL_SEC", "900") or 900)     # all holes of one job together

DEFAULT_PROMPT = ("the same background sound continues exactly as before: same instruments or sound sources, tempo, texture "
                  "and loudness, smooth and steady, no vocals, no speech")
AMBIENCE_NEGATIVE = ", music, melody, drums, rhythm, beat"     # added when the sound is not music (engine, wind, crowd ...)

# A hole in a steady sound (engine hum, wind, room tone, a held drone) is rebuilt from the clean sound next to it, locally
# and for free. A music model asked to continue it invents music that was never there.
LOCAL_FILL = os.environ.get("MUSIC_FILL_LOCAL", "1").strip().lower() not in ("0", "off", "no", "false")
STEADY_MAX_DB = float(os.environ.get("MUSIC_FILL_STEADY_DB", "2.5") or 2.5)   # band levels vary less than this over time = steady
STEADY_MIN_SEC = 1.2                                                          # least clean sound needed to judge it
GRAIN_SEC = 1.6
LOCAL_MODE = os.environ.get("MUSIC_FILL_LOCAL_MODE", "auto").strip().lower()   # auto | grain | synth
SYNTH_FFT = 8192                # resolution of the sound model used when the clean sound next to a hole is short
SYNTH_MOD_MAX_DB = 2.0          # slow level flutter copied from the real sound is limited to this
BED_BAND_DB = 3.0               # steady engine: only sound this close to the usual quiet level of the track counts as "the engine" (an event next to the hole is not)
BED_LOOK_SEC = 12.0             # the level of the engine is taken from this far around a hole
BED_POOL_SEC = 40.0             # clean engine sound used to model it: the nearest stretches of the whole track, up to this much
AMBIENCE_MODEL = os.environ.get("MUSIC_FILL_AMBIENCE_MODEL", "0").strip().lower() in ("1", "on", "yes", "true")   # ask the music model to invent engine / wind / crowd sound (unreliable: off)
SYNTH_CHUNK_SEC = 60.0          # a rebuilt stretch longer than 1.5 x this is made in pieces of this length (memory)
SEAM_SEC = 0.15                 # a locally rebuilt hole is blended into the real sound this far OUTSIDE of its edges too (no dip at the seam)
LOCAL_FILL_DB = float(os.environ.get("MUSIC_FILL_LOCAL_DB", "-6") or -6)   # a made-up sound is laid this much under the real sound around it: it is not the original, and the dialogue stays clear
LOCAL_MIN_GAP_SEC = 0.15        # a steady sound is rebuilt locally for free, so even a short hole is closed (the model is called only for holes of MIN_GAP_SEC and more)
GRAIN_MAX_HOLE_RATIO = 0.8      # pieces of the real sound are laid down only if the hole is at most this long compared with the sound available (no piece is used twice)
NEGATIVE_PROMPT = "vocals, singing, spoken words, clipping, distortion, harsh noise, watermark, abrupt cutoff"     # the one the clean runs used


def _db(x):
    return 10.0 * np.log10(np.maximum(x, 1e-12))


def _ffmpeg(cmd, timeout=600, stdin=None):
    r = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "ignore")[-300:].strip() or f"ffmpeg exit {r.returncode}")
    return r.stdout


def _to_pcm(src, dst):
    _ffmpeg(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", str(CH), "-ar", str(RATE),
             "-f", "s16le", str(dst)])


def _levels(pcm):
    """Per 100 ms level (dB, mean power of the two channels) of the s16 stereo samples."""
    hop = int(RATE * HOP_SEC)
    n = pcm.shape[0] // hop
    if n == 0:
        return np.zeros(0)
    out = np.empty(n)
    for i in range(0, n, 600):                 # in chunks: the whole file never becomes floats at once
        j = min(n, i + 600)
        blk = pcm[i * hop:j * hop].astype(np.float32) / 32768.0
        out[i:j] = _db((blk ** 2).reshape(j - i, hop, CH).mean(axis=(1, 2)))
    return out


def find_gaps(db, spans, min_len=None, keep_unfillable=False, level_db=None):
    """Holes in the music: stretches inside the speech spans where the track is far under the music level.
    Returns (gaps [(start_sec, end_sec)], info). Gaps need real music next to them.
    keep_unfillable: holes the model cannot take (longer than MAX_GAP_SEC, no music next to them) are returned too, listed in
    info["local_only"]: only the free local rebuild of a steady sound can fill them. level_db: levels of the same track before the
    voices were silenced, pauses only (-120 elsewhere): where the usual level comes from when little is left in `db`."""
    info = {"ref_db": None, "music_sec": 0.0, "found": 0, "too_long": 0, "no_context": 0}
    n = len(db)
    if n == 0 or not spans:
        return [], info
    ldb = level_db if level_db is not None else db
    music = ldb >= REF_FLOOR_DB
    info["music_sec"] = round(float(music.sum()) * HOP_SEC, 1)
    if music.sum() * HOP_SEC < MIN_MUSIC_SEC:
        return [], info
    ref = float(np.median(ldb[music]))
    info["ref_db"] = round(ref, 1)
    spk = np.zeros(n, dtype=bool)
    for a, b in spans:
        spk[max(0, int((a - 0.3) / HOP_SEC)):min(n, int((b + 0.3) / HOP_SEC) + 1)] = True
    hole = spk & (db < ref - GAP_BELOW_REF_DB)
    # close the little islands inside a hole
    j = int(JOIN_HOLES_SEC / HOP_SEC)
    idx = np.flatnonzero(hole)
    runs = []
    if idx.size:
        s = p = idx[0]
        for v in idx[1:]:
            if v - p > j:
                runs.append((s, p + 1))
                s = v
            p = v
        runs.append((s, p + 1))
    gaps = []
    ctx_rel = ref - CTX_MUSIC_REL_DB
    look = int(CTX_LOOK_SEC / HOP_SEC)
    for s, e in runs:
        length = (e - s) * HOP_SEC
        if length < (MIN_GAP_SEC if min_len is None else min_len):
            continue
        if length >= MIN_GAP_SEC:
            info["found"] += 1        # a hole the model would be asked about; shorter ones (found only for the free local fill) are not counted
        else:
            info["short"] = info.get("short", 0) + 1
        local_only = False
        if length > MAX_GAP_SEC:
            info["too_long"] += 1
            if not keep_unfillable:
                continue
            local_only = True
        near = np.concatenate([db[max(0, s - look):s], db[e:e + look]])
        if not local_only and (near >= ctx_rel).sum() * HOP_SEC < CTX_NEED_SEC:
            info["no_context"] += 1
            if not keep_unfillable:
                continue
            local_only = True
        padded_start = max(0, s - int(round(GAP_PAD_SEC / HOP_SEC)))
        padded_end = min(n, e + int(round(GAP_PAD_SEC / HOP_SEC)))
        remaining = np.concatenate([db[max(0, s - look):padded_start], db[padded_end:e + look]])
        if not local_only and (remaining >= ctx_rel).sum() * HOP_SEC < CTX_NEED_SEC:
            # Never consume the only usable reference when padding a one-second continuation.
            padded_start, padded_end = s, e
        gaps.append((float(padded_start * HOP_SEC), float(padded_end * HOP_SEC)))
        if local_only:
            info.setdefault("local_only", []).append(gaps[-1])
        if length < MIN_GAP_SEC:
            info.setdefault("short_gaps", []).append(gaps[-1])
    return gaps, info


def _write_wav(path, pcm):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CH)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.ascontiguousarray(pcm).tobytes())


def _decode_audio(data):
    """Audio file bytes -> s16 stereo array at RATE."""
    raw = _ffmpeg(["ffmpeg", "-v", "error", "-i", "pipe:0", "-vn", "-ac", str(CH), "-ar", str(RATE), "-f", "s16le", "pipe:1"],
                  stdin=data)
    return np.frombuffer(raw, dtype="<i2").reshape(-1, CH)


def _find_audio_url(obj):
    """The result of the model is a dict; the audio file is under "audio" (or a similar key) as {"url": ...}."""
    if isinstance(obj, dict):
        for k in ("audio", "audio_file", "output", "audio_url", "file"):
            v = obj.get(k)
            if isinstance(v, dict) and isinstance(v.get("url"), str):
                return v["url"]
            if isinstance(v, str) and v.startswith("http"):
                return v
        for v in obj.values():
            u = _find_audio_url(v)
            if u:
                return u
    elif isinstance(obj, list):
        for v in obj:
            u = _find_audio_url(v)
            if u:
                return u
    return None


def _fal_run(key, wav_path, m0, m1, prompt):
    """One call of the model. Returns the bytes of the audio it made (the whole window, the hole filled in)."""
    import fal_client
    client = fal_client.SyncClient(key=key)
    url = client.upload_file(str(wav_path))
    args = {"prompt": prompt, "audio_url": url, "mask_start_seconds": round(float(m0), 3),
            "mask_end_seconds": round(float(m1), 3),
            "negative_prompt": NEGATIVE_PROMPT + (AMBIENCE_NEGATIVE if "no music" in prompt.lower() else ""),
            "output_format": OUTPUT_FORMAT, "enable_prompt_expansion": False, "enable_safety_checker": True}
    try:
        args["guidance_scale"] = float(GUIDANCE)
    except ValueError:
        args["guidance_scale"] = 1.0
    if STEPS.isdigit():
        args["num_inference_steps"] = int(STEPS)
    handle = client.submit(MODEL, arguments=args)
    t0 = time.time()
    while True:
        status = handle.status()
        if isinstance(status, fal_client.Completed):
            if getattr(status, "error", None):
                raise RuntimeError(f"model error: {status.error}")
            break
        if time.time() - t0 > CALL_TIMEOUT_SEC:
            raise RuntimeError(f"timed out after {CALL_TIMEOUT_SEC} s (request {handle.request_id})")
        time.sleep(2)
    res = handle.get()
    out = _find_audio_url(res)
    if not out:
        raise RuntimeError(f"no audio in the answer: {json.dumps(res)[:300]}")
    with urllib.request.urlopen(out, timeout=120) as r:
        return r.read()


def describe_music(gemini_key, pcm, loud_db, log=None):
    """One line describing the music (genre, instruments, tempo, mood) from the loudest 20 s of music, or ""."""
    if not gemini_key or pcm is None or pcm.shape[0] < RATE * 5:
        return ""
    try:
        hop = int(RATE * HOP_SEC)
        win = int(20.0 / HOP_SEC)
        n = len(loud_db)
        ok = (loud_db >= REF_FLOOR_DB).astype(float)
        if n <= win:
            a, b = 0, n
        else:
            c = np.convolve(ok, np.ones(win), mode="valid")
            a = int(np.argmax(c))
            b = a + win
        seg = pcm[a * hop:b * hop]
        mono = seg.reshape(-1, CH).mean(axis=1).astype(np.int16)
        raw = _ffmpeg(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:0",
                       "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"], stdin=mono.tobytes())
        import gemini_service
        payload = {"contents": [{"parts": [
            {"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(raw).decode("ascii")}},
            {"text": ("This is the background sound of a film clip (the voices were taken out). Answer with ONE line. "
                      "If there is real music, start with MUSIC: and give comma separated tags: genre or mood, main "
                      "instruments, tempo, energy. If there is NO music (engine noise, wind, crowd, machinery, room tone, "
                      "silence), start with AMBIENCE: and describe the sound itself as comma separated tags: what makes "
                      "it, steady or changing, pitch, texture. Never invent music that is not there. No vocals. "
                      "No sentences, at most 25 words. Return just the line.")}]}],
            "generationConfig": {"maxOutputTokens": 512}}
        data, err = gemini_service.call_gemini(gemini_key, payload, timeout=60)
        if data is None:
            if log:
                log(f"music description failed: {str(err)[:150]}")
            return ""
        txt = data["candidates"][0]["content"]["parts"][0]["text"].strip().strip("`").strip()
        txt = " ".join(txt.split())[:240]
        low = txt.lower()
        if low.startswith("ambience:") or low.startswith("ambient:"):
            tags = txt.split(":", 1)[1].strip(" ,.")
            return (tags + ", continuous background sound only, no music, no melody, no drums, no rhythm") if tags else ""
        if low.startswith("music:"):
            txt = txt.split(":", 1)[1].strip(" ,.")
        return txt
    except Exception as ex:
        if log:
            log(f"music description failed: {str(ex)[:150]}")
        return ""


def _seg_db(x):
    if x.shape[0] == 0:
        return -120.0
    return float(_db(((x.astype(np.float32) / 32768.0) ** 2).mean()))


def _clean_context(pcm, db, g0, g1, ctx_db, band=None, look_sec=None):
    """The clean sound next to the hole: up to CTX_LOOK_SEC before and after it, only 100 ms frames that are at the level of
    the surrounding sound. Returns a list of float32 (n, 2) arrays, one per uninterrupted stretch of at least 0.4 s."""
    hop = int(RATE * HOP_SEC)
    s, e = int(round(g0 / HOP_SEC)), int(round(g1 / HOP_SEC))
    look = int((look_sec or CTX_LOOK_SEC) / HOP_SEC)
    # band given: only sound within `band` dB of ctx_db (the engine itself, not a louder event beside the hole); else everything not far under ctx_db
    good = (np.abs(db - ctx_db) <= band) if band is not None else (db >= ctx_db - CTX_MUSIC_REL_DB)
    out = []
    for a, b in ((max(0, s - look), s), (e, min(len(db), e + look))):
        i = a
        while i < b:
            if not good[i]:
                i += 1
                continue
            j = i
            while j < b and good[j]:
                j += 1
            if (j - i) * HOP_SEC >= 0.4:
                out.append(pcm[i * hop:j * hop].astype(np.float32) / 32768.0)
            i = j
    return out


def _bed_context(pcm, db, g0, g1, bed, only=None):
    """All the clean engine sound of the track (frames within BED_BAND_DB of its usual level, stretches of 0.4 s and more), the
    stretches nearest to the hole first, up to BED_POOL_SEC. The engine is the same everywhere in the track, so the sound far away
    is as good as the sound beside a hole - and in a track where people speak most of the time there is no sound beside it."""
    hop = int(RATE * HOP_SEC)
    good = (np.abs(db - bed) <= BED_BAND_DB)
    if only is not None:
        good = good & only[:len(good)]
    good = good.astype(np.int8)
    edge = np.flatnonzero(np.diff(np.concatenate(([0], good, [0]))))
    runs = [(int(a), int(b)) for a, b in zip(edge[0::2], edge[1::2]) if (b - a) * HOP_SEC >= 0.4]
    mid = (g0 + g1) / 2.0 / HOP_SEC
    runs.sort(key=lambda r: 0.0 if r[0] <= mid < r[1] else min(abs(r[0] - mid), abs(r[1] - mid)))
    out, tot = [], 0.0
    for a, b in runs:
        out.append(pcm[a * hop:b * hop].astype(np.float32) / 32768.0)
        tot += (b - a) * HOP_SEC
        if tot >= BED_POOL_SEC:
            break
    return out


def _bed_level_near(db, g0, g1, bed):
    """The level of the engine around this hole: its usual level, or what it is within BED_LOOK_SEC of the hole if it is
    different there (the plane is nearer, the wind is stronger)."""
    s0, e0 = int(g0 / HOP_SEC), int(g1 / HOP_SEC)
    look = int(BED_LOOK_SEC / HOP_SEC)
    near = np.concatenate([db[max(0, s0 - look):s0], db[e0:e0 + look]])
    near = near[np.abs(near - bed) <= BED_BAND_DB]
    return float(np.median(near)) if near.size * HOP_SEC >= 0.5 else float(bed)


def _bed_db(db, spans=None):
    """The usual level (dB) of the quiet, steady sound of the track (an engine, wind, room tone): the most common level of the
    audible frames outside the speech (2 dB bins). A loud event (a plane passing, a bang) is rare, so it does not move it.
    None when there is not enough audible sound to say."""
    n = len(db)
    ok = db >= REF_FLOOR_DB
    if spans:
        for a, b in spans:
            ok[max(0, int((a - 0.3) / HOP_SEC)):min(n, int((b + 0.3) / HOP_SEC) + 1)] = False
    vals = db[ok]
    if vals.size * HOP_SEC < STEADY_MIN_SEC:       # no real pauses: the sound under the speech is not evidence (the voice leaks into it)
        return None
    lo = float(np.floor(vals.min()))
    hist, edges = np.histogram(vals, bins=np.arange(lo, float(vals.max()) + 2.0, 2.0))
    if hist.size == 0:
        return None
    smooth = np.convolve(hist, np.ones(3), mode="same")          # a level and its two neighbours: the densest 6 dB
    k = int(np.argmax(smooth))
    sel = vals[(vals >= edges[max(0, k - 1)]) & (vals < edges[min(len(edges) - 1, k + 2)])]
    return float(np.median(sel)) if sel.size else float(edges[k] + 1.0)


def _steadiness_db(segs):
    """How much the sound changes over time: the median over 8 frequency bands of the standard deviation (dB) of the band level,
    averaged over ~0.25 s. About 0.5-2 for an engine, wind or room tone; 3 and more for music with a beat or changing notes.
    None when there is too little sound to judge."""
    n_fft, hop = 2048, 1024
    win = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / RATE)
    edges = np.geomspace(100.0, 10000.0, 9)
    scores = []
    total = 0.0
    for seg in segs:
        mono = seg.mean(axis=1)
        if len(mono) < n_fft * 3:
            continue
        frames = 1 + (len(mono) - n_fft) // hop
        idx = np.arange(n_fft)[None, :] + hop * np.arange(frames)[:, None]
        power = np.abs(np.fft.rfft(mono[idx] * win, axis=1)) ** 2
        bands = np.stack([power[:, (freqs >= lo) & (freqs < hi)].sum(axis=1) for lo, hi in zip(edges[:-1], edges[1:])], axis=1)
        k = 10                                           # ~0.23 s
        if bands.shape[0] < k + 8:
            continue
        kernel = np.ones(k) / k
        smooth = np.stack([np.convolve(bands[:, b], kernel, mode="valid") for b in range(bands.shape[1])], axis=1)
        level = _db(smooth)
        scores.append((float(np.median(level.std(axis=0))), len(mono) / RATE))
        total += len(mono) / RATE
    if total < STEADY_MIN_SEC or not scores:
        return None
    return float(sum(sc * w for sc, w in scores) / sum(w for _, w in scores))


CROWD_MOD = float(os.environ.get("MUSIC_FILL_CROWD_MOD", "0.18") or 0.18)     # a crowd, a restaurant, a street: the level wobbles with the syllables (1-12 Hz) ...
CROWD_IQR_DB = float(os.environ.get("MUSIC_FILL_CROWD_IQR_DB", "2.0") or 2.0)  # ... and the typical level is not constant. A machine hum / an engine / white wind are far under both
TEXTURE_EVENT_DB = 10.0                                                   # frames this far above the usual level are an event, not the texture
TEXTURE_MIN_SEC = 3.0
TEXTURE_MAX_SEC = 60.0


def _pause_audio(src, spans, pad=0.4, min_len=0.5, trim=0.05, timed=False):
    """The sound of the pauses between the speakers: float32 (n, 2) stretches of at least min_len seconds, the longest first, up to
    TEXTURE_MAX_SEC. `src` is an int16 (n, 2) array at RATE. timed=True: a list of (start second, stretch) instead."""
    n = src.shape[0]
    free = np.ones(n // 441 + 2, dtype=bool)
    for a, b in spans or []:
        free[max(0, int((a - pad) * 100)):int((b + pad) * 100) + 1] = False
    edge = np.flatnonzero(np.diff(np.concatenate(([0], free.astype(np.int8), [0]))))
    runs = [(int(a) + int(trim * 100), int(b) - int(trim * 100)) for a, b in zip(edge[0::2], edge[1::2])]
    runs = [(a, b) for a, b in runs if (b - a) / 100.0 >= min_len]
    runs.sort(key=lambda r: r[0] - r[1])
    out, tot = [], 0.0
    for a, b in runs:
        if tot >= TEXTURE_MAX_SEC:
            break
        seg = src[a * 441:min(n, b * 441)].astype(np.float32) / 32768.0
        out.append((a / 100.0, seg) if timed else seg)
        tot += len(seg) / RATE
    return out


MATCH_MAX_DB = float(os.environ.get("MUSIC_FILL_MATCH_DB", "6") or 6)     # a piece of real sound is laid into a hole only if its spectrum is this close (mean dB over the bands) to the real sound beside the hole
MATCH_LOOK_SEC = float(os.environ.get("MUSIC_FILL_MATCH_LOOK_SEC", "25") or 25)    # the real sound beside a hole is taken from the pauses this near to it
MATCH_CHUNK_SEC = 1.5
MATCH_NEAR_CHUNKS = 4
_MATCH_EDGES = np.geomspace(100.0, 12000.0, 21)


def _band_shape(x):
    """The spectral shape of a sound: its mean level in 20 bands (100 Hz - 12 kHz) minus their average, so the loudness does not
    matter, only what the sound is made of (a murmur, a hum, a chord). None when the sound is too short."""
    mono = x.mean(axis=1) if x.ndim == 2 else x
    n = 4096
    if len(mono) < n:
        return None
    win = np.hanning(n).astype(np.float32)
    acc, cnt = None, 0
    for k in range(0, len(mono) - n + 1, n // 2):
        pw = np.abs(np.fft.rfft(mono[k:k + n] * win)) ** 2
        acc = pw if acc is None else acc + pw
        cnt += 1
    pw = acc / cnt
    f = np.fft.rfftfreq(n, 1.0 / RATE)
    out = np.array([10.0 * np.log10(pw[(f >= a) & (f < b)].mean() + 1e-14) for a, b in zip(_MATCH_EDGES[:-1], _MATCH_EDGES[1:])])
    return out - out.mean()


TONAL_MIN = int(float(os.environ.get("MUSIC_FILL_TONAL_MIN", "3") or 3))    # this many lasting musical tones in a piece: it is music (or a hum), not a murmur


def tonal_lines(x):
    """How many steady tones (a lasting line in the spectrum, 100-4000 Hz: a note, a chord, a hum) the sound holds. A murmur, a crowd, room
    noise, clatter and speech have none that last; music has many. x: float32 (n,) or (n, 2); 0 when it is shorter than 0.8 s."""
    try:
        from scipy.ndimage import median_filter
        from scipy.signal import stft
        mono = x.mean(axis=1) if x.ndim == 2 else x
        if len(mono) < int(0.8 * RATE):
            return 0
        f, _, z = stft(mono, RATE, nperseg=8192, noverlap=6144)
        sel = (f >= 100) & (f < 4000)
        lev = 20.0 * np.log10(np.abs(z[sel]) + 1e-9)
        peak = (lev - median_filter(lev, size=(31, 1), mode="nearest")) > 8.0
        return int((peak.mean(axis=1) >= 0.7).sum())
    except Exception:
        return 0


def _matching_pieces(timed, g0, g1, near=None):
    """The real sound that may be laid into the hole g0..g1 (seconds): only pieces of the pauses that sound like the real sound
    right beside it. Whatever else the pauses hold (the closing music of a scene, a laugh, a bang) is not what was in the hole, so it is
    never used: the hole stays as it is rather than getting something that does not belong.
    timed = _pause_audio(..., timed=True); near = clean sound beside the hole in the track itself (list of arrays), used when no pause is
    near enough. Returns (list of float32 (n, 2) stretches, note)."""
    chunks = []
    for t0, seg in timed:
        k = int(MATCH_CHUNK_SEC * RATE)
        pos = 0
        while pos < len(seg):
            end = pos + k
            if len(seg) - end < k // 2:
                end = len(seg)
            part = seg[pos:end]
            sh = _band_shape(part) if len(part) >= 4096 else None
            if sh is not None:
                chunks.append((t0 + pos / RATE, t0 + end / RATE, pos, end, seg, sh, tonal_lines(part) >= TONAL_MIN))
            pos = end
    if not chunks:
        return [], "no real sound of this scene to take pieces from"
    mid = (g0 + g1) / 2.0
    close = [c for c in chunks if min(abs(c[1] - g0), abs(c[0] - g1), abs((c[0] + c[1]) / 2 - mid)) <= MATCH_LOOK_SEC]
    close.sort(key=lambda c: min(abs(c[1] - g0), abs(c[0] - g1)))
    ref, ref_music = None, False
    if close:
        ref = np.median(np.stack([c[5] for c in close[:MATCH_NEAR_CHUNKS]]), axis=0)
        ref_music = sum(1 for c in close[:MATCH_NEAR_CHUNKS] if c[6]) * 2 > len(close[:MATCH_NEAR_CHUNKS])
    elif near:
        shapes = [sh for sh in (_band_shape(x) for x in near if len(x) >= 4096) if sh is not None]
        if shapes:
            ref = np.median(np.stack(shapes), axis=0)
            tones = [tonal_lines(x) >= TONAL_MIN for x in near if len(x) >= int(0.8 * RATE)]
            ref_music = bool(tones) and sum(tones) * 2 > len(tones)
    if ref is None:
        return [], "no real sound beside the hole to compare the pieces with"
    # what is beside the hole decides what it is: music (lasting tones) only gets music, anything else only gets sound that is not music
    keep = [c for c in chunks if c[6] == ref_music and float(np.mean(np.abs(c[5] - ref))) <= MATCH_MAX_DB]
    if not keep:
        return [], "none of the real sound sounds like what is beside the hole" + (" (it is music there)" if ref_music else " (the pauses hold music, which was not there)")
    keep.sort(key=lambda c: (id(c[4]), c[2]))
    pieces, cur, last = [], [], None
    for t_a, t_b, a, b, seg, _sh, _mu in keep:       # neighbouring chunks of one stretch become one piece again
        if last is not None and last[0] is seg and last[1] == a:
            cur.append(seg[a:b])
        else:
            if cur:
                pieces.append(np.concatenate(cur))
            cur = [seg[a:b]]
        last = (seg, b)
    if cur:
        pieces.append(np.concatenate(cur))
    return pieces, f"{len(keep)} of {len(chunks)} pieces of real sound sound like the hole's surroundings"


def texture_segs(segs):
    """Is this sound a steady machine-like one (an engine, a hum, wind, a room tone) that can be made up again without anybody hearing
    it, or has it a life of its own (a crowd, a restaurant, a street: murmur, clatter, voices) that a made-up sound can only spoil?
    The level (300-6000 Hz, 20 ms) of a crowd wobbles 0.2-0.3 with the rhythm of speech (1-12 Hz) and its typical level varies by 4-5 dB;
    a machine hum is at 0.05 and under 1 dB, steady wind 0.1. Returns {"ok": True | False | None (too little sound to say), "mod", "iqr_db", "sec"}."""
    out = {"ok": None, "mod": None, "iqr_db": None, "sec": 0.0}
    try:
        from scipy.signal import butter, sosfilt, stft
        sos = butter(2, [1, 12], btype="band", fs=100, output="sos")
        levels, mods = [], []
        total = 0.0
        for seg in segs:
            mono = seg.mean(axis=1) if seg.ndim == 2 else seg
            if len(mono) < int(0.5 * RATE):
                continue
            f, _, z = stft(mono, RATE, nperseg=882, noverlap=441)
            lv = 10.0 * np.log10((np.abs(z) ** 2)[(f >= 300) & (f < 6000)].sum(axis=0) + 1e-14)
            levels.append(lv)
            total += len(mono) / RATE
        out["sec"] = round(total, 1)
        if total < TEXTURE_MIN_SEC or not levels:
            return out
        # A loud event (a plane, a bang, a shout) is not the texture of the scene: what is far above the usual level is set aside
        # (it would make a steady engine look like a crowd).
        usual = float(np.median(np.concatenate(levels)))
        kept = []
        for lv in levels:
            lv = np.where(lv > usual + TEXTURE_EVENT_DB, usual, lv)
            kept.append(lv)
            if len(lv) > 60:
                x = 10.0 ** (lv / 20.0)
                x = x / (float(x.mean()) + 1e-12)
                mods.append((float(np.std(sosfilt(sos, x))), len(lv)))
        if not mods:
            return out
        lv = np.concatenate(kept)
        q25, q75 = np.percentile(lv, [25, 75])
        mod = sum(m * w for m, w in mods) / sum(w for _, w in mods)
        out["mod"], out["iqr_db"] = round(float(mod), 3), round(float(q75 - q25), 2)
        out["ok"] = not (mod > CROWD_MOD and (q75 - q25) > CROWD_IQR_DB)
        return out
    except Exception:
        return out


def _synth_sound(pool, need, salt=0):
    """A new stretch of `need` samples with the same sound as `pool` (clean, steady sound): the average spectrum of the pool
    (fine resolution, so a hum stays a hum) is applied to fresh random noise, with the slow level flutter of the pool.
    Nothing of the pool is repeated, so a short pool does not turn into an audible loop."""
    n_fft, hop = SYNTH_FFT, SYNTH_FFT // 2
    win = np.hanning(n_fft).astype(np.float32)
    all_power = []
    for seg in pool:
        for s in range(0, len(seg) - n_fft + 1, hop):
            chunk = seg[s:s + n_fft]
            all_power.append(np.abs(np.fft.rfft(chunk.T * win, axis=1)) ** 2)
    frames = len(all_power)
    if not frames:
        return None
    all_power = np.stack(all_power)
    # A short blip in the pool (a beep, a click, a bang) would become a tone that never stops. The median over the frames ignores
    # what is present in only a few of them; for noise it is 0.69 x the mean (hence / ln 2), for a steady tone the mean is smaller.
    power = np.minimum(all_power.mean(axis=0), np.median(all_power, axis=0) / np.log(2.0)).astype(np.float64)
    mono_all = np.concatenate([x.mean(axis=1) for x in pool])
    rho = 0.0
    if all(len(x) > 1 for x in pool) and CH == 2:
        a = np.concatenate([x[:, 0] for x in pool]); b = np.concatenate([x[:, 1] for x in pool])
        c = np.corrcoef(a, b)[0, 1]
        rho = float(np.clip(c if np.isfinite(c) else 0.0, 0.0, 0.98))
    rng = np.random.default_rng((int(np.abs(mono_all[:64]).sum() * 1e6) + 7919 * salt) % (2 ** 32))
    size = 1 << int(np.ceil(np.log2(need + n_fft)))
    common = rng.standard_normal(size).astype(np.float32)
    out = np.zeros((need, CH), dtype=np.float32)
    for ch in range(CH):
        own = rng.standard_normal(size).astype(np.float32)
        white = np.sqrt(rho) * common + np.sqrt(1.0 - rho) * own
        amp = np.sqrt(power[ch] / (np.sum(win ** 2) + 1e-9))
        h = np.fft.irfft(amp, n_fft)                         # zero-phase filter of the pool's spectrum
        h = np.roll(h, n_fft // 2) * np.hanning(n_fft)
        y = np.fft.irfft(np.fft.rfft(white) * np.fft.rfft(h, size), size)[n_fft // 2:n_fft // 2 + need]
        out[:, ch] = y
    # slow level flutter (an engine is never perfectly flat): same size as in the real sound, at most SYNTH_MOD_MAX_DB
    h100 = int(0.1 * RATE)
    levels = []
    for seg in pool:
        m = seg.mean(axis=1)
        for s in range(0, len(m) - h100 + 1, h100):
            levels.append(_db(float((m[s:s + h100] ** 2).mean())))
    if len(levels) >= 6:
        flut = float(min(np.std(levels), SYNTH_MOD_MAX_DB))
        z = np.convolve(rng.standard_normal(need + int(RATE)), np.ones(int(0.4 * RATE)) / (0.4 * RATE), "same")[:need]
        z = z / (z.std() + 1e-9)
        out *= (10.0 ** (flut * z / 20.0))[:, None].astype(np.float32)
    ref_rms = float(np.sqrt(np.mean(np.concatenate(pool) ** 2)))
    got_rms = float(np.sqrt(np.mean(out ** 2)))
    if got_rms > 0 and ref_rms > 0:
        out *= ref_rms / got_rms
    return out


def _synth_long(pool, need):
    """A long stretch (minutes) made piece by piece of SYNTH_CHUNK_SEC, so memory stays small; the pieces are blended with an
    equal-power cross-fade and each has its own noise (no repetition)."""
    over = int(0.5 * RATE)
    step = int(SYNTH_CHUNK_SEC * RATE)
    out = np.zeros((need, CH), dtype=np.float32)
    pos, k = 0, 0
    fin = np.sin(np.linspace(0.0, np.pi / 2, over, dtype=np.float32))[:, None]
    fout = np.cos(np.linspace(0.0, np.pi / 2, over, dtype=np.float32))[:, None]
    while pos < need:
        n_piece = min(step + over, need - pos)
        piece = _synth_sound(pool, n_piece, salt=k)
        if piece is None:
            return None
        if pos == 0:
            out[:n_piece] = piece
        else:
            m = min(over, n_piece)
            out[pos:pos + m] = out[pos:pos + m] * fout[:m] + piece[:m] * fin[:m]
            out[pos + m:pos + n_piece] = piece[m:]
        pos += step
        k += 1
    return out


def _texture_fill(pcm, g0, g1, ctx_db, segs, allow_synth=True):
    """Fills the hole g0..g1 (seconds) from the clean sound beside it, matched to the level around the hole. Only for steady
    sounds. With plenty of clean sound, overlapping pieces of it are laid one after the other at random places; with little of
    it (a loop would be heard) a new stretch of the same sound is made from its spectrum instead."""
    pool = [x for x in segs if len(x) >= int(0.5 * RATE)]
    have = sum(len(x) for x in pool) / RATE
    if have < 0.6:
        return False, "not enough steady sound next to the hole"
    need = int(round((g1 - g0) * RATE)) + 2 * int(SEAM_SEC * RATE)
    use_synth = LOCAL_MODE == "synth" or (LOCAL_MODE != "grain" and need / RATE > have * GRAIN_MAX_HOLE_RATIO)
    if not allow_synth:
        # a crowd, a restaurant, a street: nothing is made up for it; only real pieces of its own sound, each used once
        if need / RATE > have * GRAIN_MAX_HOLE_RATIO:
            return False, "not enough real sound of this scene to fill the hole without making any up"
        use_synth = False
    if use_synth:
        spool = [x for x in segs if len(x) >= SYNTH_FFT * 2] or pool
        synth = _synth_sound(spool, need) if need <= SYNTH_CHUNK_SEC * RATE * 1.5 else _synth_long(spool, need)
        if synth is not None:
            return _place_fill(pcm, g0, g1, ctx_db, synth, "made new from its sound")
    grain = int(min(GRAIN_SEC, max(0.5, have / 2.0)) * RATE)
    fade = grain // 3
    rng = np.random.default_rng(int(g0 * 1000) % (2 ** 32))
    out = np.zeros((need + 2 * grain, CH), dtype=np.float32)
    pos, last = 0, None
    used = []                      # (segment, start, end) already laid down: new pieces avoid them so nothing is heard twice
    ramp = np.linspace(0.0, np.pi / 2, fade, dtype=np.float32)
    fin, fout = np.sin(ramp)[:, None], np.cos(ramp)[:, None]
    while pos < need:
        k = int(rng.integers(len(pool)))
        seg = pool[k]
        g = min(grain, len(seg))
        if g <= fade + 8:
            k = int(np.argmax([len(x) for x in pool]))
            seg, g = pool[k], min(grain, len(pool[k]))
        st = int(rng.integers(0, len(seg) - g + 1))
        for _try in range(30):
            clash = any(u[0] == k and min(st + g, u[2]) - max(st, u[1]) > 0.15 * g for u in used)
            if not clash:
                break
            st = int(rng.integers(0, len(seg) - g + 1))
        used.append((k, st, st + g))
        if last is not None and last == (k, st // (RATE // 2)) and len(seg) - g > RATE // 2:
            st = (st + RATE // 2) % (len(seg) - g + 1)
        last = (k, st // (RATE // 2))
        chunk = seg[st:st + g]
        if pos == 0:
            out[:g] = chunk
        else:
            out[pos:pos + fade] = out[pos:pos + fade] * fout + chunk[:fade] * fin
            out[pos + fade:pos + g] = chunk[fade:]
        pos += g - fade
    return _place_fill(pcm, g0, g1, ctx_db, out[:need], "from the sound beside it")


def _place_fill(pcm, g0, g1, ctx_db, fillv, how):
    got_db = _seg_db((fillv * 32768.0))
    gain_db = float(np.clip(ctx_db - got_db, -GAIN_MAX_DB, GAIN_MAX_DB)) if np.isfinite(got_db) else 0.0
    gain_db += LOCAL_FILL_DB
    fillv = fillv * 10 ** (gain_db / 20.0) * 32768.0
    seam = int(SEAM_SEC * RATE)
    a, b = int(round(g0 * RATE)) - seam, int(round(g1 * RATE)) + seam
    if a < 0:
        fillv, a = fillv[-a:], 0
    b = min(b, a + fillv.shape[0], pcm.shape[0])
    fillv = fillv[:b - a]
    f = max(1, min(2 * seam, fillv.shape[0] // 3))
    # equal-power blend over the seams: the real sound beside the hole fades out as the rebuilt sound fades in (two unrelated
    # noises added with weights 1-w and w would dip by 3 dB in the middle of the seam)
    ramp = np.linspace(0.0, np.pi / 2, f, dtype=np.float32)
    w_fill = np.ones(fillv.shape[0], dtype=np.float32)
    w_cur = np.zeros(fillv.shape[0], dtype=np.float32)
    w_fill[:f], w_cur[:f] = np.sin(ramp), np.cos(ramp)
    w_fill[-f:], w_cur[-f:] = np.sin(ramp[::-1]), np.cos(ramp[::-1])
    cur = pcm[a:a + fillv.shape[0]].astype(np.float32)
    mixed = cur * w_cur[:, None] + fillv * w_fill[:, None]
    pcm[a:a + fillv.shape[0]] = np.clip(np.rint(mixed), -32768, 32767).astype(np.int16)
    return True, f"steady sound: filled {g1 - g0:.1f} s {how} (set to {got_db + gain_db:.0f} dB)"


def _fill_one(pcm, g0, g1, ctx_db, key, prompt, runner, log):
    """Fills the hole g0..g1 (seconds) in the s16 array `pcm` in place. Returns (ok, reason)."""
    total = pcm.shape[0] / RATE
    need = max(MIN_WINDOW_SEC, (g1 - g0) + 2 * CONTEXT_SEC)
    need = min(need, MAX_WINDOW_SEC, total)
    w0 = max(0.0, g0 - (need - (g1 - g0)) / 2.0)
    w1 = min(total, w0 + need)
    w0 = max(0.0, w1 - need)
    i0, i1 = int(w0 * RATE), int(w1 * RATE)
    win = np.array(pcm[i0:i1])
    # Concurrent jobs can repair the same timestamp; each request needs its own sample.
    with tempfile.NamedTemporaryFile(prefix="lisan_music_", suffix=".wav", delete=False) as sample:
        wav = Path(sample.name)
    try:
        _write_wav(wav, win)
        data = runner(key, wav, g0 - w0, g1 - w0, prompt)
    finally:
        try:
            wav.unlink()
        except Exception:
            pass
    gen = _decode_audio(data)
    if gen.shape[0] < win.shape[0] * 0.9:
        return False, f"the model's answer is too short ({gen.shape[0] / RATE:.1f} s for {win.shape[0] / RATE:.1f} s)"
    gen = gen[:win.shape[0]]
    a, b = int((g0 - w0) * RATE), int((g1 - w0) * RATE)
    seg = gen[a:b].astype(np.float32)
    clipped_share = float(np.mean((seg <= -32768) | (seg >= 32767))) if seg.size else 0.0
    if clipped_share >= MAX_CLIPPED_SHARE:
        return False, f"the generated music is severely clipped ({clipped_share:.0%} of samples at full scale)"
    gen_db = _seg_db(seg)
    if gen_db < GEN_MIN_DB:
        return False, f"the model made silence ({gen_db:.0f} dB)"
    target = ctx_db - TARGET_BELOW_CTX_DB
    gain_db = target - gen_db
    if not np.isfinite(gain_db) or gain_db > GAIN_MAX_DB:
        return False, f"the result is {gen_db:.0f} dB against music of {ctx_db:.0f} dB around it (needs {gain_db:+.0f} dB)"
    seg *= 10 ** (gain_db / 20.0)
    f = int(XFADE_SEC * RATE)
    f = max(1, min(f, seg.shape[0] // 3))
    w = np.ones(seg.shape[0], dtype=np.float32)
    w[:f] = np.linspace(0.0, 1.0, f)
    w[-f:] = np.linspace(1.0, 0.0, f)
    cur = pcm[i0 + a:i0 + b].astype(np.float32)
    mixed = cur * (1.0 - w[:, None]) + seg * w[:, None]
    # the seams: the filled stretch must not jump against the music right beside it
    left = _seg_db(pcm[max(0, i0 + a - RATE):i0 + a])
    right = _seg_db(pcm[i0 + b:i0 + b + RATE])
    inner_l = _seg_db(mixed[f:f + RATE // 2]) if mixed.shape[0] > f + 100 else gen_db
    inner_r = _seg_db(mixed[-f - RATE // 2:-f]) if mixed.shape[0] > f + 100 else gen_db
    for side_db, in_db, nm in ((left, inner_l, "start"), (right, inner_r, "end")):
        if side_db > REF_FLOOR_DB and abs(in_db - side_db) > SEAM_MAX_DB:
            return False, f"the {nm} of the filled part is {in_db:.0f} dB next to music of {side_db:.0f} dB"
    pcm[i0 + a:i0 + b] = np.clip(np.rint(mixed), -32768, 32767).astype(np.int16)
    return True, f"filled {g1 - g0:.1f} s (generated {gen_db:.0f} dB, set to {target:.0f} dB)"


def steady_engine(muted_path, reference_path, spans):
    """Can the holes of this track be rebuilt locally from a steady sound (engine, wind, room tone ...) found in the pauses of the
    reference (the same background before the voices were silenced)? Returns {"ok", "bed_db", "steadiness_db", "pool_sec"}; never raises."""
    out = {"ok": False, "bed_db": None, "steadiness_db": None, "pool_sec": 0.0}
    ref_pcm = None
    raw = Path(str(muted_path) + ".steady.pcm")
    try:
        if not (LOCAL_FILL and ENABLED and spans and reference_path):
            return out
        _to_pcm(reference_path, raw)
        n = raw.stat().st_size // (2 * CH)
        if n < RATE * 3 or n > RATE * MAX_FILE_SEC:
            return out
        ref_pcm = np.memmap(raw, dtype="<i2", mode="r", shape=(n, CH))
        ref_db = _levels(ref_pcm)
        pause = np.ones(len(ref_db), dtype=bool)
        for a, b in spans:
            pause[max(0, int((a - 0.15) / HOP_SEC)):min(len(pause), int((b + 0.15) / HOP_SEC) + 1)] = False
        bed = _bed_db(ref_db, spans)
        if bed is None:
            return out
        out["bed_db"] = round(bed, 1)
        mid = (n / RATE) / 2.0
        segs = _bed_context(ref_pcm, ref_db, mid, mid, bed, only=pause)
        out["pool_sec"] = round(sum(len(s) for s in segs) / RATE, 1)
        score = _steadiness_db(segs)
        if score is not None:
            out["steadiness_db"] = round(score, 1)
            out["ok"] = score <= STEADY_MAX_DB
        return out
    except Exception:
        return out
    finally:
        if ref_pcm is not None:
            try:
                ref_pcm._mmap.close()
            except Exception:
                pass
        try:
            raw.unlink()
        except Exception:
            pass


def fill(bg_path, out_path, spans, key, gemini_key=None, prompt=None, work_dir=None, runner=None, log=None,
         allow=None, on_filled=None, progress=None, reference=None, reference_pad=0.15):
    """Fills the holes in the background track `bg_path` (the one that voices were silenced in) and writes the result to
    `out_path`. spans = [(start_sec, end_sec)] where the original voices speak. Returns
    {"filled": bool, "reason", "gaps": [{start, end, ok, note}], "filled_sec", "sent_sec", "prompt"}; never raises.
    When nothing was filled, `out_path` is not written.
    allow() -> bool is asked before every call (False = stop calling the model, for example when the user cannot pay);
    on_filled(start_sec, end_sec) is called after each hole that was really filled (the place to charge for it);
    progress(done, total) is called before each hole is attempted (for a progress bar; errors in it are ignored).
    reference = the same background BEFORE the voices were silenced (optional): the sound of a steady engine, wind or room tone
    is learned from it - its pauses between the speakers, which are real, untouched sound of the whole track - and not only
    from the little that is left in `bg_path`."""
    info = {"filled": False, "reason": "", "gaps": [], "filled_sec": 0.0, "sent_sec": 0.0, "prompt": "", "found": 0}
    runner = runner or _fal_run
    say = log or (lambda m: None)
    raw = Path(str(out_path) + ".mf.pcm")
    ref_raw = Path(str(out_path) + ".mf.ref.pcm")
    pcm = None
    ref_pcm = None
    try:
        if not ENABLED:
            info["reason"] = "off (MUSIC_FILL=0)"
            return info
        if not key:
            info["reason"] = "no fal.ai key set"
            return info
        if not spans:
            info["reason"] = "no speech map"
            return info
        _to_pcm(bg_path, raw)
        n = raw.stat().st_size // (2 * CH)
        if n < RATE * 3:
            info["reason"] = "track too short"
            return info
        if n > RATE * MAX_FILE_SEC:
            info["reason"] = "track too long for music repair"
            return info
        pcm = np.memmap(raw, dtype="<i2", mode="r+", shape=(n, CH))
        db = _levels(pcm)
        ref_pcm = ref_db = ref_pause = None
        if reference and LOCAL_FILL:
            try:
                _to_pcm(reference, ref_raw)
                rn = ref_raw.stat().st_size // (2 * CH)
                if abs(rn - n) <= RATE and rn > RATE * 3:
                    ref_pcm = np.memmap(ref_raw, dtype="<i2", mode="r", shape=(rn, CH))
                    ref_db = _levels(ref_pcm)
                    ref_pause = np.ones(len(ref_db), dtype=bool)          # the pauses between the speakers: real, untouched sound
                    for a, b in spans:
                        ref_pause[max(0, int((a - reference_pad) / HOP_SEC)):min(len(ref_pause), int((b + reference_pad) / HOP_SEC) + 1)] = False
            except Exception as ex:
                say(f"reference background not usable: {str(ex)[:120]}")
                if ref_pcm is not None:
                    ref_pcm._mmap.close()
                ref_pcm = ref_db = ref_pause = None
        ref_level = np.where(ref_pause, ref_db, -120.0) if ref_pcm is not None else None     # levels of the pauses before muting
        gaps, gi = find_gaps(db, spans, min_len=LOCAL_MIN_GAP_SEC if LOCAL_FILL else None,
                             keep_unfillable=LOCAL_FILL, level_db=ref_level)
        info["found"] = gi["found"]
        if not gaps:
            if gi["music_sec"] < MIN_MUSIC_SEC:
                info["reason"] = f"no music to continue (only {gi['music_sec']} s of music in the track)"
            elif gi["found"] == 0:
                info["reason"] = "no holes in the music"
            else:
                info["reason"] = (f"{gi['found']} hole(s) found, none usable ({gi['too_long']} too long, "
                                  f"{gi['no_context']} without music next to them)")
            return info
        if len(gaps) > MAX_GAPS:
            keep = sorted(sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)[:MAX_GAPS])
            info["reason_cap"] = f"{len(gaps)} holes, filling the {MAX_GAPS} longest"
            gaps = keep
        prompt_box = {"text": prompt or ""}

        def get_prompt():
            # Asked only when a hole really needs the model; a steady sound is filled locally and needs no description.
            if not prompt_box["text"]:
                desc = describe_music(gemini_key, pcm, db, log=say) if gemini_key else ""
                prompt_box["text"] = (desc + (", no vocals, no speech" if "no music" in desc else ", instrumental, no vocals, no speech")) if desc else DEFAULT_PROMPT
            info["prompt"] = prompt_box["text"]
            return prompt_box["text"]

        audible = db[db >= REF_FLOOR_DB]
        ctx_ref = float(np.median(audible)) if audible.size else float(gi.get("ref_db") or -40.0)
        budget = MAX_TOTAL_SEC
        ok_n = 0
        t_start = time.time()
        todo = sorted(gaps)
        local_done = []
        short_set = set(gi.get("short_gaps", []))
        local_only_set = set(gi.get("local_only", []))

        def level_around(g0, g1, levels):
            hop = int(1 / HOP_SEC)
            s0, e0 = int(g0 / HOP_SEC), int(g1 / HOP_SEC)
            near = np.concatenate([levels[max(0, s0 - 6 * hop):s0], levels[e0:e0 + 6 * hop]])
            near = near[near >= ctx_ref - CTX_MUSIC_REL_DB]
            return float(np.median(near)) if near.size else ctx_ref

        # Judge every hole's surroundings before anything is filled, so a filled hole can never count as evidence.
        # The engine itself is what counts: sound near the usual quiet level of the track. A loud event beside the hole (a plane
        # passing) neither makes the engine look unsteady nor sets the level the hole is filled to.
        steady_of, level_of = {}, {}
        real_pool, real_timed, tex = [], [], {"ok": None}
        if LOCAL_FILL:
            try:
                real_timed = _pause_audio(ref_pcm if ref_pcm is not None else pcm, spans, timed=True)
                real_pool = [x for _, x in real_timed]
                tex = texture_segs(real_pool)
            except Exception as ex:
                say(f"texture of the pauses not judged: {str(ex)[:120]}")
        synth_ok = tex.get("ok") is not False          # False = a crowd / restaurant / street: no sound is made up for it
        info["texture"] = tex
        bed = _bed_db(ref_db if ref_pcm is not None else db, spans) if LOCAL_FILL else None
        level_db = np.where(ref_pause, ref_db, -120.0) if ref_pcm is not None else db     # the level the pauses (real sound) have

        def engine_pool(g0, g1):
            """The clean engine sound a hole is rebuilt from: the pauses of the whole track (with the reference), else what is left."""
            if bed is None:
                return []
            if ref_pcm is not None:
                return _bed_context(ref_pcm, ref_db, g0, g1, bed, only=ref_pause)     # the pauses only: real sound, no leaked voice
            return _bed_context(pcm, db, g0, g1, bed)

        if LOCAL_FILL:
            for g0, g1 in todo:
                try:
                    score = None
                    if bed is not None:
                        score = _steadiness_db(engine_pool(g0, g1))
                        if score is not None:
                            level_of[(g0, g1)] = _bed_level_near(level_db, g0, g1, bed)
                    if score is None:
                        score = _steadiness_db(_clean_context(pcm, db, g0, g1, level_around(g0, g1, db)))
                    steady_of[(g0, g1)] = score
                except Exception:
                    steady_of[(g0, g1)] = None
        for n_seen, (g0, g1) in enumerate(todo):
            if progress is not None:
                try:
                    progress(n_seen, len(todo))
                except Exception:
                    pass
            ln = g1 - g0
            short = (g0, g1) in short_set        # too short for the model: only the free local rebuild can fill it, and it is optional
            rec = {"start": round(float(g0), 1), "end": round(float(g1), 1), "ok": False, "note": ""}
            if short:
                rec["optional"] = True
            info["gaps"].append(rec)
            if ln > budget:
                rec["note"] = "over the per-job limit"
                continue
            if time.time() - t_start > MAX_WALL_SEC:
                rec["note"] = "out of time"
                continue
            # level of the sound right around this hole
            ctx_db = level_around(g0, g1, db)
            score = steady_of.get((g0, g1))
            local = False
            ok, note = False, ""
            if score is not None and score <= STEADY_MAX_DB:
                rec["steadiness_db"] = round(score, 1)
                try:
                    if not synth_ok:
                        # the scene has a life of its own (people, dishes, traffic): only real pieces of it are used, never a made-up sound
                        pieces, why = _matching_pieces(real_timed, g0, g1, _clean_context(pcm, db, g0, g1, ctx_db))
                        if pieces:
                            ok, note = _texture_fill(pcm, g0, g1, level_of.get((g0, g1), ctx_db), pieces, allow_synth=False)
                        else:
                            ok, note = False, why
                    elif (g0, g1) in level_of:      # the engine itself, at its usual level: not a louder event next to the hole
                        ok, note = _texture_fill(pcm, g0, g1, level_of[(g0, g1)],
                                                 engine_pool(g0, g1))
                    else:
                        ok, note = _texture_fill(pcm, g0, g1, ctx_db, _clean_context(pcm, db, g0, g1, ctx_db))
                except Exception as ex:
                    ok, note = False, f"local fill failed: {str(ex)[:160]}"
                local = ok
                if not ok and not synth_ok:
                    rec["note"] = note + " (the hole stays as it is; the music model is not asked for a crowd scene)"
                    continue
                if not ok:
                    say(f"hole {g0:.1f}-{g1:.1f} s: {note}; asking the model instead")
            if not local and short:
                rec["note"] = "too short for the model and no steady sound to rebuild it from"
                continue
            if not local and (g0, g1) in local_only_set:
                rec["note"] = "too long for the model (or no music next to it) and no steady sound to rebuild it from"
                continue
            if not local and not AMBIENCE_MODEL and "no music" in get_prompt().lower():
                # The sound is not music (engine, wind, crowd ...). A music model invents a different one in every hole, so it is
                # not asked: the hole stays silent rather than getting a sound that does not belong.
                rec["note"] = "not music and no steady clean sound to rebuild it from; the model is not used for that"
                continue
            if not local:
                if allow is not None:
                    try:
                        permitted = bool(allow())
                    except Exception:
                        permitted = False
                    if not permitted:
                        rec["note"] = "not enough credits to repair this one"
                        continue
                try:
                    ok, note = _fill_one(pcm, g0, g1, ctx_db, key, get_prompt(), runner, say)
                except Exception as ex:
                    ok, note = False, f"call failed: {str(ex)[:160]}"
                info["sent_sec"] = round(info["sent_sec"] + min(MAX_WINDOW_SEC, max(MIN_WINDOW_SEC, ln + 2 * CONTEXT_SEC)), 1)
            rec["ok"], rec["note"], rec["local"] = ok, note, local
            if ok:
                ok_n += 1
                info["local_filled_sec"] = round(float(info.get("local_filled_sec", 0.0) + (ln if local else 0.0)), 1)
                if on_filled is not None and not local:     # a steady sound is rebuilt locally: it costs nothing, so it is not charged
                    try:
                        on_filled(g0, g1)
                    except Exception as ex:
                        raise RuntimeError("Music repair payment could not be confirmed") from ex
                budget -= ln
                info["filled_sec"] = round(float(info["filled_sec"] + ln), 1)
                db = _levels(pcm)       # later holes see this one as music
                if local:
                    local_done.append((g0, g1))
                for a0, a1 in local_done:    # ... except a hole rebuilt locally: it is made-up sound, never evidence for the next hole
                    db[max(0, int(a0 / HOP_SEC)):int(a1 / HOP_SEC) + 1] = -120.0
        if ok_n == 0:
            info["reason"] = "no hole could be filled: " + "; ".join(r["note"] for r in info["gaps"])[:300]
            return info
        pcm.flush()
        pcm._mmap.close()
        pcm = None
        part = Path(str(out_path) + ".mf.tmp.wav")
        _ffmpeg(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(RATE), "-ac", str(CH), "-i", str(raw),
                 "-c:a", "pcm_s16le", str(part)])
        os.replace(part, out_path)
        info["filled"] = True
        made_new = sum(1 for g in info["gaps"] if g.get("ok") and "made new" in str(g.get("note")))
        info["made_new"] = made_new
        info["reason"] = (f"{ok_n} of {len(gaps)} hole(s) in the music filled ({info['filled_sec']} s)"
                          + (f"; {info['reason_cap']}" if info.get("reason_cap") else "")
                          + (f"; {made_new} of them made new from a steady sound" if made_new else "")
                          + ("; the scene has a life of its own (crowd/street): no sound made up, real pieces only" if tex.get("ok") is False else ""))
        return info
    except Exception as ex:
        info["filled"] = False
        info["reason"] = f"skipped ({str(ex)[:200]})"
        return info
    finally:
        if pcm is not None:
            pcm._mmap.close()
        if ref_pcm is not None:
            try:
                ref_pcm._mmap.close()
            except Exception:
                pass
        for p in (raw, ref_raw, Path(str(out_path) + ".mf.tmp.wav")):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
