"""Tone match: the dubbed voices get the tonal colour of the original recording.

Why: a generated voice is perfectly clean and full-range, while the original voices were recorded through a microphone, a room and
a camera: a narrower or darker or brighter spectrum, a little less low end, a different "air". That difference is much of what makes a
dub sound like a voice-over laid on top instead of people in the scene. (The room's echo is added by room_acoustics.py; this is the
other half: the colour of the voice itself.)

How: the average spectrum of the SPEECH of the original voices (the separated voice track, only inside the lines, only the frames that
really carry speech) is compared with the average spectrum of the dubbed speech, in 11 bands from 100 Hz to 10 kHz (about 2/3 octave
each). The loudness difference is taken out first (loudness has its own step, voice_level.py): only the SHAPE is matched. The
difference is then made gentle and safe:
  * only part of it is applied (TONE_BLEND, default 60 %), never more than TONE_MAX_DB (default 4 dB) up or down in a band,
  * differences under 0.8 dB are left alone,
  * a band in which the separated voices of the original are nearly empty (the separation cut it away) is not copied,
  * above 6 kHz the voice is never boosted by more than 2 dB (no hiss).
The result is a handful of ordinary peaking filters run by ffmpeg over the whole dub: no delay (lines stay exactly where they are),
no change of length, and it needs little memory however long the video is.

Everything here is best effort and never raises into the caller: on any problem the dub is left as it is.
Switch off globally with LONGDUB_TONE_MATCH=0.
"""
import os
import re
import subprocess
from pathlib import Path

import numpy as np

ENABLED = os.environ.get("LONGDUB_TONE_MATCH", "1").strip().lower() not in ("0", "off", "no", "false", "")

RATE = 44100
FRAME = 2048
HOP = 1024
CENTERS = (100, 160, 250, 400, 630, 1000, 1600, 2500, 4000, 6300, 10000)
BAND_RATIO = 10 ** 0.1                 # a band reaches from centre / 1.26 to centre * 1.26 (neighbours touch)
REF_RANGE = (300.0, 3000.0)            # the overall level difference is read here and taken out
ACTIVE_BELOW_DB = 20.0                 # a frame counts as speech when it is within this many dB of the loud ones
MIN_SPEECH_SEC = 8.0                   # less speech than this (either side) is too little to say anything
EMPTY_BAND_DB = 40.0                   # a band this far under the strongest band holds nothing worth copying
MIN_DB = 0.8                           # smaller differences are left alone
HIGH_FROM_HZ = 6000.0
HIGH_BOOST_MAX_DB = 2.0
FILTER_WIDTH_OCT = 0.8
TARGET_PEAK_DB = -0.3

try:
    MAX_DB = float(os.environ.get("LONGDUB_TONE_MAX_DB", "4") or 4)
    BLEND = float(os.environ.get("LONGDUB_TONE_BLEND", "0.6") or 0.6)
except ValueError:
    MAX_DB, BLEND = 4.0, 0.6


def _db(x):
    return 10.0 * np.log10(np.maximum(x, 1e-20))


def _band_bins():
    freqs = np.fft.rfftfreq(FRAME, 1.0 / RATE)
    out = []
    for c in CENTERS:
        idx = np.nonzero((freqs >= c / BAND_RATIO) & (freqs < c * BAND_RATIO))[0]
        out.append(idx)
    return out


_BINS = _band_bins()
_WINDOW = np.hanning(FRAME)


# ---------------------------------------------------------------- measuring

def _merge(windows):
    ws = sorted((float(a), float(z)) for a, z in windows if z > a)
    out = []
    for a, z in ws:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], z)
        else:
            out.append([a, z])
    return out


def measure(path, windows):
    """-> {"bands": [dB per centre], "seconds": speech seconds used} for the speech inside `windows` (seconds) of the audio at `path`,
    or None when there is too little speech. Streamed: a one hour file needs little memory."""
    wins = _merge(windows)
    if not wins:
        return None
    starts = np.array([w[0] for w in wins])
    ends = np.array([w[1] for w in wins])
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    levels, bands = [], []
    buf = np.zeros(0, dtype=np.float32)
    first = 0                                  # sample index of buf[0]
    rest = b""                                 # bytes of a sample cut in two by the pipe
    block = HOP * 512 * 4
    try:
        while True:
            raw = proc.stdout.read(block)
            if raw:
                raw = rest + raw
                cut = len(raw) - len(raw) % 4
                rest = raw[cut:]
                buf = np.concatenate([buf, np.frombuffer(raw[:cut], dtype="<f4")])
            n_frames = (buf.size - FRAME) // HOP + 1 if buf.size >= FRAME else 0
            if n_frames > 0:
                k = np.arange(n_frames)
                centre_t = (first + k * HOP + FRAME / 2.0) / RATE
                pos = np.searchsorted(starts, centre_t, side="right") - 1
                inside = (pos >= 0) & (centre_t < ends[np.maximum(pos, 0)])
                sel = np.nonzero(inside)[0]
                if sel.size:
                    idx = sel[:, None] * HOP + np.arange(FRAME)[None, :]
                    fr = buf[idx].astype(np.float64) * _WINDOW
                    p = np.abs(np.fft.rfft(fr, axis=1)) ** 2
                    levels.append(_db(p.sum(axis=1)))
                    bands.append(np.stack([p[:, b].mean(axis=1) if b.size else np.zeros(p.shape[0]) for b in _BINS], axis=1))
                drop = n_frames * HOP
                buf = buf[drop:]
                first += drop
            if not raw:
                break
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.kill()
        proc.wait()
    if not levels:
        return None
    lv = np.concatenate(levels)
    bd = np.concatenate(bands, axis=0)
    thresh = float(np.percentile(lv, 95)) - ACTIVE_BELOW_DB
    act = lv >= thresh
    seconds = float(act.sum()) * HOP / RATE
    if seconds < MIN_SPEECH_SEC:
        return None
    return {"bands": [float(x) for x in _db(bd[act].mean(axis=0))], "seconds": seconds}


