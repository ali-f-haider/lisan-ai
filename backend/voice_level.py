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
        return None
    act = db > top - ACTIVE_BELOW_DB
    if int(act.sum()) < 3:
        return None
    return float(10.0 * np.log10(power[act].mean() + 1e-12)), float(20.0 * np.log10(np.max(np.abs(x)) + 1e-9))


def voice_gain(o_db, d_db, d_peak=None, anchor=None, gain_max=8.0, peak_ceil=-1.0):
    """Gain (dB) that brings a generated line (speech level d_db) to the level of the original line (o_db). With the speaker's usual level
    (`anchor`) the line follows only LEVEL_FOLLOW of its distance from it, never more than LEVEL_SPREAD_DB. The gain is limited to +-gain_max,
    and a boost never pushes the peak (d_peak, when known) above peak_ceil."""
    if o_db is None or d_db is None:
        return 0.0
    target = float(o_db)
    if anchor is not None:
        target = float(anchor) + max(-LEVEL_SPREAD_DB, min(LEVEL_SPREAD_DB, LEVEL_FOLLOW * (float(o_db) - float(anchor))))
    g = max(-gain_max, min(gain_max, target - float(d_db)))
    if d_peak is not None and peak_ceil is not None:
        g = min(g, peak_ceil - float(d_peak))
    return g


def speaker_anchors(levels_by_speaker):
    """{speaker: usual level (the median of the original's speech levels)} for the speakers with enough measured lines."""
    out = {}
    for sp, vals in levels_by_speaker.items():
        vals = [v for v in vals if v is not None and v > -60]
        if len(vals) >= LEVEL_MIN_LINES:
            out[sp] = float(np.median(vals))
    return out


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
            s = stats.setdefault(sp, {"anchor": anchor, "orig": [], "exact": [], "after": [], "limit": 0})
            s["orig"].append(it["orig"])
            s["exact"].append(it["dub"] + voice_gain(it["orig"], it["dub"], it.get("peak"), None, gain_max, peak_ceil))
            s["after"].append(it["dub"] + g)
            s["limit"] += abs(g) >= gain_max - 0.05
    out = {}
    for sp, s in stats.items():
        if len(s["orig"]) >= 2:
            out[sp] = {"lines": len(s["orig"]), "anchor": round(s["anchor"], 1), "orig_spread": round(float(np.std(s["orig"])), 1),
                       "dub_spread": round(float(np.std(s["after"])), 1), "exact_spread": round(float(np.std(s["exact"])), 1), "at_limit": int(s["limit"])}
    return gains, {k: round(v, 1) for k, v in anchors.items()}, out


def describe(stats, names=None):
    """One line for the job log."""
    names = names or {}
    return " | ".join(f"{names.get(sp) or sp}: {s['lines']} lines, usual level {s['anchor']:.1f} dB, spread of the original {s['orig_spread']:.1f} dB, "
                      f"of the dub {s['dub_spread']:.1f} dB (following the original exactly: {s['exact_spread']:.1f} dB), {s['at_limit']} at the gain limit"
                      for sp, s in stats.items())
