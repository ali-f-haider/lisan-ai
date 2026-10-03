"""Room sound: measures how reverberant the ORIGINAL recording is, phrase by phrase, groups the phrases into rooms, and
gives every dubbed (dry) Arabic line the room it was originally spoken in.

Why: a text-to-speech voice is completely dry. In the original a man speaking in a big hall is heard with the hall
(his voice keeps ringing for a moment after each phrase); a man speaking outdoors is not; and one video can switch
between several places. A dry Arabic voice laid over a hall scene sounds pasted on.

How it measures (blind estimate, from the separated voices of the original + the word timing, no test signal needed):
  * at the end of every phrase (a word followed by a pause) it follows how fast the voice level falls away afterwards
    (the room's decay time, RT60) and how loud that tail is compared with the voice (the wet/dry ratio);
    a voice that stops dead is a "dry" phrase;
  * the phrases are grouped by those two numbers into rooms (agglomerative clustering, small noisy groups are merged
    into the nearest one, isolated flips in time are smoothed away);
  * every line of the dub gets the room of the phrases around the moment it was spoken in the original.

How it renders: for every room a synthetic tail (noise that decays at the measured RT60, high frequencies faster, a
different tail for left and right) is convolved with the lines of that room and added to the dub. The loudness of every
line is kept as it was (the sliders in Step 5.5 matched it to the original already). Audio is processed in blocks, so
long dubs need little memory.

Everything here is best effort and never raises into the caller: on any problem the dub is left as it is.
Switch off globally with ROOM_FX=0.
"""
import json
import os
import subprocess
from pathlib import Path

import numpy as np

ENABLED = os.environ.get("ROOM_FX", "1").strip() not in ("0", "false", "False", "")

ANALYSIS_RATE = 16000
HOP = 160                          # 10 ms
FRAME = HOP / ANALYSIS_RATE
OUT_RATE = 44100

MIN_GAP_SEC = 0.45                 # a word followed by at least this much silence is a usable phrase end
LOOK_BEFORE = 0.25                 # speech level is measured this long before the end
FIT_MIN_DROP_DB = 12.0             # a decay must show at least this fall to give a reliable slope
FIT_MAX_DROP_DB = 32.0
SPEECH_OFFSET_BIAS_SEC = 0.15      # even in a dead room the voice itself needs this long to "ring down"
DRY_RT_SEC = 0.12                  # below this (after the bias) a phrase counts as dry
DRY_WET_DB = -26.0                 # ...and so does one whose tail is quieter than this
RT_MIN, RT_MAX = 0.15, 3.5
WET_MIN_DB, WET_MAX_DB = -34.0, -3.0
TAIL_MIN_R2 = 0.6                  # a tail must follow a straight fall in dB at least this well
TAIL_MAX_REBOUND_DB = 8.0          # ...and must not rise more than this above its own lowest point so far
MIN_EVENTS_OK = 4                  # a room backed by fewer phrases is applied at reduced strength
PROFILE_VERSION = 4                # a cached measurement made by an older version is measured again
WET_BIAS_DB = 5.0                  # separated real speech reads this much "wetter" than it sounds (set by ear on a real hall recording)
RENDER_TRIM_DB = 1.0               # measured back, a rendered tail reads about 1 dB lighter than the wet level asked for
CLUSTER_DIST = 1.3                 # phrases closer than this (in scaled rt / wet units) are the same room
CLUSTER_RT_SCALE = 0.35            # ln(rt) units
CLUSTER_WET_SCALE = 6.0            # dB
CLUSTER_MAX_POINTS = 3000
SMOOTH_WINDOW = 5                  # a room must hold for this many phrase ends in a row (majority vote over the labels)
BLOCK_SEC = 30.0                   # audio is rendered in blocks of this length
FADE_SEC = 0.015                   # a line's room is faded in/out this long at the edges of the line


def _db(x):
    return 10.0 * np.log10(np.maximum(x, 1e-12))


