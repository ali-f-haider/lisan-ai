"""One level for each speaker: how loud a generated line must be so the dubbed voice sits where the original voice sat, without jumping.

Used by the long dub (longdub_service) and the short dub (eleven_service). Pure numpy, no I/O.

Every line is measured the same way as the original line it replaces (`active_level`: the speech frames only). A speaker has a usual level, the
median of the original's lines. A line is brought to that usual level plus LEVEL_FOLLOW of its difference from it (never more than LEVEL_SPREAD_DB
away): the dub keeps the shape of the original's loud and soft passages, but the measuring noise and the chance of each separately generated line
do not make the voice jump."""
import os

import numpy as np

LEVEL_FOLLOW = float(os.environ.get("LISAN_VOICE_FOLLOW", "0.6") or 0.6)     # how much of the original's line-to-line level changes the dub follows (1 = all)
LEVEL_SPREAD_DB = float(os.environ.get("LISAN_VOICE_SPREAD_DB", "6") or 6)   # a line never ends up further than this from the speaker's usual level
LEVEL_MIN_LINES = 4                                                          # a speaker with fewer measured lines has no usual level: the lines follow the original exactly
ACTIVE_BELOW_DB = 15.0                                                       # frames this far under the loud ones of a line are not speech
SHORTFALL_DB = 3.0                                                           # a line that still ends up this far UNDER its target is reported (never changed by this)


def active_level(x, sr):
    """(level_db, peak_db) of the speech in the samples `x` (mono float): the RMS of the frames that are speech (within ACTIVE_BELOW_DB of the loud
    ones). Pauses, breaths and a murmur under the words are left out, and the same measure is used for the original and for the dub, so the two
    can be compared. None when there is no speech."""
    frame = max(1, int(0.02 * sr))
    k = len(x) // frame
    if k < 5:
        return None
    fr = np.asarray(x[:k * frame], dtype=np.float64).reshape(k, frame)
    power = (fr ** 2).mean(axis=1)
    db = 10.0 * np.log10(power + 1e-12)
    top = float(np.percentile(db, 90))
    if top < -65.0:
        # a short audible word in a long slot: the loud frames are fewer than a tenth of all, so the percentile describes the silence around it
        audible = db[db >= -65.0]
        if len(audible) < 3:
            return None
        top = float(np.percentile(audible, 90))
    act = db > top - ACTIVE_BELOW_DB
    if int(act.sum()) < 3:
        return None
    return float(10.0 * np.log10(power[act].mean() + 1e-12)), float(20.0 * np.log10(np.max(np.abs(x)) + 1e-9))


def _target(o_db, anchor=None):
    """The level (dB) a generated line is meant to reach: the original's own level, or, with the speaker's usual level (`anchor`), that level plus
    LEVEL_FOLLOW of the line's distance from it (never more than LEVEL_SPREAD_DB away)."""
    target = float(o_db)
    if anchor is not None:
        target = float(anchor) + max(-LEVEL_SPREAD_DB, min(LEVEL_SPREAD_DB, LEVEL_FOLLOW * (float(o_db) - float(anchor))))
    return target


def voice_gain(o_db, d_db, d_peak=None, anchor=None, gain_max=8.0, peak_ceil=-1.0):
    """Gain (dB) that brings a generated line (speech level d_db) to the level of the original line (o_db). With the speaker's usual level
    (`anchor`) the line follows only LEVEL_FOLLOW of its distance from it, never more than LEVEL_SPREAD_DB. The gain is limited to +-gain_max,
    and a boost never pushes the peak (d_peak, when known) above peak_ceil."""
    if o_db is None or d_db is None:
        return 0.0
    target = _target(o_db, anchor)
    g = max(-gain_max, min(gain_max, target - float(d_db)))
    if d_peak is not None and peak_ceil is not None:
        g = min(g, peak_ceil - float(d_peak))
    return g


def shortfall(o_db, d_db, d_peak=None, anchor=None, gain_max=8.0, peak_ceil=-1.0):
    """(miss_db, limit): how far UNDER its target a generated line still ends up after voice_gain, and what held it there -- "gain" (the +-gain_max
    limit) or "peak" (a bigger boost would push the peak over peak_ceil). (0.0, None) when the line reaches its target or is louder than it.
    Measuring only: no gain is changed here."""
    if o_db is None or d_db is None:
        return 0.0, None
    want = _target(o_db, anchor) - float(d_db)
    got = voice_gain(o_db, d_db, d_peak, anchor, gain_max, peak_ceil)
    miss = want - got
    if miss <= 0.05:
        return 0.0, None
    capped = max(-gain_max, min(gain_max, want))
    return float(miss), ("peak" if got < capped - 1e-9 else "gain")


def speaker_anchors(levels_by_speaker):
    """{speaker: usual level (the median of the original's speech levels)} for the speakers with enough measured lines."""
    out = {}
    for sp, vals in levels_by_speaker.items():
        vals = [v for v in vals if v is not None and v > -60]
        if len(vals) >= LEVEL_MIN_LINES:
            out[sp] = float(np.median(vals))
    return out


