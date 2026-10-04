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
STEPS = os.environ.get("MUSIC_FILL_STEPS", "").strip()

REF_FLOOR_DB = -50.0           # quieter than this is not music
GAP_BELOW_REF_DB = 30.0        # a hole is this far under the typical music level (digital silence, a hard mute)
CTX_MUSIC_REL_DB = 12.0        # context frames must be within this of the typical level to count as music
CTX_NEED_SEC = 2.0             # music needed next to a hole (within CTX_LOOK_SEC on either side)
CTX_LOOK_SEC = 6.0
MIN_MUSIC_SEC = 3.0            # music in the whole track, or there is nothing to continue
GAP_PAD_SEC = 0.1
JOIN_HOLES_SEC = 0.5           # two holes closer than this are one hole
CONTEXT_SEC = 10.0             # music sent to the model on each side of a hole
MIN_WINDOW_SEC = 12.0
MAX_WINDOW_SEC = 60.0
XFADE_SEC = 0.2
TARGET_BELOW_CTX_DB = 2.0      # the filled music is this much under the music around it
GAIN_MIN_DB, GAIN_MAX_DB = -12.0, 6.0      # level correction allowed; needing more means the result is not usable
GEN_MIN_DB = -60.0             # a result quieter than this is silence
SEAM_MAX_DB = 9.0              # level difference allowed between a seam and the music beside it
MAX_FILE_SEC = 1800.0
CALL_TIMEOUT_SEC = 240
MAX_WALL_SEC = float(os.environ.get("MUSIC_FILL_MAX_WALL_SEC", "900") or 900)     # all holes of one job together

DEFAULT_PROMPT = ("instrumental background music that continues the same style, instruments, tempo and mood, "
                  "smooth and steady, no vocals, no speech")
NEGATIVE_PROMPT = "vocals, singing, speech, talking, voice, choir, humming, abrupt change, silence"


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


def find_gaps(db, spans):
    """Holes in the music: stretches inside the speech spans where the track is far under the music level.
    Returns (gaps [(start_sec, end_sec)], info). Gaps need real music next to them."""
    info = {"ref_db": None, "music_sec": 0.0, "found": 0, "too_long": 0, "no_context": 0}
    n = len(db)
    if n == 0 or not spans:
        return [], info
    music = db >= REF_FLOOR_DB
    info["music_sec"] = round(float(music.sum()) * HOP_SEC, 1)
    if music.sum() * HOP_SEC < MIN_MUSIC_SEC:
        return [], info
    ref = float(np.median(db[music]))
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
        if length < MIN_GAP_SEC:
            continue
        info["found"] += 1
        if length > MAX_GAP_SEC:
            info["too_long"] += 1
            continue
        near = np.concatenate([db[max(0, s - look):s], db[e:e + look]])
        if (near >= ctx_rel).sum() * HOP_SEC < CTX_NEED_SEC:
            info["no_context"] += 1
            continue
        gaps.append((float(max(0.0, s * HOP_SEC - GAP_PAD_SEC)), float(min(n * HOP_SEC, e * HOP_SEC + GAP_PAD_SEC))))
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
            "mask_end_seconds": round(float(m1), 3), "negative_prompt": NEGATIVE_PROMPT,
            "output_format": "wav"}
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
            {"text": ("Describe ONLY the background music in this clip for an AI music generator, as one short line of "
                      "comma separated tags: genre or mood, main instruments, tempo, energy. No vocals. No sentences, "
                      "at most 25 words. Return just the line.")}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 512}}
        data, err = gemini_service.call_gemini(gemini_key, payload, timeout=60)
        if data is None:
            if log:
                log(f"music description failed: {str(err)[:150]}")
            return ""
        txt = data["candidates"][0]["content"]["parts"][0]["text"].strip().strip("`").strip()
        txt = " ".join(txt.split())[:240]
        return txt
    except Exception as ex:
        if log:
            log(f"music description failed: {str(ex)[:150]}")
        return ""


def _seg_db(x):
    if x.shape[0] == 0:
        return -120.0
    return float(_db(((x.astype(np.float32) / 32768.0) ** 2).mean()))


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
    wav = Path(os.environ.get("TMPDIR") or "/tmp") / f"mf_{os.getpid()}_{int(g0 * 1000)}.wav"
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
    gen_db = _seg_db(seg)
    if gen_db < GEN_MIN_DB:
        return False, f"the model made silence ({gen_db:.0f} dB)"
    target = ctx_db - TARGET_BELOW_CTX_DB
    gain_db = target - gen_db
    if gain_db > GAIN_MAX_DB or gain_db < GAIN_MIN_DB:
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