def _power_frames(path):
    """Mean power per 10 ms of a file, mono 16 kHz (linear), streamed so long files need little memory."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(ANALYSIS_RATE), "-f", "f32le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out, left, fb = [], b"", HOP * 4
    try:
        while True:
            buf = p.stdout.read(fb * 4000)
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
    pw = np.concatenate(out) if out else np.zeros(0)
    return pw if pw.size >= 100 else np.zeros(0)


# ---------------------------------------------------------------- measuring: one reading per phrase end

def _phrase_events(p, spans):
    """-> (events, candidates). Event = {"t": seconds, "dry": bool, "rt": observed decay time or None, "w": wet level dB or None}."""
    pdb = _db(p)
    sm = np.convolve(pdb, np.ones(3) / 3.0, mode="same")           # 30 ms smoothing
    spans = sorted((float(a), float(z)) for a, z in spans if z > a)
    noise_floor = float(np.percentile(pdb, 5))
    events, cands = [], 0
    for i, (a, z) in enumerate(spans):
        nxt = spans[i + 1][0] if i + 1 < len(spans) else p.size * FRAME
        if nxt - z < MIN_GAP_SEC or (z - a) < 0.08:
            continue
        k_end = int(round(z / FRAME))
        k_next = int(nxt / FRAME)
        if k_end + 30 >= p.size or k_end < 30:
            continue
        lo = max(0, k_end - int(LOOK_BEFORE / FRAME))
        if k_end + 3 - lo < 10:
            continue
        speech_lin = float(np.max(np.convolve(p[lo:k_end + 3], np.ones(10) / 10.0, mode="valid")))
        if speech_lin <= 0:
            continue
        speech_db = float(_db(speech_lin))
        if speech_db < noise_floor + 25.0:          # a mumbled, nearly silent word: no good for measuring
            continue
        # the real stop of the voice: first frame after (end - 50 ms) that has fallen 6 dB under the speech level
        k0 = None
        for k in range(max(lo, k_end - 5), min(k_next, k_end + 30)):
            if sm[k] < speech_db - 6.0:
                k0 = k
                break
        if k0 is None:
            continue
        cands += 1
        lvl0 = sm[k0]
        stop = min(k_next - 5, k0 + 120)
        floor = noise_floor + 6.0
        ks, vs = [], []
        k_end_of_scan = [k0]
        for k in range(k0, stop):
            if sm[k] < floor or sm[k] < lvl0 - FIT_MAX_DROP_DB:
                break
            ks.append(k)
            vs.append(sm[k])
            k_end_of_scan[0] = k + 1
        if len(ks) < 8 or (vs[0] - vs[-1]) < FIT_MIN_DROP_DB:
            if ks and k_end_of_scan[0] >= stop and (vs[0] - vs[-1]) < FIT_MIN_DROP_DB:
                continue                       # the sound goes on instead of dying away (laughter, music, noise): no evidence either way
            events.append({"t": z, "dry": True, "rt": None, "w": None})       # the voice stops at once
            continue
        t = (np.array(ks) - ks[0]) * FRAME
        va = np.array(vs)
        slope, icpt = np.polyfit(t, va, 1)                         # dB per second (negative)
        if slope >= -5.0:
            continue
        # A room tail falls steadily. If the "tail" bounces back up (laughter, applause, the next word, music) or does not
        # follow a straight fall in dB, it is not a room tail and must not be read as one.
        ss_tot = float(np.sum((va - va.mean()) ** 2))
        r2 = 1.0 - float(np.sum((va - (slope * t + icpt)) ** 2)) / ss_tot if ss_tot > 0 else 0.0
        rebound = float(np.max(va - np.minimum.accumulate(va)))
        if r2 < TAIL_MIN_R2 or rebound > TAIL_MAX_REBOUND_DB:
            continue
        rt = 60.0 / -slope
        t1, t2 = k0 + 12, min(k0 + 25, k_next - 3)
        if t2 - t1 < 5:
            continue
        tail_db = float(_db(np.mean(p[t1:t2]))) - speech_db
        # steady-state reverb level relative to the voice, from the tail level and the decay rate
        # (the tail is measured ~0.185 s after the stop and falls 60 dB per rt60)
        w_db = tail_db + min(15.0, 60.0 / max(rt, 0.1) * 0.185 * 0.85)
        dry = (rt - SPEECH_OFFSET_BIAS_SEC) < DRY_RT_SEC or w_db < DRY_WET_DB
        events.append({"t": z, "dry": bool(dry), "rt": float(rt), "w": float(w_db)})
    return events, cands



INFLATE_WINDOW = 9                 # a phrase is compared with its nearest ringing neighbours in time
INFLATE_RT_RATIO = 1.5             # ...and is not trusted if its decay is this much longer than their lower quartile
INFLATE_W_DB = 6.0                 # ...or its tail this much louder than their lower quartile


def _drop_inflated(events):
    """Anything that shares the sound after a phrase (laughter, applause, a reply, music) can only make that tail
    look longer and louder than the room really is, never shorter. A phrase whose reading is far above the lower
    quartile of its neighbours in time is therefore read as contaminated and ignored. A real change of room is not
    lost: it shows up in the neighbours too, and only the first phrases after the change can be dropped.
    -> (kept events, number dropped)"""
    ring = [i for i, e in enumerate(events) if not e["dry"] and e["rt"] is not None]
    if len(ring) < 5:
        return events, 0
    rt = np.array([events[i]["rt"] for i in ring])
    w = np.array([events[i]["w"] for i in ring])
    bad = set()
    half = INFLATE_WINDOW // 2
    for k, i in enumerate(ring):
        lo = max(0, min(k - half, len(ring) - INFLATE_WINDOW))
        sl = slice(lo, lo + INFLATE_WINDOW)
        if rt[k] > INFLATE_RT_RATIO * np.percentile(rt[sl], 25) or w[k] > np.percentile(w[sl], 25) + INFLATE_W_DB:
            bad.add(i)
    return [e for i, e in enumerate(events) if i not in bad], len(bad)


# ---------------------------------------------------------------- grouping the phrases into rooms

def _features(events):
    X = np.zeros((len(events), 2))
    for i, e in enumerate(events):
        if e["dry"]:
            rt_eff, w = 0.12, WET_MIN_DB
        else:
            rt_eff = max(e["rt"] - SPEECH_OFFSET_BIAS_SEC, 0.12)
            w = float(np.clip(e["w"], WET_MIN_DB, WET_MAX_DB))
        X[i] = (np.log(rt_eff) / CLUSTER_RT_SCALE, w / CLUSTER_WET_SCALE)
    return X


def _cluster_labels(events):
    """Room number for every phrase (events are sorted by time)."""
    n = len(events)
    if n == 0:
        return np.zeros(0, dtype=int)
    X = _features(events)
    if n < 3:
        return np.zeros(n, dtype=int)
    from scipy.cluster.hierarchy import linkage, fcluster
    if n > CLUSTER_MAX_POINTS:
        pick = np.linspace(0, n - 1, CLUSTER_MAX_POINTS).astype(int)
        lab_p = fcluster(linkage(X[pick], "average"), t=CLUSTER_DIST, criterion="distance") - 1
        cent = {c: X[pick][lab_p == c].mean(axis=0) for c in np.unique(lab_p)}
        keys = list(cent)
        lab = np.array([keys[int(np.argmin([np.sum((x - cent[k]) ** 2) for k in keys]))] for x in X])
    else:
        lab = fcluster(linkage(X, "average"), t=CLUSTER_DIST, criterion="distance") - 1
    ids, counts = np.unique(lab, return_counts=True)
    min_sz = max(2, int(0.03 * n))
    big = [int(i) for i, c in zip(ids, counts) if c >= min_sz] or [int(ids[int(np.argmax(counts))])]
    cent = {i: X[lab == i].mean(axis=0) for i in big}
    out = lab.copy()
    for idx in range(n):
        if int(lab[idx]) not in cent:
            out[idx] = min(big, key=lambda i: float(np.sum((X[idx] - cent[i]) ** 2)))
    # a room has to hold for a few phrases in a row: majority vote over a short window
    half = SMOOTH_WINDOW // 2
    sm = out.copy()
    for idx in range(n):
        win = out[max(0, idx - half): idx + half + 1]
        vals, cnt = np.unique(win, return_counts=True)
        top = cnt.max()
        winners = vals[cnt == top]
        sm[idx] = out[idx] if out[idx] in winners else winners[0]
    return sm


def _room_name(rt):
    return "small room" if rt < 0.6 else ("room" if rt < 1.1 else ("large room / hall" if rt < 2.0 else "very large space"))


def analyze(vocals_path, spans):
    """-> profile dict (see module docstring).  Top-level label / rt60 / wet_db / confidence describe the main room."""
    prof = {"ok": False, "version": PROFILE_VERSION, "label": "unknown", "rt60": None, "wet_db": None, "events": 0,
            "confidence": "none", "note": "", "groups": [], "regions": []}
    try:
        p = _power_frames(vocals_path)
        if p.size == 0:
            prof["note"] = "no audio"
            return prof
        if len(spans) < 3:
            prof["note"] = "too few words to measure"
            return prof
        events, cands = _phrase_events(p, spans)
        events.sort(key=lambda e: e["t"])
        events, prof["dropped"] = _drop_inflated(events)
        prof["events"] = len(events)
        prof["candidates"] = cands
        prof["detail"] = [{"t": round(e["t"], 1), "dry": bool(e["dry"]),
                           "rt": None if e["rt"] is None else round(e["rt"], 2),
                           "w": None if e["w"] is None else round(e["w"], 1)} for e in events[:80]]
        if not events:
            prof["note"] = "no usable phrase ends (speech runs on without pauses or the voice stem is too quiet)"
            return prof
        labels = _cluster_labels(events)
        # rooms in order of first appearance
        order = []
        for l in labels:
            if int(l) not in order:
                order.append(int(l))
        remap = {old: new for new, old in enumerate(order)}
        labels = np.array([remap[int(l)] for l in labels])
        groups = []
        for g in range(len(order)):
            mem = [e for e, l in zip(events, labels) if l == g]
            wet = [e for e in mem if not e["dry"]]
            dry_group = len(wet) * 3 < len(mem)          # a room counts as dry only if fewer than a third of its phrases ring
            gi = {"id": g, "n": len(mem), "dry": dry_group, "confidence": "ok" if len(mem) >= MIN_EVENTS_OK else "low"}
            if dry_group:
                gi.update(label="dry", rt60=None, wet_db=None)
            else:
                # The decay of a phrase is measured short when the next sound cuts it off or the voice fades by itself, never
                # much too long: with few phrases the longest reading is taken, with more the upper quartile.
                rts = [e["rt"] for e in wet]
                # Whatever else sounds after a phrase (laughter, music, a reply) only lengthens and loudens its tail, so
                # the honest reading is the low one: the shortest when little is measured, the lower quartile otherwise.
                ws_ = [e["w"] for e in wet]
                rt_obs = float(min(rts)) if len(rts) < 4 else float(np.percentile(rts, 25))
                w = (float(min(ws_)) if len(ws_) < 4 else float(np.median(ws_))) - WET_BIAS_DB
                # the tail we render is measured back with the same method, so ask for the observed decay time
                # (a rendered tail reads about 6 % shorter than its nominal rt60)
                gi.update(label=_room_name(max(rt_obs - SPEECH_OFFSET_BIAS_SEC, 0.0)),
                          rt60=round(float(np.clip(rt_obs / 0.94, RT_MIN, RT_MAX)), 2),
                          wet_db=round(float(np.clip(w, WET_MIN_DB, WET_MAX_DB)), 1))
            groups.append(gi)
        # regions of time: boundaries halfway between two phrases that belong to different rooms
        regions = []
        t0, cur = 0.0, int(labels[0])
        for i in range(1, len(events)):
            if int(labels[i]) != cur:
                tb = (events[i - 1]["t"] + events[i]["t"]) / 2.0
                regions.append([round(t0, 2), round(tb, 2), cur])
                t0, cur = tb, int(labels[i])
        regions.append([round(t0, 2), 1e9, cur])
        prof["groups"], prof["regions"] = groups, regions
        main = max(groups, key=lambda x: x["n"])
        prof.update(ok=True, label=main["label"], rt60=main["rt60"], wet_db=main["wet_db"], confidence=main["confidence"])
        if all(g["dry"] for g in groups):
            prof["note"] = "the original is dry (little or no room sound): nothing is added"
        return prof
    except Exception as ex:
        prof["note"] = f"could not measure ({ex})"[:200]
        return prof


estimate = analyze


def group_at(profile, t):
    """Room number (index into profile['groups']) at second t of the original."""
    regs = profile.get("regions") or []
    for a, z, g in regs:
        if a <= t < z:
            return int(g)
    return int(regs[-1][2]) if regs else 0


# ---------------------------------------------------------------- rendering

def _impulse_response(rt60, wet_db, rate=OUT_RATE, seed=1234):
    """Stereo room tail (2, n). Energy of each channel's response = 10^(wet_db/10) (the wet/dry energy ratio)."""
    from scipy.signal import butter, sosfilt
    rt60 = float(np.clip(rt60, RT_MIN, RT_MAX))
    n = int(rate * min(rt60 * 1.15 + 0.05, 4.5))
    t = np.arange(n) / rate
    # octave-ish bands: higher bands die away faster, like air and soft surfaces do in real rooms
    bands = [((None, 500.0), 1.00), ((500.0, 2000.0), 0.85), ((2000.0, 6000.0), 0.60), ((6000.0, None), 0.38)]
    pre = int(0.012 * rate)
    ir = np.zeros((2, n))
    for ch in range(2):
        rng = np.random.default_rng(seed + 977 * ch)
        for (lo, hi), k in bands:
            noise = rng.standard_normal(n)
            if lo is None:
                sos = butter(2, hi / (rate / 2), btype="low", output="sos")
            elif hi is None:
                sos = butter(2, lo / (rate / 2), btype="high", output="sos")
            else:
                sos = butter(2, [lo / (rate / 2), hi / (rate / 2)], btype="band", output="sos")
            env = np.exp(-6.9078 * t / max(rt60 * k, 0.05))
            ir[ch] += sosfilt(sos, noise) * env
        ir[ch, :pre] = 0.0
        on = int(0.008 * rate)                       # soft onset (no click)
        ir[ch, pre:pre + on] *= np.linspace(0.0, 1.0, on)
        e = float(np.sum(ir[ch] ** 2))
        if e > 0:
            ir[ch] *= np.sqrt(10.0 ** (wet_db / 10.0) / e)
    return ir


