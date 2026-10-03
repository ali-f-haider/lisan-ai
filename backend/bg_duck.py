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
Off by default (BG_DUCK_DB=0); set BG_DUCK_DB=12 to switch it on (it also lowers the ambience while people speak).
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


# OFF by default since v1.70: the ambience (room tone, crowd murmur, music) must stay at the same level for the whole clip,
# with or without speech. Lowering the separated background while people speak also lowered that ambience. Set the
# environment variable BG_DUCK_DB (e.g. 12) to switch the old behaviour back on.
DEPTH_DB = _env_float("BG_DUCK_DB", 0.0)
LOW_KEEP_HZ = _env_float("BG_DUCK_KEEP_LOW_HZ", 250.0)   # below this the background is never lowered
ENABLED = DEPTH_DB > 0.5
MAKEUP_MAX_DB = _env_float("BG_MAKEUP_MAX_DB", 20.0)   # most the separated background is raised to match the original (0 = off)
MAKEUP_MIN_DB = 3.0            # a smaller difference is left alone
MAKEUP_MIN_PAUSE_SEC = 5.0     # needs at least this much silence between the voices to compare levels
MAKEUP_FLOOR_DB = -80.0        # a background this silent in the pauses holds nothing to raise
LEAK_MAX_DB = _env_float("BG_LEAK_MAX_DB", 3.0)   # a background that is this much louder while people speak than in the pauses holds
                                                  # traces of the voices ("shadows"): it is NOT raised, that would only make them audible
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