# ---------------------------------------------------------------- deciding

def plan_offsets(orig_bands, dub_bands, max_db=None, blend=None):
    """-> (offsets in dB per centre, raw difference in dB per centre). The shape of the original's speech minus the shape of the dub's,
    level taken out, made gentle: partial, limited, empty bands and a hissy top left alone."""
    max_db = MAX_DB if max_db is None else max_db
    blend = BLEND if blend is None else blend
    o, d = np.asarray(orig_bands, dtype=float), np.asarray(dub_bands, dtype=float)
    diff = o - d
    cen = np.array(CENTERS, dtype=float)
    ref_mask = (cen >= REF_RANGE[0]) & (cen <= REF_RANGE[1])
    diff = diff - float(np.median(diff[ref_mask]))              # loudness is not tone
    raw = diff.copy()
    diff[o < o.max() - EMPTY_BAND_DB] = 0.0                     # the separation cut this band away: nothing to copy
    diff[d < d.max() - EMPTY_BAND_DB] = 0.0                     # nothing in the dub to shape
    padded = np.concatenate([[diff[0]], diff, [diff[-1]]])
    diff = 0.25 * padded[:-2] + 0.5 * padded[1:-1] + 0.25 * padded[2:]
    off = np.clip(blend * diff, -max_db, max_db)
    hi = cen >= HIGH_FROM_HZ
    off[hi] = np.minimum(off[hi], HIGH_BOOST_MAX_DB)
    off[np.abs(off) < MIN_DB] = 0.0
    return [round(float(x), 1) for x in off], [round(float(x), 1) for x in raw]


def filter_chain(offsets, pre_gain_db=0.0):
    """The ffmpeg -af string for these offsets ('' when there is nothing to do)."""
    parts = []
    if pre_gain_db < -0.05:
        parts.append(f"volume={pre_gain_db:.2f}dB")
    for c, g in zip(CENTERS, offsets):
        if g:
            parts.append(f"equalizer=f={c}:t=o:w={FILTER_WIDTH_OCT:g}:g={g:g}")
    return ",".join(parts) if any(offsets) else ""


# ---------------------------------------------------------------- applying

def _peak_db(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=1800)
    m = re.search(r"max_volume:\s*(-?[0-9.]+|-inf)\s*dB", r.stderr or "")
    if not m or m.group(1) == "-inf":
        return None
    return float(m.group(1))


def apply(in_wav, out_wav, offsets):
    """in_wav -> out_wav with the filters. Same length, same channels, same rate; the lines stay exactly where they are. True on success."""
    try:
        peak = _peak_db(in_wav)
        pre = 0.0
        if peak is not None:
            pre = min(0.0, TARGET_PEAK_DB - (peak + max(max(offsets), 0.0)))      # keep the louder result under full scale
        chain = filter_chain(offsets, pre)
        if not chain:
            return False
        part = Path(str(out_wav) + ".part.wav")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(in_wav), "-af", chain, "-c:a", "pcm_s16le", str(part)],
                           capture_output=True, timeout=1800)
        if r.returncode != 0 or not part.exists() or part.stat().st_size < 1000:
            try:
                part.unlink()
            except Exception:
                pass
            return False
        os.replace(part, out_wav)
        return True
    except Exception as ex:
        print(f"[tone] apply failed: {ex}")
        return False


def run(orig_voice_path, dub_path, out_path, lines):
    """Measure, decide, apply. `lines` = the placed lines [{"start", "allowed" (where the dub plays), "orig_start", "end" (the original line)}].
    Returns {"applied": bool, "why": str, "offsets": [...], "raw": [...]}; out_path is written only when applied."""
    res = {"applied": False, "why": "", "offsets": [], "raw": []}
    if not ENABLED:
        res["why"] = "switched off (LONGDUB_TONE_MATCH=0)"
        return res
    try:
        ow, dw = [], []
        for it in lines or []:
            try:
                o0 = float(it.get("orig_start", it["start"]))
                o1 = float(it["end"])
                d0 = float(it["start"])
                d1 = d0 + float(it["allowed"])
            except (KeyError, TypeError, ValueError):
                continue
            if o1 > o0 and d1 > d0:
                ow.append((o0, o1))
                dw.append((d0, d1))
        if not ow:
            res["why"] = "no lines to measure"
            return res
        mo = measure(orig_voice_path, ow)
        md = measure(dub_path, dw)
        if mo is None or md is None:
            res["why"] = "left as it is: too little clear speech to compare (original %s, dub %s)" % (
                "ok" if mo else "under %gs" % MIN_SPEECH_SEC, "ok" if md else "under %gs" % MIN_SPEECH_SEC)
            return res
        off, raw = plan_offsets(mo["bands"], md["bands"])
        res.update(offsets=off, raw=raw)
        seen = "original %.0fs and dub %.0fs of speech compared" % (mo["seconds"], md["seconds"])
        if not any(off):
            res["why"] = "left as it is: the dub already has the original's tone (%s)" % seen
            return res
        if not apply(dub_path, out_path, off):
            res["why"] = "could not be applied; dub left as it is"
            return res
        res["applied"] = True
        res["why"] = "%s; changes in dB: %s (a part of the difference, at most %g dB a band)" % (
            seen, ", ".join("%g Hz %+g" % (c, g) for c, g in zip(CENTERS, off) if g), MAX_DB)
        return res
    except Exception as ex:
        res["why"] = ("skipped (%s)" % ex)[:200]
        return res