def _mask_for_block(windows, b0, n, rate):
    """0..1 mask (n,) for the samples [b0, b0+n): 1 inside the windows (seconds), cosine fades at their edges."""
    m = np.zeros(n)
    f = max(2, int(FADE_SEC * rate))
    for (t0, t1) in windows:
        s0, s1 = int(t0 * rate) - b0, int(min(t1, 1e7) * rate) - b0
        if s1 <= 0 or s0 >= n or s1 - s0 < 4:
            continue
        a, z = max(s0, 0), min(s1, n)
        seg = np.ones(z - a)
        ff = min(f, (s1 - s0) // 2)
        if s0 >= 0 and ff > 1:                       # fade in only where the window really starts inside this block
            k = min(ff, z - a)
            seg[:k] = np.sin(np.linspace(0, np.pi / 2, k)) ** 2
        if s1 <= n and ff > 1:
            k = min(ff, z - a)
            seg[-k:] = np.minimum(seg[-k:], np.cos(np.linspace(0, np.pi / 2, k)) ** 2)
        m[a:z] = np.maximum(m[a:z], seg)
    return m


def render_groups(dry_path, out_wav, plan, rate=OUT_RATE):
    """dry_path (any audio) -> out_wav (16-bit stereo 44.1 kHz WAV): every group in `plan` gets its own room on its lines.
    plan = [{"rt60": s, "wet_db": dB, "windows": [(t0, t1), ...]}, ...] (seconds on the dry track's timeline).
    Same length as the input, same loudness line by line. Returns True on success."""
    import soundfile as sf
    from scipy.signal import oaconvolve
    out_wav = Path(out_wav)
    part = Path(str(out_wav) + ".part.wav")
    cmd = ["ffmpeg", "-v", "error", "-i", str(dry_path), "-vn", "-ac", "2", "-ar", str(rate), "-f", "f32le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    irs = []
    for g in plan:
        w = sorted((float(a), float(z)) for a, z in g["windows"])
        irs.append((_impulse_response(g["rt60"], g["wet_db"], rate), w, 10.0 ** (g["wet_db"] / 10.0)))
    block = int(BLOCK_SEC * rate)
    bytes_block = block * 2 * 4
    carry = np.zeros((2, 0))
    done = 0
    try:
        with sf.SoundFile(str(part), mode="w", samplerate=rate, channels=2, subtype="PCM_16", format="WAV") as fo:
            left = b""
            while True:
                buf = proc.stdout.read(bytes_block)
                if not buf and not left:
                    break
                buf = left + buf
                n_frames = (len(buf) // 8)
                if n_frames == 0:
                    break
                left = buf[n_frames * 8:]
                x = np.frombuffer(buf[:n_frames * 8], dtype="<f4").astype(np.float64).reshape(-1, 2).T
                n = x.shape[1]
                out = np.zeros_like(x)
                new_carry = np.zeros((2, max(carry.shape[1] - n, 0)))
                if carry.shape[1]:
                    k = min(n, carry.shape[1])
                    out[:, :k] += carry[:, :k]
                    new_carry[:, :carry.shape[1] - k] += carry[:, k:]
                masks = []
                tot_mask = np.zeros(n)
                for (ir, wins, q) in irs:
                    near = [(a, z) for (a, z) in wins if z * rate > done and a * rate < done + n]
                    if not near:
                        masks.append(None)
                        continue
                    m = _mask_for_block(near, done, n, rate)
                    masks.append(m)
                    tot_mask += m
                over = np.maximum(tot_mask, 1.0)
                passthrough = np.clip(1.0 - tot_mask, 0.0, 1.0)
                out += x * passthrough
                for gi, (ir, wins, q) in enumerate(irs):
                    m = masks[gi]
                    if m is None:
                        continue
                    m = m / over
                    xs = x * m
                    y = np.stack([oaconvolve(xs[0], ir[0]), oaconvolve(xs[1], ir[1])])
                    yy = y[:, :n]
                    inside = m > 0.5
                    e_dry = float(np.sum(xs[:, inside] ** 2))
                    e_tot = float(np.sum((xs + yy)[:, inside] ** 2))
                    c = float(np.sqrt(e_dry / e_tot)) if e_dry > 1e-6 and e_tot > 0 else 1.0 / np.sqrt(1.0 + 1.3 * q)
                    c = float(np.clip(c, 0.5, 1.0))
                    out += c * (xs + yy)
                    if y.shape[1] > n:
                        tail = c * y[:, n:]
                        if tail.shape[1] > new_carry.shape[1]:
                            new_carry = np.pad(new_carry, ((0, 0), (0, tail.shape[1] - new_carry.shape[1])))
                        new_carry[:, :tail.shape[1]] += tail
                carry = new_carry
                fo.write(np.clip(out.T, -0.99, 0.99))
                done += n
        os.replace(part, out_wav)
        return True
    except Exception as ex:
        print(f"[room] render failed: {ex}")
        return False
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        proc.wait()
        try:
            if part.exists():
                part.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------- per-job glue

def _stem_files(job_id):
    from media_paths import job_background_audio
    bg = job_background_audio(job_id)
    if bg is None:
        return None, None
    return bg.parent / "vocals.wav", bg.parent / "speech_spans.json"


def profile_for_job(job_id, cache_dir, vocals=None, spans_file=None):
    """Measured rooms of this job's original (cached in cache_dir/<job>_room2.json).
    vocals / spans_file default to the short-dub stems of the job."""
    cache = Path(cache_dir) / f"{job_id}_room2.json"
    try:
        if cache.exists():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if int(cached.get("version") or 0) >= PROFILE_VERSION:
                return cached
    except Exception:
        pass
    prof = {"ok": False, "version": PROFILE_VERSION, "label": "unknown", "rt60": None, "wet_db": None, "events": 0, "confidence": "none",
            "note": "the separated voices of the original are not available", "groups": [], "regions": []}
    try:
        if vocals is None or spans_file is None:
            vocals, spans_file = _stem_files(job_id)
        if vocals is not None and Path(vocals).exists() and spans_file is not None and Path(spans_file).exists():
            spans = [(float(x[0]), float(x[1])) for x in json.loads(Path(spans_file).read_text(encoding="utf-8"))]
            prof = analyze(vocals, spans)
        elif vocals is not None and Path(vocals).exists():
            prof["note"] = "no word timing was kept for this job"
    except Exception as ex:
        prof["note"] = f"could not measure ({ex})"[:200]
    try:
        cache.write_text(json.dumps(prof), encoding="utf-8")
    except Exception:
        pass
    return prof


def make_plan(profile, settings, lines):
    """-> (plan, assignment, why).
    plan = groups to render [{"rt60","wet_db","windows"}]; assignment = {sid: room number};
    lines = [{"sid", "t0", "t1" (where the line plays in the dub), "orig_mid" (when it was spoken in the original)}].
    An empty plan means 'add nothing'."""
    mode = str((settings or {}).get("mode") or "auto").lower()
    lines = list(lines or [])
    if not ENABLED:
        return [], {}, "switched off (ROOM_FX=0)"
    if mode == "off":
        return [], {}, "off"
    if mode == "manual":
        try:
            rt = float(np.clip(float(settings.get("rt60")), RT_MIN, RT_MAX))
            wd = float(np.clip(float(settings.get("wet_db")), WET_MIN_DB, WET_MAX_DB))
            wins = [(l["t0"], l["t1"]) for l in lines] or [(0.0, 1e9)]
            return [{"rt60": rt, "wet_db": wd + RENDER_TRIM_DB, "windows": wins}], {l["sid"]: 0 for l in lines}, \
                f"manual: reverb {rt:g} s, amount {wd:g} dB on every line"
        except Exception:
            mode = "auto"
    if not profile or not profile.get("ok") or not profile.get("groups"):
        return [], {}, "auto: " + str((profile or {}).get("note") or "room could not be measured")
    groups = profile["groups"]
    try:      # "Match original" with a correction: the user found the measured amount too strong / too weak
        trim = float(np.clip(float((settings or {}).get("trim_db") or 0.0), -12.0, 12.0))
    except (TypeError, ValueError):
        trim = 0.0
    scenes = (settings or {}).get("scenes") or None      # what kind of place each stretch is (scene_context.py): only ever a ceiling
    assignment, per_group, mids = {}, {}, {}
    for l in lines:
        g = group_at(profile, float(l["orig_mid"]))
        assignment[l["sid"]] = g
        per_group.setdefault(g, []).append((float(l["t0"]), float(l["t1"])))
        mids.setdefault(g, []).append(float(l["orig_mid"]))
    if not lines:                                   # no line timing: use the main room for the whole track
        main = max(groups, key=lambda x: x["n"])
        per_group[main["id"]] = [(0.0, 1e9)]
        scenes = None
    plan, parts = [], []
    for g, wins in sorted(per_group.items()):
        gi = groups[g]
        if gi["dry"]:
            parts.append(f"room {g + 1}: dry ({len(wins)} lines)")
            continue
        if (settings or {}).get("require_ok") and gi["confidence"] != "ok":
            parts.append(f"room {g + 1}: only {gi['n']} phrase(s) measured, too few to be sure ({len(wins)} lines)")
            continue
        strength = 1.0 if gi["confidence"] == "ok" else 0.75
        # lines of this room are split by the kind of place they are in: a measurement far beyond what that place can
        # have is brought down to the limit of the place (never raised)
        by_scene = {}
        for w, m in zip(wins, mids.get(g) or [None] * len(wins)):
            sc = None
            if scenes and m is not None:
                import scene_context
                sc = scene_context.scene_at(scenes, m)
            by_scene.setdefault(id(sc) if sc else None, (sc, []))[1].append(w)
        limited = []
        for sc, ws in by_scene.values():
            rt, wet = float(gi["rt60"]), float(gi["wet_db"])
            if sc:
                import scene_context
                rt, wet, changed = scene_context.apply_limit(rt, wet, sc)
                if changed:
                    limited.append(f"{sc['place'] or sc['key']} ({len(ws)} lines): {gi['rt60']:g} s / {gi['wet_db']:g} dB limited to {rt:g} s / {wet:g} dB")
            plan.append({"rt60": rt, "wet_db": float(np.clip(wet + trim, WET_MIN_DB, WET_MAX_DB)) + RENDER_TRIM_DB + 20.0 * float(np.log10(strength)), "windows": ws})
        parts.append(f"room {g + 1}: {gi['label']}, {gi['rt60']:g} s, {gi['wet_db'] + trim:g} dB"
                     + (f" (measured {gi['wet_db']:g} dB, you set {trim:+g} dB)" if trim else "") + f" ({len(wins)} lines"
                     + ("" if strength >= 1.0 else ", few phrases measured: softer") + ")"
                     + (" -- scene check: " + "; ".join(limited) if limited else ""))
    if not plan:
        return [], assignment, "auto: " + ("; ".join(parts) or "dry original") + " — nothing added"
    merged = {}                                      # same room sound in several places: one tail to render, not many
    for it in plan:
        k = (round(it["rt60"], 3), round(it["wet_db"], 3))
        if k in merged:
            merged[k]["windows"] = list(merged[k]["windows"]) + list(it["windows"])
        else:
            merged[k] = dict(it)
    return list(merged.values()), assignment, "auto: " + "; ".join(parts)


def apply_to_file(job_id, dry_path, out_path, settings, cache_dir, lines=None, vocals=None, spans_file=None, mp3=True):
    """Reads dry_path, writes out_path with each line's room added (mp3 for the short dub, wav for the long dub).
    Returns {"applied": bool, "why": str, "profile": ..., "assignment": {sid: room}}.  If nothing is to be added,
    out_path is not touched."""
    res = {"applied": False, "why": "", "assignment": {}}
    try:
        prof = profile_for_job(job_id, cache_dir, vocals, spans_file)
        plan, assignment, why = make_plan(prof, settings, lines)
        res.update(why=why, profile=prof, assignment=assignment)
        if not plan:
            return res
        tmp = Path(cache_dir) / f"{job_id}_room_tmp.wav"
        if not render_groups(dry_path, tmp, plan):
            res["why"] = "render failed; dub left as it is"
            return res
        if mp3:
            part = Path(str(out_path) + ".part.mp3")
            r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-codec:a", "libmp3lame", "-q:a", "2", "-f", "mp3", str(part)],
                               capture_output=True, timeout=1800)
            try:
                tmp.unlink()
            except Exception:
                pass
            if r.returncode != 0 or not part.exists() or part.stat().st_size < 1000:
                res["why"] = "encode failed; dub left as it is"
                return res
            os.replace(part, out_path)
        else:
            os.replace(tmp, out_path)
        res["applied"] = True
        return res
    except Exception as ex:
        res["why"] = f"skipped ({ex})"[:200]
        return res