def _frame_power_db(path, af=None):
    """Mean power (dB, one value per 10 ms) of a file, mono 16 kHz. `af` = an ffmpeg audio filter chain
    applied first (used to measure a track exactly as it is mixed)."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn"]
    if af:
        cmd += ["-af", af]
    cmd += ["-ac", "1", "-ar", str(ANALYSIS_RATE), "-f", "f32le", "-"]
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


def level_report(original, background, pauses, vocals=None):
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
            if vocals is not None:      # where did the sound of the pauses go? into the separated voices?
                v = _frame_power_db(vocals)
                if v.size >= mask.size:
                    rep["voc_pauses"] = _db(v[:mask.size][mask].mean())
        return rep
    except Exception:
        return {}


def final_level_report(mixed_background, mix_filter, dub, pauses):
    """Levels (dB) of the background exactly as it goes into the final video (after the ducking
    and the mix filter), overall and inside the pauses of the voices, and of the dubbed voices.
    Returns a dict, {} on any problem."""
    try:
        b = _frame_power_db(mixed_background, mix_filter)
        n = b.size
        if n < 100:
            return {}
        mask = np.zeros(n, dtype=bool)
        for (a, z) in pauses or []:
            i0, i1 = int((float(a) + 0.15) / FRAME_SEC), int((float(z) - 0.15) / FRAME_SEC)
            if i1 > i0:
                mask[max(0, i0):min(n, i1)] = True
        rep = {"final_bg_all": _db(b.mean())}
        if mask.sum() >= 100:
            rep["final_bg_pauses"] = _db(b[mask].mean())
        if dub is not None:
            d = _frame_power_db(dub)
            if d.size >= 100:
                talk = d > (10.0 ** (-50.0 / 10.0))       # frames where the dubbed voice is actually speaking
                rep["dub_voice"] = _db(d[talk].mean()) if talk.sum() >= 100 else _db(d.mean())
                m2 = talk[:n]
                if m2.sum() >= 100:
                    rep["final_bg_while_dub_speaks"] = _db(b[:m2.size][m2].mean())
        return rep
    except Exception:
        return {}


def _pause_mean_db(path, pauses, af=None):
    """Mean power (dB) of a file inside the pauses of the voices (same frames as level_report), or None."""
    p = _frame_power_db(path, af)
    if p.size < 100:
        return None
    mask = np.zeros(p.size, dtype=bool)
    for (a, z) in pauses or []:
        i0, i1 = int((float(a) + 0.15) / FRAME_SEC), int((float(z) - 0.15) / FRAME_SEC)
        if i1 > i0:
            mask[max(0, i0):min(p.size, i1)] = True
    return _db(p[mask].mean()) if mask.sum() >= 100 else None


def speech_leak_db(background, pauses):
    """How much louder (dB, mean power) the separated background is while people speak than inside the pauses.
    A clean background (music, ambience) has about the same level in both; traces of the voices that the separator
    left behind make it clearly louder while someone talks. None when it cannot be told."""
    try:
        b = _frame_power_db(background)
        n = b.size
        if n < 200:
            return None
        pause = np.zeros(n, dtype=bool)
        edge = np.zeros(n, dtype=bool)
        for (a, z) in pauses or []:
            i0, i1 = int(float(a) / FRAME_SEC), int(float(z) / FRAME_SEC)
            if i1 > i0:
                edge[max(0, i0):min(n, i1)] = True
            j0, j1 = int((float(a) + 0.15) / FRAME_SEC), int((float(z) - 0.15) / FRAME_SEC)
            if j1 > j0:
                pause[max(0, j0):min(n, j1)] = True
        speech = ~edge
        k = int(0.15 / FRAME_SEC)           # keep away from the edges of the speech too
        speech = np.convolve(speech.astype(float), np.ones(2 * k + 1), mode="same") >= (2 * k + 1) - 0.5
        if pause.sum() < 100 or speech.sum() < 100:
            return None
        return float(_db(b[speech].mean()) - _db(b[pause].mean()))
    except Exception:
        return None


def makeup_gain(original, background, pauses, max_db=None, mix_filter=None, min_db=None):
    """How many dB the separated background must be raised so that, in the pauses of the voices
    (where the original holds only music and ambience), it is as loud as the original there.
    The separator often keeps far less of the room sound than the original had. Returns
    (gain_db, note); gain_db is 0.0 whenever nothing should be done."""
    try:
        mx = MAKEUP_MAX_DB if max_db is None else float(max_db)
        mn = MAKEUP_MIN_DB if min_db is None else float(min_db)
        if mx < mn:
            return 0.0, "switched off"
        rep = level_report(original, background, pauses)
        if "orig_pauses" not in rep:
            return 0.0, "not enough silence between the voices to compare the levels"
        if rep["pause_sec"] < MAKEUP_MIN_PAUSE_SEC:
            return 0.0, f"only {rep['pause_sec']:g}s of silence between the voices, too little to compare the levels"
        o, b = rep["orig_pauses"], rep["bg_pauses"]
        if b < MAKEUP_FLOOR_DB:
            return 0.0, f"the separated background is silent in the pauses ({b} dB)"
        b_after = b
        if mix_filter:      # what the background loses on its way into the final mix (volume, bass filter)
            m = _pause_mean_db(background, pauses, mix_filter)
            if m is not None:
                b_after = m
        diff = o - b_after
        if diff < mn:
            return 0.0, f"already within {diff:.1f} dB of the original in the pauses"
        leak = speech_leak_db(background, pauses)
        if leak is not None and leak >= LEAK_MAX_DB:
            return 0.0, (f"not raised: the separated background is {leak:.1f} dB louder while people speak than in the pauses "
                         f"(it holds traces of the voices; raising it would only make them audible)")
        g = round(min(diff, mx), 1)
        return g, (f"raised {g:g} dB (in the pauses: original {o} dB, separated background {b} dB"
                   + (f", {b_after} dB after the mix filter" if b_after != b else "")
                   + (f"; limited to {mx:g} dB" if diff > mx else "") + ")")
    except Exception as ex:
        return 0.0, f"skipped ({ex})"[:200]


def lift_background(bg_path, out_path, gain_db):
    """Writes `out_path` (16-bit stereo WAV, 44.1 kHz) = the background raised by gain_db, with a
    limiter so nothing clips. Returns True on success; never raises."""
    out_path = Path(out_path)
    part = out_path.with_name(out_path.name + ".part.wav")
    try:
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(bg_path), "-vn",
               "-af", f"volume={float(gain_db):.2f}dB,alimiter=limit=0.95:level=disabled",
               "-ac", str(CH), "-ar", str(RATE), "-c:a", "pcm_s16le", str(part)]
        r = subprocess.run(cmd, capture_output=True, timeout=900)
        if r.returncode != 0 or not part.exists() or part.stat().st_size < 1000:
            return False
        os.replace(part, out_path)
        return True
    except Exception:
        return False
    finally:
        try:
            if part.exists():
                part.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------- muting the background while the original voices speak
#
# The separator never removes the voices completely: a faint copy of every English phrase stays in the separated
# background ("the shadow of the original voice"). While the original speaker talks, the dubbed Arabic voice is
# there anyway, so the background is simply silenced for exactly those stretches (with short fades) and comes back
# in the pauses. BG_MUTE: "auto" (default) = only when the background holds such a copy (it is louder while people
# speak than in the pauses) or is so quiet then that only the copy could be left; "always"; "off".

MUTE_MODE = os.environ.get("BG_MUTE", "auto").strip().lower()
MUTE_LEAK_DB = _env_float("BG_MUTE_LEAK_DB", 1.5)   # auto: muted when the background is this much louder during speech than in the pauses
MUTE_QUIET_DB = -45.0          # auto: ...or when it is quieter than this during speech (nothing worth keeping there)
MUTE_PRE_SEC = 0.10            # silence starts a little before the first word
MUTE_POST_SEC = 0.30           # ...and ends a little after the last one (the room tail of the original voice)
MUTE_BRIDGE_SEC = 0.60         # a gap shorter than this between two phrases does not bring the background back
MUTE_FADE_DOWN_SEC = 0.025     # time constants of the gain: going silent / coming back
MUTE_FADE_UP_SEC = 0.10
MUTE_VOICE_FLOOR_DB = -45.0    # separated voices quieter than this are the room, not a voice
MUTE_MIN_SPEECH_SEC = 0.5      # less speech than this in the whole video: nothing to mute


def speech_mute_mask(spans, n_frames):
    """Boolean per 10 ms frame: True where the background is to be silent. spans = [(start, end)] seconds of the words."""
    m = np.zeros(n_frames, dtype=bool)
    for a, z in merge_spans(spans, gap=MUTE_BRIDGE_SEC):
        i0 = int((a - MUTE_PRE_SEC) / FRAME_SEC)
        i1 = int((z + MUTE_POST_SEC) / FRAME_SEC) + 1
        if i1 > 0 and i0 < n_frames:
            m[max(0, i0):min(n_frames, i1)] = True
    return m


def _extend_with_voice_onsets(mask, levels_db):
    """Adds to `mask` the frames where the separated voices are clearly audible and that touch (within MUTE_PRE/POST
    reach) a stretch already in the mask. Loud noise far from any word is not taken."""
    from scipy.ndimage import label, binary_dilation

    n = mask.size
    lv = levels_db[:n]
    if lv.size < n:
        lv = np.concatenate([lv, np.full(n - lv.size, -120.0)])
    inside = lv[mask & (lv > -70.0)]
    if inside.size < 50:
        return mask
    thr = max(MUTE_VOICE_FLOOR_DB, float(np.percentile(inside, 95)) - 28.0)
    cand = lv > thr
    reach = int(round(0.5 / FRAME_SEC))
    near = binary_dilation(mask, structure=np.ones(2 * reach + 1, dtype=bool))
    lab, k = label(cand | mask)
    if k == 0:
        return mask
    keep = np.unique(lab[near & cand]) if (near & cand).any() else np.array([], dtype=int)
    keep = keep[keep > 0]
    ext = np.isin(lab, keep) & cand
    out = mask | ext
    # a little room tail after what was added
    post = int(round(0.15 / FRAME_SEC))
    out = binary_dilation(out, structure=np.concatenate([np.zeros(post, dtype=bool), np.ones(post + 1, dtype=bool)]))
    return out


def mute_gain_curve(mask):
    """Gain (1 = untouched, 0 = silent) per 10 ms from the mask, with short fades (never a click)."""
    target = np.where(mask, 0.0, 1.0)
    a_up = 1.0 - np.exp(-FRAME_SEC / MUTE_FADE_UP_SEC)
    a_dn = 1.0 - np.exp(-FRAME_SEC / MUTE_FADE_DOWN_SEC)
    g = np.empty_like(target)
    cur = 1.0
    for i in range(target.size):
        t = target[i]
        cur += (t - cur) * (a_up if t > cur else a_dn)
        g[i] = cur
    return g


def mute_speech(bg_path, vocals_path, out_path, spans=None, mode=None):
    """Writes `out_path` (16-bit stereo WAV, 44.1 kHz) = the background silenced while the original voices speak.
    spans = [(start, end)] of the spoken words (the speech map written at transcription); without it the loudness of
    the separated voices is followed instead. Returns {"muted": bool, "reason": str, "share": share of the time silenced,
    "leak_db", "speech_db"}; "muted" is False when nothing was done (the caller then keeps the background). Never raises."""
    info = {"muted": False, "reason": "", "share": None, "leak_db": None, "speech_db": None}
    out_path = Path(out_path)
    part = out_path.with_name(out_path.name + ".part.wav")
    try:
        mode = (MUTE_MODE if mode is None else str(mode)).strip().lower()
        if mode not in ("auto", "always"):
            info["reason"] = "switched off"
            return info
        bg_path = Path(bg_path)
        if not bg_path.exists() or bg_path.stat().st_size < 1000:
            info["reason"] = "no background file"
            return info
        bgp = _frame_power_db(bg_path)
        n = bgp.size
        if n < 200:
            info["reason"] = "background too short"
            return info
        if spans:
            mask = speech_mute_mask(spans, n)
            src = "speech map"
            if vocals_path is not None and Path(vocals_path).exists():
                # the map starts at the first recognised word: soft starts, breaths and word ends are in the separated
                # voices a little earlier / later. Take them in when they are connected to a spoken stretch.
                try:
                    mask = _extend_with_voice_onsets(mask, _voice_levels_db(vocals_path))
                    src = "speech map and separated voices"
                except Exception:
                    pass
        elif vocals_path is not None and Path(vocals_path).exists():
            _g, _s, grown = speech_gain_curve(_voice_levels_db(vocals_path), 60.0)
            mask = np.zeros(n, dtype=bool)
            k = min(n, grown.size)
            mask[:k] = grown[:k]
            src = "voice loudness"
        else:
            info["reason"] = "no speech map and no separated voices to follow"
            return info
        share = float(mask.mean())
        info["share"] = round(share, 3)
        if float(mask.sum()) * FRAME_SEC < MUTE_MIN_SPEECH_SEC:
            info["reason"] = "no speech found in the original"
            return info
        # does the background hold a copy of the voices? (louder while people speak than in the pauses)
        k = int(0.15 / FRAME_SEC)
        near = np.convolve(mask.astype(float), np.ones(2 * k + 1), mode="same") > 0.5     # speech and its surroundings
        inner = np.convolve((~mask).astype(float), np.ones(2 * k + 1), mode="same") < 0.5   # well inside speech
        far = ~near
        lvl_speech = _db(bgp[inner].mean()) if inner.sum() >= 100 else None
        lvl_pause = _db(bgp[far].mean()) if far.sum() >= 100 else None
        info["speech_db"] = None if lvl_speech is None else round(lvl_speech, 1)
        if lvl_speech is not None and lvl_pause is not None:
            info["leak_db"] = round(lvl_speech - lvl_pause, 1)
        if mode == "auto":
            leaky = info["leak_db"] is not None and info["leak_db"] >= MUTE_LEAK_DB
            quiet = lvl_speech is not None and lvl_speech < MUTE_QUIET_DB
            if not (leaky or quiet):
                info["reason"] = ("not needed: the background is not louder while people speak"
                                  + (f" ({info['leak_db']:+.1f} dB) " if info["leak_db"] is not None else " ")
                                  + "so it holds no copy of the voices")
                return info
        gain = mute_gain_curve(mask)
        centers = (np.arange(gain.size) + 0.5) * (HOP * RATE / ANALYSIS_RATE)
        dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(bg_path), "-vn", "-ac", str(CH), "-ar", str(RATE),
                                "-f", "f32le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", str(CH), "-i", "-",
                                "-c:a", "pcm_s16le", str(part)], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        pos, left, fb = 0, b"", 4 * CH
        try:
            while True:
                buf = dec.stdout.read(CHUNK * fb)
                if not buf:
                    break
                buf = left + buf
                m_ = (len(buf) // fb) * fb
                left = buf[m_:]
                if not m_:
                    continue
                x = np.frombuffer(buf[:m_], dtype="<f4").reshape(-1, CH).astype(np.float64)
                g = np.interp(np.arange(pos, pos + x.shape[0]), centers, gain)[:, None]
                enc.stdin.write(np.clip(x * g, -1.0, 1.0).astype("<f4").tobytes())
                pos += x.shape[0]
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
        if dec.returncode not in (0, None) or enc.returncode != 0 or pos == 0 or not part.exists() or part.stat().st_size < 1000:
            raise RuntimeError("ffmpeg failed while muting the background")
        os.replace(part, out_path)
        info["muted"] = True
        why = ("holds a copy of the voices" if (info["leak_db"] is not None and info["leak_db"] >= MUTE_LEAK_DB)
               else "nothing but a faint copy of the voices is left in it" if mode == "auto" else "always on")
        info["reason"] = (f"silenced while the original voices speak ({share * 100:.0f}% of the time, from the {src}); "
                          f"{why}"
                          + (f": {info['speech_db']} dB while people speak, {info['leak_db']:+.1f} dB against the pauses"
                             if info["speech_db"] is not None and info["leak_db"] is not None else ""))
        return info
    except Exception as ex:
        info["muted"] = False
        info["reason"] = f"skipped ({ex})"[:200]
        return info
    finally:
        try:
            if part.exists():
                part.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------- ambience bed
#
# The voice separator often files a steady background sound (a crowd murmur, a machine hum, traffic,
# rain, wind ...) under "voices". In the pauses between the speakers that sound is then the ONLY thing in
# the voices track. The bed is made from exactly those steady stretches: pieces of them are joined in
# random order with long crossfades and laid under the whole video, so the sound also continues while
# people speak. Nothing is done unless the sound is clearly missing and clean, steady material exists.

BED_ENABLED = _env_float("BG_BED", 1.0) > 0.5
BED_TRIGGER_DB = 6.0           # the pauses must be at least this much quieter in the separated background than in the original
BED_MIN_SOURCE_SEC = 4.0       # least clean, steady material needed
BED_MAX_SOURCE_SEC = 90.0      # most material that is used
BED_MARGIN_SEC = 0.25          # kept away from both ends of a pause (word tails, breaths)
BED_MIN_SEG_SEC = 2.0          # shortest usable stretch
BED_STEADY_DB = 6.0            # a stretch whose 0.1 s loudness jumps more than this (clinks, words, barks) is not used
BED_LEVEL_WINDOW_DB = 5.0      # stretches this far from the typical level are not used
BED_FLOOR_DB = -70.0           # quieter than this there is nothing to rebuild
BED_XFADE = int(0.6 * RATE)    # crossfade between two pieces
BED_PIECE_SEC = (3.0, 7.0)


def _bed_material(vocals, pauses):
    """Clean, steady stretches of the separated voices found in the pauses. Returns (list, note);
    each item = {"t0","t1","level","spread"} (seconds, dB of mean power)."""
    p = _frame_power_db(vocals)
    if p.size < 300:
        return [], "the separated voices track is too short"
    cands = []
    for (a, z) in pauses or []:
        i0 = int((float(a) + BED_MARGIN_SEC) / FRAME_SEC)
        i1 = min(int((float(z) - BED_MARGIN_SEC) / FRAME_SEC), p.size)
        if i1 - i0 < int(BED_MIN_SEG_SEC / FRAME_SEC):
            continue
        seg = p[i0:i1]
        lvl = _db(seg.mean())
        if lvl < BED_FLOOR_DB:
            continue
        sm = 10.0 * np.log10(np.convolve(seg, np.ones(10) / 10.0, mode="valid") + 1e-12)
        spread = float(np.percentile(sm, 95) - np.percentile(sm, 50))
        cands.append({"t0": i0 * FRAME_SEC, "t1": i1 * FRAME_SEC, "level": lvl, "spread": round(spread, 1)})
    if not cands:
        return [], "no stretch between the voices is long and loud enough"
    steady = [c for c in cands if c["spread"] <= BED_STEADY_DB]
    if not steady:
        return [], f"none of the {len(cands)} stretches is steady (clinks, words or other single sounds in them)"
    med = float(np.median([c["level"] for c in steady]))
    steady = [c for c in steady if abs(c["level"] - med) <= BED_LEVEL_WINDOW_DB]
    steady.sort(key=lambda c: c["t1"] - c["t0"], reverse=True)
    out, tot = [], 0.0
    for c in steady:
        if tot >= BED_MAX_SOURCE_SEC:
            break
        out.append(c)
        tot += c["t1"] - c["t0"]
    if tot < BED_MIN_SOURCE_SEC:
        return [], f"only {tot:.1f}s of clean, steady sound between the voices (needs {BED_MIN_SOURCE_SEC:g}s)"
    return out, f"{len(out)} stretches, {tot:.0f}s"


def plan_ambience_bed(original, background, vocals, pauses):
    """Decides whether a bed should be built. Returns (plan, reason); plan is None when not."""
    try:
        if not BED_ENABLED:
            return None, "switched off"
        rep = level_report(original, background, pauses)
        if "orig_pauses" not in rep or rep.get("pause_sec", 0) < MAKEUP_MIN_PAUSE_SEC:
            return None, "not enough silence between the voices to compare the sound"
        o, b = rep["orig_pauses"], rep["bg_pauses"]
        missing = o - b
        if missing < BED_TRIGGER_DB:
            return None, f"the separated background already holds the sound of the pauses (gap {missing:.1f} dB)"
        v = _pause_mean_db(vocals, pauses)
        if v is None or v < o - 8.0:
            return None, f"the missing sound is not in the separated voices either (voices {v} dB in the pauses, original {o} dB)"
        segs, note = _bed_material(vocals, pauses)
        if not segs:
            return None, note
        target = float(np.median([c["level"] for c in segs]))
        return {"segs": segs, "target": target, "vocals": str(vocals), "missing": round(missing, 1), "note": note}, note
    except Exception as ex:
        return None, f"skipped ({ex})"[:200]


def _read_mono(path, t0, dur):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", str(path), "-vn",
                        "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"], capture_output=True, timeout=300)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


class _BedStream:
    """Endless steady sound made of random pieces of the source stretches, equal-power crossfaded."""

    def __init__(self, segs, seed):
        self.segs = segs
        self.rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
        w = np.array([len(s["x"]) for s in segs], dtype=np.float64)
        self.w = w / w.sum()
        self.acc = np.zeros(0, dtype=np.float32)
        self.first = True
        t = np.linspace(0.0, np.pi / 2.0, BED_XFADE, dtype=np.float32)
        self.fin, self.fout = np.sin(t), np.cos(t)

    def _add_piece(self):
        s = self.segs[int(self.rng.choice(len(self.segs), p=self.w))]
        x = s["x"]
        L = int(self.rng.uniform(*BED_PIECE_SEC) * RATE)
        L = min(L, x.size)
        off = int(self.rng.integers(0, max(1, x.size - L + 1)))
        p = (x[off:off + L] * s["gain"]).astype(np.float32)
        if p.size <= 2 * BED_XFADE:
            return
        p[-BED_XFADE:] *= self.fout
        if self.acc.size == 0:
            self.acc = p
        else:
            p[:BED_XFADE] *= self.fin
            self.acc[-BED_XFADE:] += p[:BED_XFADE]
            self.acc = np.concatenate([self.acc, p[BED_XFADE:]])

    def take(self, n):
        guard = 0
        while self.acc.size - BED_XFADE < n:
            before = self.acc.size
            self._add_piece()
            guard += 1
            if guard > 10000 or (self.acc.size == before and guard > 50):
                raise RuntimeError("could not build the ambience bed")
        out, self.acc = self.acc[:n], self.acc[n:]
        return out


def add_ambience_bed(plan, sep_path, out_path, seed=0):
    """Writes `out_path` (16-bit stereo WAV, 44.1 kHz) = the separated background + the ambience bed
    laid under the whole video. Returns a dict {"ok", "reason", ...}; never raises."""
    info = {"ok": False, "reason": ""}
    out_path = Path(out_path)
    part = out_path.with_name(out_path.name + ".part.wav")
    dec = enc = None
    try:
        segs = []
        for c in plan["segs"]:
            x = _read_mono(plan["vocals"], c["t0"], c["t1"] - c["t0"])
            if x.size < BED_MIN_SEG_SEC * RATE * 0.9:
                continue
            segs.append({"x": x, "gain": float(10.0 ** ((plan["target"] - c["level"]) / 20.0))})
        if not segs or sum(s["x"].size for s in segs) < BED_MIN_SOURCE_SEC * RATE * 0.9:
            info["reason"] = "could not read the clean stretches"
            return info
        bed = _BedStream(segs, seed)
        dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(sep_path), "-vn", "-ac", str(CH), "-ar", str(RATE),
                                "-f", "f32le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", str(CH), "-i", "-",
                                "-c:a", "pcm_s16le", str(part)], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        left, fb, pos = b"", 4 * CH, 0
        while True:
            buf = dec.stdout.read(CHUNK * fb)
            if not buf:
                break
            buf = left + buf
            n = (len(buf) // fb) * fb
            left = buf[n:]
            if not n:
                continue
            x = np.frombuffer(buf[:n], dtype="<f4").reshape(-1, CH).astype(np.float32)
            y = x + bed.take(x.shape[0])[:, None]
            np.clip(y, -1.0, 1.0, out=y)
            enc.stdin.write(y.astype("<f4").tobytes())
            pos += x.shape[0]
        dec.stdout.close()
        dec.wait()
        enc.stdin.close()
        enc.wait()
        if dec.returncode not in (0, None) or enc.returncode != 0 or pos == 0 or not part.exists() or part.stat().st_size < 1000:
            info["reason"] = "ffmpeg failed while adding the ambience bed"
            return info
        os.replace(part, out_path)
        info.update({"ok": True, "reason": f"built from {plan['note']} of the separated voices in the pauses, "
                                           f"level {plan['target']:.1f} dB, {plan['missing']} dB was missing"})
        return info
    except Exception as ex:
        info["reason"] = f"skipped ({ex})"[:200]
        return info
    finally:
        for pr in (dec, enc):
            try:
                if pr is not None and pr.poll() is None:
                    pr.kill()
            except Exception:
                pass
        try:
            if part.exists():
                part.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------- reactions: laughter, applause, cheers
#
# The voice separator files every human sound that is not a word (an audience laughing, applauding, cheering,
# gasping) under "voices", so none of it reaches the background track and the dubbed video ends up with dead
# silence exactly where the original had its loudest moments. The recogniser knows where WORDS are. Everything
# the separator called "voice" outside those words is a human sound without speech: it is cut out of the
# separated voices track (never the English words) and laid back under the dub, at its original place and
# level, as a layer of its own. While the dubbed voice speaks, the layer is lowered.
# Nothing is done when no such stretches exist; on any problem the caller keeps the video as it was.

REACT_ENABLED = _env_float("BG_REACTIONS", 1.0) > 0.5
REACT_GAIN_DB = _env_float("BG_REACTIONS_DB", 0.0)          # level of the layer relative to the original
REACT_DUCK_DB = _env_float("BG_REACTIONS_DUCK_DB", 9.0)     # lowered by this much while the dubbed voice speaks
REACT_PRE_SEC = 0.12          # kept away from the start of a spoken word
REACT_POST_SEC = 0.18         # ...and from its end
REACT_MIN_SEC = 0.35          # shorter sounds are left out
REACT_BRIDGE_SEC = 0.25       # gaps this short inside one reaction (between two bursts of laughter) are kept
REACT_REL_DB = 30.0           # quieter than this far under the speech level = not a reaction
REACT_MIN_MEAN_REL_DB = 22.0   # a stretch whose average is this far under the speech level is a tail or a breath, not a reaction
REACT_MIN_TOTAL_SEC = 0.8     # less than this in total: not worth adding
REACT_MAX_SHARE = 0.6         # more than this share of the video would not be audience sound (singing, a wrong speech map)
REACT_FADE_IN_SEC = 0.08      # fade in
REACT_FADE_OUT_SEC = _env_float("BG_REACTIONS_FADE_OUT", 1.5)   # laughter is never cut: it fades to zero over (at most) this long
REACT_FADE_SHARE = 0.75       # ...and over at most this share of a short reaction
REACT_TAIL_SEC = 0.5          # the natural tail of a reaction is kept this far past the point it drops under the threshold


def merge_spans(spans, gap=0.35):
    """[(start, end)] sorted and joined when closer than `gap` seconds."""
    out = []
    for a, z in sorted((float(a), float(z)) for a, z in spans if z is not None and a is not None and float(z) >= float(a)):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], z)
        else:
            out.append([a, z])
    return [(a, z) for a, z in out]


def reaction_frames(levels_db, spans, min_level_db=None):
    """Which 10 ms frames of the separated voices are human sound WITHOUT words.
    Returns (mask, speech_mask, info)."""
    from scipy.ndimage import binary_closing

    n = levels_db.size
    speech = np.zeros(n, dtype=bool)
    for a, z in spans:
        i0, i1 = int((float(a) - REACT_PRE_SEC) / FRAME_SEC), int((float(z) + REACT_POST_SEC) / FRAME_SEC) + 1
        if i1 > 0 and i0 < n:
            speech[max(0, i0):min(n, i1)] = True
    audible = levels_db[levels_db > -70.0]
    if audible.size < 50:
        return np.zeros(n, dtype=bool), speech, {"reason": "no sound in the separated voices"}
    ref_pool = levels_db[speech & (levels_db > -70.0)]
    ref = float(np.percentile(ref_pool if ref_pool.size >= 50 else audible, 95))
    thr = max(FLOOR_DB, ref - REACT_REL_DB)
    if min_level_db is not None:
        thr = max(thr, float(min_level_db))
    cand = (levels_db > thr) & ~speech
    b = max(1, int(round(REACT_BRIDGE_SEC / FRAME_SEC)))
    cand = binary_closing(np.pad(cand, b), structure=np.ones(b, dtype=bool))[b:-b] & ~speech
    # drop the short ones
    min_len = int(round(REACT_MIN_SEC / FRAME_SEC))
    idx = np.flatnonzero(np.diff(np.concatenate([[0], cand.astype(np.int8), [0]])))
    dropped = 0
    for i0, i1 in zip(idx[0::2], idx[1::2]):
        if i1 - i0 < min_len:
            cand[i0:i1] = False
        elif _db(float(np.mean(10.0 ** (levels_db[i0:i1] / 10.0)))) < ref - REACT_MIN_MEAN_REL_DB:
            cand[i0:i1] = False       # too faint to be laughter or applause: the tail of a spoken phrase, a breath, a trace of a word
            dropped += 1
    return cand, speech, {"ref_db": round(ref, 1), "thr_db": round(thr, 1), "faint_dropped": dropped}


def reaction_gain(mask, speech):
    """Gain curve (0..1, one value per 10 ms) for the reaction layer. Every reaction starts with a short fade in and
    ENDS WITH A GRADUAL FADE OUT that reaches exactly zero at its last frame: a laugh or an applause is never cut off.
    The natural tail (REACT_TAIL_SEC) is kept where no word follows; the fade never runs into a spoken word."""
    n = mask.size
    g = np.zeros(n, dtype=np.float64)
    idx = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(np.int8), [0]])))
    tail = int(round(REACT_TAIL_SEC / FRAME_SEC))
    for i0, i1 in zip(idx[0::2], idx[1::2]):
        e = i1
        while e < n and e - i1 < tail and not speech[e] and not mask[e]:
            e += 1
        L = e - i0
        if L < 3:
            continue
        seg = np.ones(L)
        fin = max(1, min(int(round(REACT_FADE_IN_SEC / FRAME_SEC)), L // 3))
        seg[:fin] = np.sin(0.5 * np.pi * np.linspace(0.0, 1.0, fin, endpoint=False)) ** 2
        fout = max(2, min(int(round(REACT_FADE_OUT_SEC / FRAME_SEC)), int(L * REACT_FADE_SHARE)))
        seg[L - fout:] *= np.cos(0.5 * np.pi * np.linspace(0.0, 1.0, fout)) ** 2     # 1 -> exactly 0 at the last frame
        g[i0:e] = np.maximum(g[i0:e], seg)
    return g


def _smooth_gain(target, rise_tau, fall_tau):
    """One-pole smoothing of a 0..1 curve (10 ms steps): rises with rise_tau, falls with fall_tau."""
    a_up = 1.0 - np.exp(-FRAME_SEC / rise_tau)
    a_dn = 1.0 - np.exp(-FRAME_SEC / fall_tau)
    g = np.empty_like(target, dtype=np.float64)
    cur = 0.0
    for i in range(target.size):
        t = target[i]
        cur += (t - cur) * (a_up if t > cur else a_dn)
        g[i] = cur
    return g


def build_reaction_layer(vocals_path, spans, dub_path, out_path, gain_db=None, min_level_db=None):
    """Writes `out_path` (16-bit stereo WAV, 44.1 kHz): the human sounds without words that the
    separator put in the voices (laughter, applause, cheers), at their original place and level,
    lowered while the dubbed voice speaks. spans = [(start, end)] of every recognised spoken word
    (seconds). min_level_db: only sounds louder than this are taken (used when a steady ambience bed already
    carries the quiet room sound). Returns {"ok", "reason", "regions", "seconds", "share", "level_db"}; never raises."""
    info = {"ok": False, "reason": "", "regions": 0, "seconds": 0.0, "share": 0.0, "level_db": None}
    out_path = Path(out_path)
    part = out_path.with_name(out_path.name + ".part.wav")
    dec = enc = None
    try:
        if not REACT_ENABLED:
            info["reason"] = "switched off"
            return info
        vocals_path = Path(vocals_path)
        if not vocals_path.exists() or vocals_path.stat().st_size < 1000:
            info["reason"] = "no separated voices file"
            return info
        if not spans:
            info["reason"] = "no speech map (nothing to tell words from other human sounds)"
            return info
        spans = merge_spans(spans)
        levels = _voice_levels_db(vocals_path)
        if levels.size < 300:
            info["reason"] = "the separated voices track is too short"
            return info
        mask, speech, det = reaction_frames(levels, spans, min_level_db)
        secs = float(mask.sum()) * FRAME_SEC
        share = float(mask.mean())
        edges = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(np.int8), [0]])))
        info.update({"regions": int(len(edges) // 2), "seconds": round(secs, 1), "share": round(share, 3)})
        if secs < REACT_MIN_TOTAL_SEC:
            info["reason"] = "no laughter, applause or cheering found outside the words"
            return info
        if share > REACT_MAX_SHARE:
            info["reason"] = (f"{share * 100:.0f}% of the video would be affected, more than audience sound "
                              f"could be: not used")
            return info
        info["level_db"] = _db(float(np.mean(10.0 ** (levels[mask] / 10.0))))
        g = reaction_gain(mask, speech)
        # lowered while the dubbed voice speaks
        duck_note = "no ducking"
        if dub_path is not None and Path(dub_path).exists() and REACT_DUCK_DB > 0.5:
            dlev = _voice_levels_db(dub_path)
            gd, dshare, _m = speech_gain_curve(dlev, REACT_DUCK_DB)
            if gd.size:
                m = min(g.size, gd.size)
                g[:m] *= gd[:m]
                duck_note = f"lowered {REACT_DUCK_DB:g} dB while the dubbed voice speaks ({dshare * 100:.0f}% of the time)"
        gain_lin = 10.0 ** ((REACT_GAIN_DB if gain_db is None else float(gain_db)) / 20.0)
        centers = (np.arange(g.size) + 0.5) * (HOP * RATE / ANALYSIS_RATE)
        dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(vocals_path), "-vn", "-af", "pan=mono|c0=0.5*c0+0.5*c1", "-ar", str(RATE),
                                "-f", "f32le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", str(CH), "-i", "-",
                                "-c:a", "pcm_s16le", str(part)], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        pos, left = 0, b""
        while True:
            buf = dec.stdout.read(CHUNK * 4)
            if not buf:
                break
            buf = left + buf
            n = (len(buf) // 4) * 4
            left = buf[n:]
            if not n:
                continue
            x = np.frombuffer(buf[:n], dtype="<f4").astype(np.float64)
            gg = np.interp(np.arange(pos, pos + x.size), centers, g) * gain_lin
            y = np.clip(x * gg, -1.0, 1.0).astype("<f4")
            enc.stdin.write(np.repeat(y[:, None], CH, axis=1).tobytes())
            pos += x.size
        dec.stdout.close()
        dec.wait()
        enc.stdin.close()
        enc.wait()
        if dec.returncode not in (0, None) or enc.returncode != 0 or pos == 0 or not part.exists() or part.stat().st_size < 1000:
            info["reason"] = "ffmpeg failed while building the reaction layer"
            return info
        os.replace(part, out_path)
        info["ok"] = True
        info["reason"] = (f"{info['regions']} stretches, {info['seconds']:g}s ({share * 100:.0f}% of the video) of human sound "
                          f"without words put back, level {info['level_db']} dB; {duck_note}; "
                          f"speech level {det.get('ref_db')} dB, threshold {det.get('thr_db')} dB")
        return info
    except Exception as ex:
        info["ok"] = False
        info["reason"] = f"skipped ({ex})"[:200]
        return info
    finally:
        for pr in (dec, enc):
            try:
                if pr is not None and pr.poll() is None:
                    pr.kill()
            except Exception:
                pass
        try:
            if part.exists():
                part.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------- short dubbing: one call for the whole background

SHORT_MIX_FILTER = "volume=1.0"      # what ffmpeg_utils.mix_two_audio does to the background (nothing: its level comes from the original)


def prepare_background(bg_path, vocals_path, original, work_dir, tag, seed=0):
    """Background track for the short dubbing (used by the merge step and by the lip-sync step).
    Rebuilds a steady background sound that the separator filed under "voices" (crowd, machine hum, traffic,
    rain ...) when it is clearly missing; the separated background is lowered while people speak as before.
    Returns {"path": file to mix, "temps": files to delete afterwards, "note": text for the log,
    "bed": True/False, "ducked": True/False}. Never raises; on any problem "path" is the given background."""
    res = {"path": Path(bg_path), "temps": [], "note": "", "bed": False, "ducked": False}
    work_dir = Path(work_dir)
    try:
        ducked = work_dir / f"bg_ducked_{tag}.wav"
        restored = work_dir / f"bg_restored_{tag}.wav"
        lifted = work_dir / f"bg_restored_lifted_{tag}.wav"
        muted = work_dir / f"bg_muted_{tag}.wav"
        res["temps"] = [ducked, restored, lifted, muted]
        vocals_ok = vocals_path is not None and Path(vocals_path).exists()
        # 1. is a steady background sound missing? (needs the pauses between the voices)
        plan, why = None, "not tried"
        pauses = None
        if BED_ENABLED and vocals_ok and original is not None and Path(original).exists():
            try:
                import ffmpeg_utils
                gaps = ffmpeg_utils.detect_silence_gaps(vocals_path, min_silence_sec=0.6, noise_db="-30dB")
                pauses = [(float(g["start"]), float(g["end"])) for g in gaps]
                plan, why = plan_ambience_bed(original, bg_path, vocals_path, pauses)
            except Exception as ex:
                plan, why = None, f"skipped ({ex})"[:160]
        else:
            why = "switched off" if not BED_ENABLED else "no original audio or separated voices to compare"
        # 2. silence the separated background while the original voices speak (it holds a faint copy of them);
        #    otherwise (switched off) lower its voice range while people speak
        sep_used = Path(bg_path)
        mute_done = False
        if vocals_ok:
            spans_ = None
            try:
                import json
                sf = Path(vocals_path).parent / "speech_spans.json"
                if sf.exists():
                    spans_ = [(float(x[0]), float(x[1])) for x in json.loads(sf.read_text(encoding="utf-8"))]
            except Exception:
                spans_ = None
            mres = mute_speech(bg_path, vocals_path, muted, spans=spans_)
            if mres["muted"] and muted.exists() and muted.stat().st_size > 1000:
                sep_used, mute_done = muted, True
                res["muted"] = True
            res["note"] = "mute: " + str(mres["reason"])
        if ENABLED and vocals_ok and not mute_done:
            d = duck_background(bg_path, vocals_path, ducked)
            res["ducked"] = bool(d.get("ducked")) and ducked.exists() and ducked.stat().st_size > 1000
            if res["ducked"]:
                sep_used = ducked
            res["note"] += ("; " if res["note"] else "") + "duck: " + str(d.get("reason"))
        res["path"] = sep_used
        # 3. lay the rebuilt sound under the whole video
        if plan:
            info = add_ambience_bed(plan, sep_used, restored, seed=seed)
            if info["ok"] and restored.exists() and restored.stat().st_size > 1000:
                res["path"], res["bed"] = restored, True
                res["bed_level"] = plan.get("target")
                g, _n = makeup_gain(original, restored, pauses, max_db=6.0, mix_filter=SHORT_MIX_FILTER, min_db=1.0)
                if g > 0 and lift_background(restored, lifted, g):
                    res["path"] = lifted
                res["note"] += f"; bed: {info['reason']}; level correction {g:g} dB"
            else:
                res["note"] += "; bed: could not be built (" + info["reason"] + ")"
        else:
            res["note"] += "; bed: not used (" + str(why) + ")"
            # No bed: set the background's level from the ORIGINAL audio (not a fixed factor). Where the voices
            # pause, the original holds only music / ambience; raise the separated background until it is as
            # loud as the original there (never lowered, capped by BG_MAKEUP_MAX_DB, ignored below 1 dB).
            if pauses and vocals_ok:
                g, mnote = makeup_gain(original, Path(bg_path), pauses, mix_filter=SHORT_MIX_FILTER, min_db=1.0)
                if g > 0 and lift_background(sep_used, lifted, g):
                    res["path"] = lifted
                res["note"] += f"; level: {mnote}"
        return res
    except Exception as ex:
        res["path"] = Path(bg_path)
        res["note"] = f"skipped ({ex})"[:200]
        return res


def prepare_reactions(vocals_path, spans_path, dub_path, work_dir, tag, bed_level=None):
    """Short dubbing: the laughter / applause / cheering layer (see build_reaction_layer).
    spans_path = the speech map written when the video was transcribed (where words are spoken).
    Returns {"path": wav to mix or None, "temps": files to delete afterwards, "note": text for the log}.
    Never raises; "path" is None whenever there is nothing to add (no map, no such sounds, switched off)."""
    res = {"path": None, "temps": [], "note": ""}
    try:
        if not REACT_ENABLED:
            res["note"] = "reactions: switched off"
            return res
        if spans_path is None or not Path(spans_path).exists():
            res["note"] = "reactions: not used (no speech map for this job)"
            return res
        import json
        spans = [(float(x[0]), float(x[1])) for x in json.loads(Path(spans_path).read_text(encoding="utf-8"))]
        out = Path(work_dir) / f"reactions_{tag}.wav"
        res["temps"] = [out, out.with_name(out.name + ".part.wav")]
        info = build_reaction_layer(vocals_path, spans, dub_path, out,
                                    min_level_db=(float(bed_level) + 8.0) if bed_level is not None else None)
        res["note"] = "reactions: " + str(info["reason"])
        if info["ok"] and out.exists() and out.stat().st_size > 1000:
            res["path"] = out
        return res
    except Exception as ex:
        res["note"] = f"reactions: skipped ({ex})"[:200]
        return res
