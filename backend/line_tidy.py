"""Free clean-up of the lines after the speakers are known (no AI call, no cost).

The speech recogniser and the speaker detector cut the transcript at pauses and at changes of voice, and they are not
always right at the edge between two speakers. Two mistakes are common and can be recognised from the text itself:

* a short phrase of ONE speaker split in two ("Good" | "Lord. You're a confidential informant.") - the two lines are
  joined again once both have the same speaker;
* the first words of a sentence stuck to the END of the previous speaker's line ("And that is? My Immunity" | "Agreement
  with the federal government...") - one sentence is said by one voice, so its unfinished beginning moves to the line
  that finishes it. Nothing is invented: the words only change line, and a line that gained words early gets its start
  moved back to where the voice really begins (looked up in the separated voices).

tidy_lines(rows, vocals) -> (rows, report). Never raises: any problem returns the rows as they were.
"""
import re

SENT_END = re.compile(r"[.!?؟…][\"'”’)\]]*\s*$")
INTERRUPTED = re.compile(r"[-–—]\s*$")
NOT_END = {"mr.", "mrs.", "ms.", "dr.", "st.", "jr.", "sr.", "vs.", "no.", "mt.", "prof.", "gen.", "col.", "sgt.", "lt.", "capt."}
MERGE_GAP = 0.25            # seconds: lines further apart than this are not one phrase
MAX_LINE_SEC = 15.0         # a joined line is never longer than this (the same cap the transcript is built with)
MAX_TAIL_WORDS = 8          # an unfinished beginning longer than this is left alone (probably a real interruption)
MIN_VOICE_SEC = 0.25        # a stretch of voice shorter than this is a breath or noise, not words
ONSET_BACK_MAX = 6.0        # a line's start is never moved back by more than this


def _ends_sentence(text):
    t = str(text or "").strip()
    return bool(SENT_END.search(t)) and t.split()[-1].lower() not in NOT_END if t else False


def _tokens(text):
    return str(text or "").split()


def _same_voice(a, b):
    ka = a.get("speaker_id") if a.get("speaker_id") is not None else a.get("speaker")
    kb = b.get("speaker_id") if b.get("speaker_id") is not None else b.get("speaker")
    return ka == kb


def _join_text(a, b):
    return (str(a or "").strip() + " " + str(b or "").strip()).strip()


def merge_split_phrases(rows):
    """Join neighbouring lines of the same speaker when the first stops in the middle of a sentence."""
    out, joined = [], 0
    for row in rows:
        if out:
            prev = out[-1]
            gap = float(row["start"]) - float(prev["end"])
            if (_same_voice(prev, row) and gap <= MERGE_GAP and not _ends_sentence(prev.get("text"))
                    and not INTERRUPTED.search(str(prev.get("text") or ""))
                    and float(row["end"]) - float(prev["start"]) <= MAX_LINE_SEC
                    and not (prev.get("locked") or row.get("locked"))):
                prev["end"] = row["end"]
                prev["text"] = _join_text(prev.get("text"), row.get("text"))
                if "words" in prev or "words" in row:
                    prev["words"] = list(prev.get("words") or []) + list(row.get("words") or [])
                joined += 1
                continue
        out.append(row)
    return out, joined


def _tail_start(tokens):
    """Index of the first word of the unfinished last sentence, or None when the line holds no finished sentence."""
    cut = None
    for i, tok in enumerate(tokens[:-1]):
        if SENT_END.search(tok) and tok.lower() not in NOT_END:
            cut = i + 1
    return cut


def voice_onset(vocals, lo, hi):
    """Where a sustained stretch of voice first starts between lo and hi seconds in the separated voices, or None."""
    try:
        import numpy as np
        import soundfile as sf
        if hi - lo < MIN_VOICE_SEC:
            return None
        info = sf.info(str(vocals))
        sr = info.samplerate
        x, _ = sf.read(str(vocals), start=int(max(0.0, lo) * sr), frames=int((hi - lo) * sr), dtype="float32", always_2d=True)
        x = x.mean(axis=1)
        win = max(1, int(0.03 * sr))
        n = len(x) // win
        if n < 3:
            return None
        rms = np.sqrt((x[:n * win].reshape(n, win) ** 2).mean(axis=1)) + 1e-9
        db = 20 * np.log10(rms)
        floor = np.percentile(db, 20)
        loud = db > max(floor + 12.0, -55.0)
        need = max(1, int(MIN_VOICE_SEC / 0.03))
        run = 0
        for i, flag in enumerate(loud):
            run = run + 1 if flag else 0
            if run >= need:
                return lo + (i - run + 1) * win / sr
        return None
    except Exception:
        return None


