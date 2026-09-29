"""Lowers the separated background while the ORIGINAL speakers talk.

The voice separator never removes a voice completely: a faint, thin, metallic
copy of it stays inside the "background" track, and it is heard exactly while
someone speaks (the dubbed voice is then on top of it). This module measures
when the original voices are active (from the separated voices track) and
turns the background down by a few dB in the voice range only, for those
moments. Low frequencies (bass, drums, rumble) are left untouched, and the
change is smooth so nothing pumps or clicks. Everywhere else the background is
identical to before.

Never raises: on any problem the caller keeps the original background.
Switch off with env BG_DUCK_DB=0. Depth in dB: BG_DUCK_DB (default 12).
"""
import os
import subprocess
from pathlib import Path

import numpy as np

RATE = 44100
CH = 2
ANALYSIS_RATE = 16000
HOP = 160                       # 10 ms at 16 kHz -> one envelope value per 10 ms
FRAME_SEC = HOP / ANALYSIS_RATE
CHUNK = RATE * 10               # seconds of audio processed at a time (memory stays small)


def _env_float(name, default):
    try:
        v = os.environ.get(name)
        return float(v) if v not in (None, "") else float(default)
    except Exception:
        return float(default)


DEPTH_DB = _env_float("BG_DUCK_DB", 12.0)
LOW_KEEP_HZ = _env_float("BG_DUCK_KEEP_LOW_HZ", 250.0)   # below this the background is never lowered
ENABLED = DEPTH_DB > 0.5
QUIET_BG_DB = -50.0     # background this quiet while people speak (mean power): never lowered
LOUD_BG_DB = -34.0      # ...and from this level up it is lowered by the full depth (in between: in proportion)

FLOOR_DB = -50.0        # frames quieter than this are never speech
REL_BELOW_PEAK_DB = 30.0   # ...and neither are frames this far under the typical speech level
BRIDGE_SEC = 0.30       # gaps shorter than this between words do not lift the background
PRE_SEC = 0.05          # start lowering a little before the voice begins
POST_SEC = 0.15         # ...and lift a little after it ends
ATTACK_SEC = 0.04
RELEASE_SEC = 0.35