def shortfalls(items, anchors=None, gain_max=8.0, peak_ceil=-1.0, min_miss=SHORTFALL_DB):
    """{key: {"miss": dB, "limit": "gain" or "peak"}} for the lines (same `items` as level_lines) that still end up at least `min_miss` dB under their
    target. `anchors` defaults to the ones level_lines uses for the same items."""
    usable = [it for it in items if it.get("orig") is not None and it.get("dub") is not None]
    if anchors is None:
        by_speaker = {}
        for it in usable:
            by_speaker.setdefault(str(it.get("speaker")), []).append(it["orig"])
        anchors = speaker_anchors(by_speaker)
    out = {}
    for it in usable:
        miss, limit = shortfall(it["orig"], it["dub"], it.get("peak"), anchors.get(str(it.get("speaker"))), gain_max, peak_ceil)
        if limit and miss >= min_miss:
            out[it["key"]] = {"miss": round(miss, 1), "limit": limit}
    return out


def describe_shortfalls(short, labels=None, most=10):
    """One line for the job log: how many lines stay under their target and the worst ones. `labels` is {key: "where it is"}."""
    if not short:
        return ""
    labels = labels or {}
    worst = sorted(short.items(), key=lambda kv: -kv[1]["miss"])[:most]
    parts = [f"{labels.get(k) or k} {v['miss']:.1f} dB under ({'gain limit' if v['limit'] == 'gain' else 'peak limit'})" for k, v in worst]
    more = f", and {len(short) - len(worst)} more" if len(short) > len(worst) else ""
    return f"{len(short)} line(s) end up more than {SHORTFALL_DB:g} dB under their target level: " + "; ".join(parts) + more


def level_lines(items, gain_max=8.0, peak_ceil=-1.0):
    """items: [{"key", "speaker", "orig" (original level dB), "dub" (generated line level dB), "peak" (optional)}].
    Returns (gains {key: dB}, anchors {speaker: dB}, stats {speaker: {...}}). Lines without both levels are left out of the result."""
    usable = [it for it in items if it.get("orig") is not None and it.get("dub") is not None]
    by_speaker = {}
    for it in usable:
        by_speaker.setdefault(str(it.get("speaker")), []).append(it["orig"])
    anchors = speaker_anchors(by_speaker)
    gains, stats = {}, {}
    for it in usable:
        sp = str(it.get("speaker"))
        anchor = anchors.get(sp)
        g = voice_gain(it["orig"], it["dub"], it.get("peak"), anchor, gain_max, peak_ceil)
        gains[it["key"]] = g
        if anchor is not None:
            s = stats.setdefault(sp, {"anchor": anchor, "orig": [], "exact": [], "after": [], "up": 0, "down": 0, "gains": [],
                                      "under": 0, "worst": 0.0, "under_gain": 0, "under_peak": 0})
            s["orig"].append(it["orig"])
            s["exact"].append(it["dub"] + voice_gain(it["orig"], it["dub"], it.get("peak"), None, gain_max, peak_ceil))
            s["after"].append(it["dub"] + g)
            s["up"] += g >= gain_max - 0.05                  # turned up as far as allowed
            s["down"] += g <= -gain_max + 0.05               # turned down as far as allowed
            s["gains"].append(g)
            miss, limit = shortfall(it["orig"], it["dub"], it.get("peak"), anchor, gain_max, peak_ceil)
            if limit and miss >= SHORTFALL_DB:
                s["under"] += 1
                s["worst"] = max(s["worst"], miss)
                s["under_gain" if limit == "gain" else "under_peak"] += 1
    out = {}
    for sp, s in stats.items():
        if len(s["orig"]) >= 2:
            out[sp] = {"lines": len(s["orig"]), "anchor": round(s["anchor"], 1), "orig_spread": round(float(np.std(s["orig"])), 1),
                       "dub_spread": round(float(np.std(s["after"])), 1), "exact_spread": round(float(np.std(s["exact"])), 1), "at_limit": int(s["up"] + s["down"]), "up_at_limit": int(s["up"]), "down_at_limit": int(s["down"]),
                       "median_gain": round(float(np.median(s["gains"])), 1),
                       "under_target": s["under"], "worst_miss": round(s["worst"], 1), "under_by_gain": s["under_gain"], "under_by_peak": s["under_peak"]}
    return gains, {k: round(v, 1) for k, v in anchors.items()}, out


def describe(stats, names=None):
    """One line for the job log."""
    names = names or {}
    return " | ".join(f"{names.get(sp) or sp}: {s['lines']} lines, usual level {s['anchor']:.1f} dB, spread of the original {s['orig_spread']:.1f} dB, "
                      f"of the dub {s['dub_spread']:.1f} dB (following the original exactly: {s['exact_spread']:.1f} dB), {s['at_limit']} at the gain limit "
                      f"({s['up_at_limit']} turned up as far as allowed, {s['down_at_limit']} turned down as far as allowed; median change {s['median_gain']:+.1f} dB)"
                      + (f", {s['under_target']} stay more than {SHORTFALL_DB:g} dB under their target (worst {s['worst_miss']:.1f} dB; "
                         f"{s.get('under_by_gain', 0)} held by the gain limit, {s.get('under_by_peak', 0)} by the peak limit)" if s.get("under_target") else "")
                      for sp, s in stats.items())
