"""A plain per-line record, in the job log, of who said each line at every step that can change it.

For every line of the finished transcript it says: what the voice detector had decided, what the speaker check (the two Gemini
listening checks together with the detector, speaker_vote.py) decided and which of them backed that, and what the line is in the end
(after the free clean-up that joins split phrases and moves a stray sentence beginning). Nothing here decides anything or costs
anything; it only reads rows and never raises.

Reading a row:
    seg_3 0:17.3-0:20.5 "How's your face?" | detector S2 | check S2 (dialogue+audio+detector, 3/3) | final S2
    seg_4 0:20.5-0:21.0 "Fine." | detector S2 | check S1 <- moved (dialogue+audio, 2/3) | final S1
A line is marked  <- moved  when the check changed the detector's answer, <- after  when the clean-up changed the check's answer,
and  split  when the voters did not agree (less than all of them backed the winner). S1, S2 are Speaker 1, Speaker 2.
"""
import re

ALL_LINES_UP_TO = 60      # up to this many lines every line gets a row
MAX_ROWS = 80             # above that, only the lines where something changed or the voters split
EVENT_CHARS = 1400        # one log event holds at most 1500 characters
TEXT_CHARS = 26

_VOTERS = {"app": "detector", "dialogue": "dialogue", "audio": "audio", "voice": "voice"}


def short(name):
    """'Speaker 2' -> 'S2'; any other name is shortened to its first 6 characters."""
    s = str(name or "?")
    m = re.fullmatch(r"Speaker ([0-9]+)", s)
    return "S" + m.group(1) if m else s[:6]


def clock(t):
    try:
        t = max(0.0, float(t))
    except (TypeError, ValueError):
        t = 0.0
    m, s = divmod(t, 60.0)
    if round(s, 1) >= 60.0:
        m, s = m + 1, 0.0
    return "%d:%04.1f" % (int(m), s)


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def snapshot(rows):
    """{segment_id: speaker} as the detector left it, taken just before the speaker check runs."""
    return {r.get("segment_id"): r.get("speaker") for r in rows or [] if isinstance(r, dict)}


def build(rows, detector, by_line):
    """The vote-stage record: one entry per line the check looked at (time, text, detector's speaker, the check's speaker,
    who backed it). `detector` is snapshot() from before the check; `by_line` is apply()'s {segment_id: {by, support, present}}."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        sid = r.get("segment_id")
        info = (by_line or {}).get(sid) or {}
        out.append({"start": _num(r.get("start")), "end": _num(r.get("end")), "text": str(r.get("text") or ""),
                    "det": (detector or {}).get(sid), "vote": r.get("speaker"),
                    "by": [_VOTERS.get(x, str(x)) for x in (info.get("by") or [])],
                    "support": info.get("support"), "present": info.get("present")})
    return out


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def _stage_for(stage, start, end):
    """The vote-stage entry that covers the most of [start, end] of a final line (the clean-up may have joined or moved lines)."""
    best, best_ov = None, 0.0
    for e in stage or []:
        ov = _overlap(e["start"], e["end"], start, end)
        if ov > best_ov:
            best, best_ov = e, ov
    return best


def facts(final_row, stage):
    start, end = _num(final_row.get("start")), _num(final_row.get("end"))
    e = _stage_for(stage, start, end)
    final = final_row.get("speaker")
    f = {"sid": str(final_row.get("segment_id") or "line"), "start": start, "end": end,
         "text": " ".join(str(final_row.get("text") or "").split()), "final": final,
         "det": e["det"] if e else None, "vote": e["vote"] if e else None, "by": e["by"] if e else [],
         "support": e["support"] if e else None, "present": e["present"] if e else None}
    f["moved"] = bool(e) and f["det"] is not None and f["det"] != f["vote"]
    f["after"] = bool(e) and f["vote"] is not None and f["vote"] != final
    f["split"] = bool(e) and isinstance(f["support"], int) and isinstance(f["present"], int) and f["present"] > 0 and f["support"] < f["present"]
    return f


def row(f):
    text = f["text"] if len(f["text"]) <= TEXT_CHARS else f["text"][:TEXT_CHARS - 1].rstrip() + "…"
    s = '%s %s-%s "%s" | detector %s' % (f["sid"], clock(f["start"]), clock(f["end"]), text.replace('"', "'"), short(f["det"]) if f["det"] is not None else "-")
    if f["vote"] is None:
        s += " | check -"
    else:
        s += " | check %s%s" % (short(f["vote"]), " <- moved" if f["moved"] else "")
        if f["by"]:
            s += " (%s%s)" % ("+".join(f["by"]), ", %s/%s" % (f["support"], f["present"]) if f["present"] else "")
        if f["split"]:
            s += " split"
    s += " | final %s%s" % (short(f["final"]), " <- after" if f["after"] else "")
    return s


def pack(rows, limit=EVENT_CHARS):
    out, cur = [], ""
    for r in rows:
        r = r[:limit]
        if cur and len(cur) + 4 + len(r) > limit:
            out.append(cur)
            cur = r
        else:
            cur = r if not cur else cur + " // " + r
    if cur:
        out.append(cur)
    return out


def report(final_rows, stage):
    """(summary text, [row events]) for the finished transcript `final_rows`; `stage` is build() (empty when the check did not run)."""
    try:
        fs = [facts(r, stage) for r in final_rows or [] if isinstance(r, dict)]
    except Exception as ex:      # a log helper must never stop a job
        return "speaker trace unavailable (%s)" % type(ex).__name__, []
    n = len(fs)
    if not n:
        return "no lines", []
    ran = bool(stage)
    moved = sum(1 for f in fs if f["moved"])
    after = sum(1 for f in fs if f["after"])
    split = sum(1 for f in fs if f["split"])
    agree = sum(1 for f in fs if f["det"] is not None and f["det"] == f["final"])
    if ran:
        summary = ("%d lines; the detector's speaker is the final one on %d; the speaker check moved %d; the clean-up changed %d after the check; "
                   "%d where the voters were split") % (n, agree, moved, after, split)
    else:
        summary = "%d lines; the speaker check did not run, so the detector decides (%d lines)" % (n, n)
    if n <= ALL_LINES_UP_TO:
        chosen, how = fs, "a row for every line"
    else:
        notable = [f for f in fs if f["moved"] or f["after"] or f["split"]][:MAX_ROWS]
        chosen, how = notable, "rows for the %d lines where something changed or the voters were split" % len(notable)
    summary += ". " + how + ". Rows are separated by //. Row: line, time, start of the text, detector, check (who backed it, backers/voters), final. S1 = Speaker 1"
    return summary[:1500], pack([row(f) for f in chosen])