def _voice_levels_db(vocals):
    """Loudness (dB, one value per 10 ms) of the separated original voices."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(vocals), "-vn", "-ac", "1", "-ar", str(ANALYSIS_RATE),
           "-f", "f32le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out = []
    left = b""
    frame_bytes = HOP * 4
    try:
        while True:
            buf = p.stdout.read(frame_bytes * 3000)
            if not buf:
                break
            buf = left + buf
            n = (len(buf) // frame_bytes) * frame_bytes
            left = buf[n:]
            if n:
                a = np.frombuffer(buf[:n], dtype="<f4").reshape(-1, HOP).astype(np.float64)
                out.append(10.0 * np.log10(np.mean(a * a, axis=1) + 1e-12))
    finally:
        try:
            p.stdout.close()
        except Exception:
            pass
        p.wait()
    if p.returncode not in (0, None) and not out:
        raise RuntimeError("could not read the separated voices")
    return np.concatenate(out) if out else np.zeros(0)


def speech_gain_curve(levels_db, depth_db):
    """Gain (1.0 = untouched .. 10^(-depth/20)) per 10 ms from voice loudness.
    Returns (gain, share_of_time_lowered, mask_of_lowered_frames)."""
    from scipy.ndimage import binary_closing

    if levels_db.size == 0:
        return np.ones(0), 0.0, np.zeros(0, dtype=bool)
    audible = levels_db[levels_db > -70.0]
    if audible.size < 50:
        return np.ones(levels_db.size), 0.0, np.zeros(levels_db.size, dtype=bool)
    ref = float(np.percentile(audible, 95))
    thr = max(FLOOR_DB, ref - REL_BELOW_PEAK_DB)
    active = levels_db > thr
    bridge = max(1, int(round(BRIDGE_SEC / FRAME_SEC)))
    active = binary_closing(np.pad(active, bridge), structure=np.ones(bridge, dtype=bool))[bridge:-bridge]
    # extend: a little before a voice starts, a little more after it ends
    pre, post = int(round(PRE_SEC / FRAME_SEC)), int(round(POST_SEC / FRAME_SEC))
    n = active.size
    c = np.concatenate([[0], np.cumsum(active.astype(np.int64))])       # c[k] = active frames before k
    idx = np.arange(n)
    hi = np.minimum(idx + pre + 1, n)          # frames up to `pre` ahead may start the lowering
    lo = np.maximum(idx - post, 0)             # ...and frames up to `post` behind keep it going
    grown = (c[hi] - c[lo]) > 0
    share = float(grown.mean())
    low = 10.0 ** (-abs(depth_db) / 20.0)
    target = np.where(grown, low, 1.0)
    # attack (going down) fast, release (coming back up) slow: one-pole, asymmetric
    a_att = 1.0 - np.exp(-FRAME_SEC / ATTACK_SEC)
    a_rel = 1.0 - np.exp(-FRAME_SEC / RELEASE_SEC)
    g = np.empty_like(target)
    cur = 1.0
    for i in range(target.size):
        t = target[i]
        cur += (t - cur) * (a_att if t < cur else a_rel)
        g[i] = cur
    return g, share, grown


def duck_background(bg_path, vocals_path, out_path, depth_db=None):
    """Writes `out_path` (16-bit stereo WAV, 44.1 kHz, same length as the
    background) with the voice range lowered while the original voices are
    active. Returns a dict; info["ducked"] is False when nothing was done (the
    caller then keeps using the original background)."""
    info = {"ducked": False, "reason": "", "depth_db": None, "share": None}
    out_path = Path(out_path)
    part = out_path.with_name(out_path.name + ".part.wav")
    try:
        depth = DEPTH_DB if depth_db is None else float(depth_db)
        info["depth_db"] = depth
        if depth <= 0.5:
            info["reason"] = "switched off"
            return info
        bg_path, vocals_path = Path(bg_path), Path(vocals_path)
        if not bg_path.exists() or bg_path.stat().st_size < 1000:
            info["reason"] = "no background file"
            return info
        if not vocals_path.exists() or vocals_path.stat().st_size < 1000:
            info["reason"] = "no separated voices file to follow"
            return info
        levels = _voice_levels_db(vocals_path)
        gain, share, mask = speech_gain_curve(levels, depth)
        info["share"] = round(share, 3)
        if gain.size == 0 or share < 0.005:
            info["reason"] = "no speech found in the original"
            return info
        # The lowering hides the faint copy of the voices inside the background. When the
        # background is itself very quiet while people speak, that copy is inaudible and
        # the music/ambience is worth more: lower less, or not at all.
        bgp = _frame_power_db(bg_path)
        m = mask[:bgp.size]
        if m.sum() >= 100:
            lvl = _db(bgp[:m.size][m].mean())
            info["bg_level_db"] = lvl
            k = min(1.0, max(0.0, (lvl - QUIET_BG_DB) / (LOUD_BG_DB - QUIET_BG_DB)))
            eff = depth * k
            info["depth_db"] = round(eff, 1)
            if eff < 1.0:
                info["reason"] = f"background already very quiet while people speak ({lvl} dB): left as it is"
                return info
            if eff < depth - 0.05:
                gain, share, mask = speech_gain_curve(levels, eff)
                depth = eff
        from scipy.signal import butter, sosfilt

        sos = butter(4, LOW_KEEP_HZ / (RATE / 2.0), btype="low", output="sos")
        zi = np.zeros((sos.shape[0], CH, 2))
        centers = (np.arange(gain.size) + 0.5) * (HOP * RATE / ANALYSIS_RATE)

        dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(bg_path), "-vn", "-ac", str(CH), "-ar", str(RATE),
                                "-f", "f32le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", str(CH), "-i", "-",
                                "-c:a", "pcm_s16le", str(part)], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        pos = 0
        left = b""
        frame_bytes = 4 * CH
        ok = True
        try:
            while True:
                buf = dec.stdout.read(CHUNK * frame_bytes)
                if not buf:
                    break
                buf = left + buf
                n = (len(buf) // frame_bytes) * frame_bytes
                left = buf[n:]
                if not n:
                    continue
                x = np.frombuffer(buf[:n], dtype="<f4").reshape(-1, CH).astype(np.float64)
                low, zi = sosfilt(sos, x, axis=0, zi=zi)
                g = np.interp(np.arange(pos, pos + x.shape[0]), centers, gain)[:, None]
                y = g * x + (1.0 - g) * low        # = low band untouched, everything above it scaled by g
                np.clip(y, -1.0, 1.0, out=y)
                enc.stdin.write(y.astype("<f4").tobytes())
                pos += x.shape[0]
        except Exception:
            ok = False
            raise
        finally:
            try:
                dec.stdout.close()
            except Exception:
                pass
            dec.wait()
            try:
                enc.stdin.close()
            except Exception:
                pass
            enc.wait()
        if not ok or dec.returncode not in (0, None) or enc.returncode != 0:
            raise RuntimeError("ffmpeg failed while lowering the background")
        if not part.exists() or part.stat().st_size < 1000 or pos == 0:
            raise RuntimeError("no audio was written")
        os.replace(part, out_path)
        info["ducked"] = True
        info["reason"] = f"lowered {depth:g} dB above {LOW_KEEP_HZ:g} Hz during {share * 100:.0f}% of the time"
        return info
    except Exception as ex:
        info["ducked"] = False
        info["reason"] = f"skipped ({ex})"[:200]
        return info
    finally:
        try:
            if part.exists():
                part.unlink()
        except Exception:
            pass


def _frame_power_db(path):
    """Mean power (dB, one value per 10 ms) of a file, mono 16 kHz."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(ANALYSIS_RATE), "-f", "f32le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out, left, fb = [], b"", HOP * 4
    try:
        while True:
            buf = p.stdout.read(fb * 3000)
            if not buf:
                break
            buf = left + buf
            n = (len(buf) // fb) * fb
            left = buf[n:]
            if n:
                a = np.frombuffer(buf[:n], dtype="<f4").reshape(-1, HOP).astype(np.float64)
                out.append(np.mean(a * a, axis=1))
    finally:
        try:
            p.stdout.close()
        except Exception:
            pass
        p.wait()
    return np.concatenate(out) if out else np.zeros(0)


def _db(x):
    return round(10.0 * float(np.log10(max(x, 1e-12))), 1)


def level_report(original, background, pauses):
    """How loud the original track and the separated background are, overall and
    only inside the pauses of the voices (there the original holds nothing but
    music/ambience, so the two should match). Returns a dict, {} on any problem."""
    try:
        o, b = _frame_power_db(original), _frame_power_db(background)
        n = min(o.size, b.size)
        if n < 100:
            return {}
        o, b = o[:n], b[:n]
        mask = np.zeros(n, dtype=bool)
        for (a, z) in pauses or []:
            i0, i1 = int((float(a) + 0.15) / FRAME_SEC), int((float(z) - 0.15) / FRAME_SEC)
            if i1 > i0:
                mask[max(0, i0):min(n, i1)] = True
        rep = {"orig_all": _db(o.mean()), "bg_all": _db(b.mean()), "pause_sec": round(float(mask.sum()) * FRAME_SEC, 1)}
        if mask.sum() >= 100:
            rep["orig_pauses"], rep["bg_pauses"] = _db(o[mask].mean()), _db(b[mask].mean())
        return rep
    except Exception:
        return {}
