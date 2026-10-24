"""A plain per-line timing report for a long dub, written into the job log.

For every dubbed line it says where the original line was, where the dub plays, how much the voice was sped up, how long the
Arabic naturally is against the room the original left, and whether the dub ends later or earlier than the original did.
It only reads what the mixing step already decided (nothing is changed, nothing is billed) and never raises.

Short videos get a row for every line; long ones get a summary of all lines and rows for the lines most worth a look (the log
keeps 1500 characters per event, so a one-hour video cannot list everything). Read the rows like this:
    seg_15 0:49.1-0:50.7 > 0:49.1-0:50.7, x1.25, natural 1.9s for 1.6s, ends +0.0s, [fast, reworded]
    line, original start-end > dub start-end, speed-up, length of the Arabic before any speed-up for the original's length,
    how much later (+) or earlier (-) the dub ends than the original, and flags.
"""
import math

FAST_TEMPO = 1.2          # a line played this much faster (or more) is called "fast"
MOVED_SEC = 0.15          # a line placed this far from where the original started is called "moved"
LATE_SEC = 0.5            # a dub that ends this much after the original line is called "late"
ALL_LINES_UP_TO = 60      # up to this many lines every line gets a row
MAX_ROWS = 90             # above that, only this many of the most notable lines
EVENT_CHARS = 1400        # one log event holds at most 1500 characters


def clock(t):
    t = max(0.0, float(t or 0.0))
    m, s = divmod(t, 60.0)
    if round(s, 1) >= 60.0:
        m, s = m + 1, 0.0
    return "%d:%04.1f" % (int(m), s)


def _f(v, default=0.0):
    try:
        v = float(v)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def facts(it, reworded=()):
    """What is worth knowing about one placed line (a dict of numbers, plus flags)."""
    start = _f(it.get("start"))
    o0 = _f(it.get("orig_start"), start)
    o1 = _f(it.get("end"), o0)
    played = _f(it.get("allowed"), _f(it.get("dur")))
    tempo = _f(it.get("tempo"), 1.0) or 1.0
    d1 = start + played
    shift = start - o0
    late = d1 - o1
    orig_len = max(o1 - o0, 0.0)
    natural = it.get("raw")
    natural = _f(natural, None) if natural is not None else None
    if natural is None:
        natural = _f(it.get("dur")) * tempo        # what the line would last at normal speed
    sid = str(it.get("seg") or "")
    flags = []
    if it.get("trim"):
        flags.append("cut")
    if tempo >= FAST_TEMPO:
        flags.append("fast")
    if abs(shift) >= MOVED_SEC:
        flags.append("moved %+.2fs" % shift)
    if late >= LATE_SEC:
        flags.append("late")
    if sid in (reworded or ()):
        flags.append("reworded")
    score = (100.0 if it.get("trim") else 0.0) + max(0.0, tempo - 1.0) * 20.0 + max(0.0, late) + abs(shift) * 2.0 \
        + (1.0 if sid in (reworded or ()) else 0.0)
    return {"sid": sid, "o0": o0, "o1": o1, "d0": start, "d1": d1, "tempo": tempo, "shift": shift, "late": late,
            "natural": natural, "orig_len": orig_len, "flags": flags, "score": score}


def row(fa):
    s = "%s %s-%s > %s-%s, x%.2f, natural %.1fs for %.1fs, ends %+.1fs" % (
        fa["sid"] or "line", clock(fa["o0"]), clock(fa["o1"]), clock(fa["d0"]), clock(fa["d1"]),
        fa["tempo"], fa["natural"], fa["orig_len"], fa["late"])
    if fa["flags"]:
        s += ", [" + ", ".join(fa["flags"]) + "]"
    return s


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return 0.0
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def pack(rows, limit=EVENT_CHARS):
    """Rows joined into as few log events as fit, none longer than `limit` characters."""
    out, cur = [], ""
    for r in rows:
        r = r[:limit]
        if cur and len(cur) + 3 + len(r) > limit:
            out.append(cur)
            cur = r
        else:
            cur = r if not cur else cur + " | " + r
    if cur:
        out.append(cur)
    return out


def report(kept, reworded=None):
    """(summary text, status, [row events]) for the placed lines `kept`. Status is "partial" when a line was cut."""
    reworded = reworded or {}
    fs = [facts(it, reworded) for it in kept]
    n = len(fs)
    if not n:
        return "no lines were placed", "info", []
    tempos = [x["tempo"] for x in fs]
    cut = sum(1 for x in fs if "cut" in x["flags"])
    fast = sum(1 for x in fs if x["tempo"] >= FAST_TEMPO)
    unchanged = sum(1 for x in fs if x["tempo"] < 1.02)
    moved = [x for x in fs if abs(x["shift"]) >= MOVED_SEC]
    late = [x for x in fs if x["late"] >= LATE_SEC]
    reph = sum(1 for x in fs if "reworded" in x["flags"])
    ratios = [x["natural"] / x["orig_len"] for x in fs if x["orig_len"] > 0.2]
    parts = ["%d lines" % n,
             "speed-up median x%.2f, highest x%.2f (%d at x%.1f or more, %d left at normal speed)" % (_median(tempos), max(tempos), fast, FAST_TEMPO, unchanged),
             "%d cut short" % cut]
    if ratios:
        parts.append("the Arabic is %.2fx the original's length before any speed-up (median), up to %.2fx" % (_median(ratios), max(ratios)))
    parts.append("%d moved by %.2fs or more%s" % (len(moved), MOVED_SEC, (" (up to %.2fs)" % max(abs(x["shift"]) for x in moved)) if moved else ""))
    parts.append("%d ending %.1fs or more after the original%s" % (len(late), LATE_SEC, (" (up to %+.1fs)" % max(x["late"] for x in late)) if late else ""))
    parts.append("%d reworded shorter" % reph)
    if n <= ALL_LINES_UP_TO:
        chosen, how = fs, "a row for every line"
    else:
        notable = sorted((x for x in fs if x["flags"]), key=lambda x: -x["score"])[:MAX_ROWS]
        chosen = notable
        how = "rows for the %d most notable of the %d lines with a flag" % (len(notable), sum(1 for x in fs if x["flags"])) \
            if sum(1 for x in fs if x["flags"]) > len(notable) else "rows for the %d lines with a flag" % len(notable)
    chosen = sorted(chosen, key=lambda x: x["o0"])
    parts.append(how + ". Row: line, original start-end > dub start-end, speed-up, Arabic length at normal speed for the original's length, dub end vs original end")
    status = "partial" if cut else "ok"
    return "; ".join(parts)[:1500], status, pack([row(x) for x in chosen])
