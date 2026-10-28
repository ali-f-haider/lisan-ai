"""Speech the recogniser heard must stay in the transcript.

The recogniser's words are compared with the lines after every step that edits lines. A stretch of heard words that no line
holds any more (no line covers its time, and its words are not in the text of the lines around it) was lost by one of our
own steps: the log names the step, and at the end the stretch is put back as its own line, marked 'check speaker'.
Words that were never in a line (lost before the first step) are only logged: those are not ours to put back.
Pure logic, no network, no models.
"""
import re

MIN_WORDS = 2          # a lost stretch needs at least this many words ...
MIN_SECONDS = 0.6      # ... or this many seconds
JOIN_GAP = 1.0         # heard words closer than this belong to one stretch
EDGE = 0.2             # a word this close to a line's start or end still counts as inside it
NEAR = 5.0             # the text of lines this close (seconds) is searched for the words
FOUND_SHARE = 0.5      # a stretch whose words are at least this share in the nearby text is not lost


def _tokens(text):
    return re.findall(r"[a-z0-9؀-ۿ']+", str(text or "").lower())


def heard_words(segments):
    """[(start, end, word)] from the recogniser's segments (objects or dicts with .words / ['words'])."""
    out = []
    for s in segments or []:
        words = getattr(s, "words", None) if not isinstance(s, dict) else s.get("words")
        for w in words or []:
            get = (lambda k: w.get(k)) if isinstance(w, dict) else (lambda k: getattr(w, k, None))
            st, en, wd = get("start"), get("end"), get("word")
            if st is None or en is None or not str(wd or "").strip():
                continue
            out.append((float(st), float(en), str(wd).strip()))
    out.sort()
    return out


def _inside(t, rows):
    return any(float(r.get("start") or 0) - EDGE <= t <= float(r.get("end") or 0) + EDGE for r in rows)


def missing(words, rows):
    """Stretches of heard words that no line holds: [{"start", "end", "words": [(s, e, w)], "text"}]."""
    loose = [w for w in words if not _inside((w[0] + w[1]) / 2.0, rows)]
    runs, cur = [], []
    for w in loose:
        if cur and w[0] - cur[-1][1] > JOIN_GAP:
            runs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        runs.append(cur)
    out = []
    for run in runs:
        start, end = run[0][0], run[-1][1]
        if len(run) < MIN_WORDS and end - start < MIN_SECONDS:
            continue
        near = set()
        for r in rows:
            if float(r.get("end") or 0) >= start - NEAR and float(r.get("start") or 0) <= end + NEAR:
                near.update(_tokens(r.get("text")))
        toks = [t for w in run for t in _tokens(w[2])]
        if toks and sum(t in near for t in toks) >= FOUND_SHARE * len(toks):
            continue          # the words are in a line nearby (its times were moved): not lost
        out.append({"start": round(start, 2), "end": round(end, 2), "words": run, "text": " ".join(w[2] for w in run).strip()})
    return out


class Tracker:
    """Checks the lines after each step. `lost_at` names the first step after which a stretch was missing."""

    def __init__(self, segments):
        self.words = heard_words(segments)
        self.first = None          # stretches already missing at the first check (never in a line)
        self.lost_at = []          # [(step, stretch)]

    def check(self, step, rows):
        try:
            now = missing(self.words, rows)
        except Exception:
            return
        if self.first is None:
            self.first = now
            return
        known = {(s["start"], s["end"]) for s in self.first} | {(s["start"], s["end"]) for _, s in self.lost_at}
        for s in now:
            if (s["start"], s["end"]) not in known and not any(_overlaps(s, o) for o in self.first):
                self.lost_at.append((step, s))

    def restore(self, rows):
        """Puts back every stretch our own steps lost that is still missing. Returns (rows, restored stretches)."""
        try:
            still = missing(self.words, rows)
        except Exception:
            return rows, []
        lost = [s for s in still if not any(_overlaps(s, o) for o in (self.first or []))]
        if not lost:
            return rows, []
        nums = [int(m.group(1)) for m in (re.fullmatch(r"seg_([0-9]+)", str(r.get("segment_id") or "")) for r in rows) if m]
        nxt = max(nums, default=len(rows)) + 1
        out = list(rows)
        for s in lost:
            ref = _nearest(s, rows)
            row = {
                "segment_id": f"seg_{nxt}", "start": s["start"], "end": s["end"], "text": s["text"],
                "speaker": (ref or {}).get("speaker") or "Speaker 1",
                "gender": (ref or {}).get("gender") or "male", "emotion": "neutral", "arabic_text": "",
                "words": [{"word": w, "start": round(a, 3), "end": round(b, 3)} for a, b, w in s["words"]],
                "restored": True,
                "speaker_vote": {"source": "restored", "support": 0, "present": 0, "badge": True, "reasons": ["voters_split"]},
            }
            nxt += 1
            out.append(row)
        out.sort(key=lambda r: (float(r.get("start") or 0), float(r.get("end") or 0)))
        for i, r in enumerate(out):      # a put-back line never runs into the next one
            if r.get("restored") and i + 1 < len(out):
                r["end"] = round(max(float(r["start"]) + 0.2, min(float(r["end"]), float(out[i + 1].get("start") or r["end"]))), 2)
        return out, lost


def _overlaps(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def _nearest(s, rows):
    """The line closest in time (the one right after wins a tie: a sentence is usually cut off before its own speaker's line)."""
    best, key = None, None
    for r in rows:
        gap = max(float(r.get("start") or 0) - s["end"], s["start"] - float(r.get("end") or 0), 0.0)
        k = (gap, 0 if float(r.get("start") or 0) >= s["end"] - 0.01 else 1)
        if key is None or k < key:
            best, key = r, k
    return best


def clock(t):
    t = max(0.0, float(t))
    return f"{int(t // 60)}:{t % 60:04.1f}"


def summary(tracker, restored):
    """Log text (empty when nothing was lost)."""
    parts = []
    if tracker.lost_at:
        parts.append("lost by a step: " + "; ".join(f"{step} {clock(s['start'])} \"{s['text'][:60]}\"" for step, s in tracker.lost_at[:8]))
    if restored:
        parts.append(f"{len(restored)} put back as their own line (marked 'check speaker'): "
                     + "; ".join(f"{clock(s['start'])} \"{s['text'][:60]}\"" for s in restored[:8]))
    if tracker.first:
        parts.append(f"{len(tracker.first)} heard but never in a line: "
                     + "; ".join(f"{clock(s['start'])} \"{s['text'][:40]}\"" for s in tracker.first[:5]))
    return " | ".join(parts)