def shift_tail(a, b, cut, vocals=None):
    """Move the words of line a from word number `cut` on to the start of the NEXT line b. a keeps its first `cut` words.
    The times follow the words: from the word times when they exist, else a keeps its share of the time (by letters), and b
    starts where the voice of the moved words really starts (looked up in the separated voices)."""
    text = str(a.get("text") or "").strip()
    tokens = _tokens(text)
    tail = " ".join(tokens[cut:])
    keep = " ".join(tokens[:cut])
    old_end, old_start_b = float(a["end"]), float(b["start"])
    a_words, tail_words = a.get("words") or [], []
    if a_words and len(a_words) >= len(tokens):
        tail_words = a_words[cut:]
        a["words"] = a_words[:cut]
        if a["words"]:
            a["end"] = round(float(a["words"][-1]["end"]), 2)
    else:                                   # no word times (the text came from the subtitle): shrink by the share of letters kept
        share = max(0.2, len(keep) / max(1, len(text)))
        a["end"] = round(float(a["start"]) + (old_end - float(a["start"])) * share, 2)
        if "words" in a:
            a["words"] = []
    a["text"] = keep
    b["text"] = _join_text(tail, b.get("text"))
    if "words" in b or tail_words:
        b["words"] = list(tail_words) + list(b.get("words") or [])
    new_start = old_start_b
    if tail_words:
        new_start = min(new_start, float(tail_words[0]["start"]))
    if vocals is not None:                  # the voice of the words that moved starts before this line did: find where
        lo = max(float(a["end"]), old_start_b - ONSET_BACK_MAX)
        onset = voice_onset(vocals, lo, old_start_b)
        if onset is not None:
            new_start = min(new_start, max(lo, onset - 0.05))
    b["start"] = round(new_start, 2)


def shift_head(a, b, count):
    """Move the first `count` words of line b to the end of the PREVIOUS line a (the same, the other way round)."""
    text = str(b.get("text") or "").strip()
    tokens = _tokens(text)
    head, rest = " ".join(tokens[:count]), " ".join(tokens[count:])
    old_end_a, old_start_b = float(a["end"]), float(b["start"])
    b_words, head_words = b.get("words") or [], []
    if b_words and len(b_words) >= len(tokens):
        head_words = b_words[:count]
        b["words"] = b_words[count:]
        if b["words"]:
            b["start"] = round(float(b["words"][0]["start"]), 2)
        a["end"] = round(max(old_end_a, float(head_words[-1]["end"])), 2)
    else:
        share = max(0.2, len(rest) / max(1, len(text)))
        span = float(b["end"]) - old_start_b
        b["start"] = round(float(b["end"]) - span * share, 2)
        a["end"] = round(max(old_end_a, min(float(b["start"]), old_end_a + (old_start_b - old_end_a) + span * (1 - share))), 2)
        if "words" in b:
            b["words"] = []
    a["text"] = _join_text(a.get("text"), head)
    b["text"] = rest
    if "words" in a or head_words:
        a["words"] = list(a.get("words") or []) + list(head_words)


def move_unfinished_beginnings(rows, vocals=None):
    """A sentence has one speaker: when a line ends in the middle of a sentence and the next line has another speaker,
    the unfinished beginning of that sentence moves to the line that finishes it."""
    moved = 0
    for k in range(len(rows) - 1):
        a, b = rows[k], rows[k + 1]
        if _same_voice(a, b) or a.get("locked") or b.get("locked"):
            continue
        text = str(a.get("text") or "").strip()
        if not text or _ends_sentence(text) or INTERRUPTED.search(text) or not str(b.get("text") or "").strip():
            continue
        tokens = _tokens(text)
        cut = _tail_start(tokens)
        if cut is None or len(tokens) - cut > MAX_TAIL_WORDS:
            continue                            # no finished sentence before it (an interruption), or too long to be a stray beginning
        shift_tail(a, b, cut, vocals)
        moved += 1
    return moved


def tidy_lines(rows, vocals=None):
    """Returns (rows, {"joined": n, "moved": n}). The rows are changed in place and renumbered."""
    report = {"joined": 0, "moved": 0}
    try:
        ordered = sorted(rows, key=lambda r: (float(r["start"]), float(r["end"])))
        merged, report["joined"] = merge_split_phrases([dict(r) for r in ordered])
        report["moved"] = move_unfinished_beginnings(merged, vocals)
        for i, r in enumerate(merged):
            r["segment_id"] = f"seg_{i}"
        return merged, report
    except Exception:
        return rows, {"joined": 0, "moved": 0, "error": True}