def fill(bg_path, out_path, spans, key, gemini_key=None, prompt=None, work_dir=None, runner=None, log=None,
         allow=None, on_filled=None):
    """Fills the holes in the background track `bg_path` (the one that voices were silenced in) and writes the result to
    `out_path`. spans = [(start_sec, end_sec)] where the original voices speak. Returns
    {"filled": bool, "reason", "gaps": [{start, end, ok, note}], "filled_sec", "sent_sec", "prompt"}; never raises.
    When nothing was filled, `out_path` is not written.
    allow() -> bool is asked before every call (False = stop calling the model, for example when the user cannot pay);
    on_filled(start_sec, end_sec) is called after each hole that was really filled (the place to charge for it)."""
    info = {"filled": False, "reason": "", "gaps": [], "filled_sec": 0.0, "sent_sec": 0.0, "prompt": "", "found": 0}
    runner = runner or _fal_run
    say = log or (lambda m: None)
    raw = Path(str(out_path) + ".mf.pcm")
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
        gaps, gi = find_gaps(db, spans)
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
        music_prompt = prompt
        if not music_prompt:
            desc = describe_music(gemini_key, np.array(pcm), db, log=say) if gemini_key else ""
            music_prompt = (desc + ", instrumental, no vocals, no speech") if desc else DEFAULT_PROMPT
        info["prompt"] = music_prompt
        ctx_ref = float(np.median(db[db >= REF_FLOOR_DB]))
        budget = MAX_TOTAL_SEC
        ok_n = 0
        t_start = time.time()
        for g0, g1 in sorted(gaps):
            ln = g1 - g0
            rec = {"start": round(float(g0), 1), "end": round(float(g1), 1), "ok": False, "note": ""}
            info["gaps"].append(rec)
            if ln > budget:
                rec["note"] = "over the per-job limit"
                continue
            if time.time() - t_start > MAX_WALL_SEC:
                rec["note"] = "out of time"
                continue
            if allow is not None:
                try:
                    permitted = bool(allow())
                except Exception:
                    permitted = False
                if not permitted:
                    rec["note"] = "not enough credits to repair this one"
                    continue
            # level of the music right around this hole
            hop = int(1 / HOP_SEC)
            s, e = int(g0 / HOP_SEC), int(g1 / HOP_SEC)
            near = np.concatenate([db[max(0, s - 6 * hop):s], db[e:e + 6 * hop]])
            near = near[near >= ctx_ref - CTX_MUSIC_REL_DB]
            ctx_db = float(np.median(near)) if near.size else ctx_ref
            try:
                ok, note = _fill_one(pcm, g0, g1, ctx_db, key, music_prompt, runner, say)
            except Exception as ex:
                ok, note = False, f"call failed: {str(ex)[:160]}"
            rec["ok"], rec["note"] = ok, note
            info["sent_sec"] = round(info["sent_sec"] + min(MAX_WINDOW_SEC, max(MIN_WINDOW_SEC, ln + 2 * CONTEXT_SEC)), 1)
            if ok:
                ok_n += 1
                if on_filled is not None:
                    try:
                        on_filled(g0, g1)
                    except Exception as ex:
                        say(f"charge hook failed: {str(ex)[:120]}")
                budget -= ln
                info["filled_sec"] = round(float(info["filled_sec"] + ln), 1)
                db = _levels(pcm)       # later holes see this one as music
        if ok_n == 0:
            info["reason"] = "no hole could be filled: " + "; ".join(r["note"] for r in info["gaps"])[:300]
            return info
        pcm.flush()
        del pcm
        part = Path(str(out_path) + ".mf.tmp.wav")
        _ffmpeg(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(RATE), "-ac", str(CH), "-i", str(raw),
                 "-c:a", "pcm_s16le", str(part)])
        os.replace(part, out_path)
        info["filled"] = True
        info["reason"] = (f"{ok_n} of {len(gaps)} hole(s) in the music filled ({info['filled_sec']} s)"
                          + (f"; {info['reason_cap']}" if info.get("reason_cap") else ""))
        return info
    except Exception as ex:
        info["filled"] = False
        info["reason"] = f"skipped ({str(ex)[:200]})"
        return info
    finally:
        for p in (raw, Path(str(out_path) + ".mf.tmp.wav")):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
