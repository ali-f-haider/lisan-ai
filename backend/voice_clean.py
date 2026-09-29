"""Cleans a finished dubbed voice track before it is mixed with the background
and put back into the video: the voice is isolated from everything that is not
voice (hiss, machine noise, artifacts of the speech generation) with the same
Demucs separation used for the original video, then a light noise reduction is
applied.

It can never make a job fail: on any problem, or when the separated voice comes
back much quieter than the original (the separation ate part of the speech),
the untouched original is kept and the reason is reported.

Switch it off with the Railway variable CLEAN_VOICE=0."""
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import ffmpeg_utils

ENABLED = (os.environ.get("CLEAN_VOICE") or "0").strip().lower() in ("1", "true", "yes", "on")   # off unless CLEAN_VOICE=1
# Light on purpose: strong reduction makes speech sound watery. The noise
# reduction is told the real noise floor of the file (measured in its quietest
# moments); when the floor is already very low nothing is applied at all.
DENOISE_REDUCTION_DB = 10
SKIP_BELOW_DB = -62.0    # a floor this low is inaudible: leave the voice alone
NOT_A_FLOOR_ABOVE_DB = -28.0   # no real pause in this file: the "floor" would be speech
MAX_LOSS_DB = 6.0       # the separated voice may be at most this much quieter than the original
MAX_BOOST_DB = 6.0      # ...and the level is given back up to this much


def _mean_db(path):
    try:
        p = subprocess.run(["ffmpeg", "-nostats", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=300)
        m = re.search(r"mean_volume:\s*(-?[0-9.]+)\s*dB", p.stderr or "")
        return float(m.group(1)) if m else None
    except Exception:
        return None


def noise_floor_db(path):
    """Level (dB, RMS) of the quietest tenth of the file in 50 ms windows, or
    None when it cannot be read. Only 16-bit wav files are read."""
    try:
        import wave
        import numpy as np
        with wave.open(str(path), "rb") as w:
            ch, sr, sw = w.getnchannels(), w.getframerate(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if sw != 2 or not raw:
            return None
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        a = a.reshape(-1, ch).mean(axis=1)
        win = max(1, int(0.05 * sr))
        n = len(a) // win
        if n < 8:
            return None
        rms = np.sqrt((a[:n * win].reshape(n, win) ** 2).mean(axis=1) + 1e-12)
        db = 20 * np.log10(rms)
        return float(np.percentile(db, 10))
    except Exception:
        return None


def clean_voice(src, dst, work_dir, sample_rate=44100, channels=2, copy_on_keep=True):
    """src (any audio file) -> dst (a wav of exactly the same length). With
    copy_on_keep=True a usable dst is ALWAYS left behind (the cleaned voice, or a
    plain copy of src); with False, dst only exists when "cleaned" is True. Returns
    {"cleaned": bool, "reason": str, "loss_db", "gain_db", "floor_db", "denoised"}."""
    src, dst = Path(src), Path(dst)
    info = {"cleaned": False, "reason": "", "loss_db": None, "gain_db": None, "floor_db": None, "denoised": False}

    def keep(reason):
        info["reason"] = reason
        if copy_on_keep and src.resolve() != dst.resolve():
            part = dst.with_name(dst.name + ".part")
            shutil.copyfile(src, part)
            os.replace(part, dst)
        return info

    if not ENABLED:
        return keep("switched off")
    tmp = Path(work_dir) / f"vc_{uuid.uuid4().hex[:8]}"
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        work = tmp / "in.wav"
        ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", str(channels), "-ar", str(sample_rate),
                                 "-acodec", "pcm_s16le", str(work)])
        dur = ffmpeg_utils.get_media_duration(work)
        if dur <= 0.2:
            return keep("too short")
        vocals, _bg = ffmpeg_utils.separate_vocals(str(work), str(tmp / "sep"))
        if not Path(vocals).exists():
            return keep("the separation gave no voice")
        m_in, m_out = _mean_db(work), _mean_db(vocals)
        if m_in is None or m_out is None:
            return keep("levels could not be measured")
        loss = m_in - m_out
        info["loss_db"] = round(loss, 1)
        if loss > MAX_LOSS_DB:
            return keep(f"the separated voice was {loss:.1f} dB quieter, so it was not used")
        gain = max(0.0, min(MAX_BOOST_DB, loss))
        info["gain_db"] = round(gain, 1)
        out = tmp / "out.wav"
        floor = noise_floor_db(vocals)
        info["floor_db"] = None if floor is None else round(floor, 1)
        parts = ["highpass=f=70"]
        if floor is not None and SKIP_BELOW_DB < floor < NOT_A_FLOOR_ABOVE_DB:
            parts.append(f"afftdn=nr={DENOISE_REDUCTION_DB}:nf={max(-80.0, min(-25.0, floor)):.0f}")
            info["denoised"] = True
        if gain > 0.05:
            parts.append(f"volume={gain:.2f}dB")
        chain = ",".join(parts) + ",apad"
        ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(vocals), "-af", chain, "-t", f"{dur:.6f}",
                                 "-ac", str(channels), "-ar", str(sample_rate), "-acodec", "pcm_s16le", str(out)])
        got = ffmpeg_utils.get_media_duration(out)
        if not out.exists() or abs(got - dur) > 0.005:
            return keep(f"the cleaned file had the wrong length ({got:.3f}s instead of {dur:.3f}s)")
        os.replace(out, dst)
        info["cleaned"] = True
        return info
    except Exception as ex:
        return keep(f"{type(ex).__name__}: {str(ex)[:200]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
