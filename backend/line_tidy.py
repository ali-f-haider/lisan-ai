"""Free clean-up of the lines after the speakers are known (no AI call, no cost).

The speech recogniser and the speaker detector cut the transcript at pauses and at changes of voice, and they are not
always right at the edge between two speakers. Two mistakes are common and can be recognised from the text itself:

* a short phrase of ONE speaker split in two ("Good" | "Lord. You're a confidential informant.") - the two lines are
  joined again once both have the same speaker;
* the first word or two of a sentence left alone on a line of their own, with the rest of the sentence on the next line
  of the same speaker a little later ("How's" | "your face?"; the lower-case start of the second line shows it continues) -
  the two lines are joined, and the joined line starts where the voice really starts;
* the first words of a sentence stuck to the END of the previous speaker's line ("And that is? My Immunity" | "Agreement
  with the federal government...") - one sentence is said by one voice, so its unfinished beginning moves to the line
  that finishes it. Nothing is invented: the words only change line, and a line that gained words early gets its start
  moved back to where the voice really begins (looked up in the separated voices).

tidy_lines(rows, vocals) -> (rows, report). Never raises: any problem returns the rows as they were.
"""
import languages, lang_text
import re

SENT_END = re.compile(r"[.!?؟…][\"'”’)\]]*\s*$")
INTERRUPTED = re.compile(r"[-–—]\s*$")
NOT_END = {"mr.", "mrs.", "ms.", "dr.", "st.", "jr.", "sr.", "vs.", "no.", "mt.", "prof.", "gen.", "col.", "sgt.", "lt.", "capt."}
MERGE_GAP = 0.25            # seconds: lines further apart than this are not one phrase
MAX_LINE_SEC = 15.0         # a joined line is never longer than this (the same cap the transcript is built with)
MAX_TAIL_WORDS = 8          # an unfinished beginning longer than this is left alone (probably a real interruption)
MIN_VOICE_SEC = 0.25        # a stretch of voice shorter than this is a breath or noise, not words
ONSET_BACK_MAX = 6.0        # a line's start is never moved back by more than this
FRAGMENT_MAX_WORDS = 3      # a first part of this many words or fewer, cut off before the sentence ends ("How's" | "your face?")
FRAGMENT_MAX_GAP = 2.5      # seconds: the recogniser sometimes places the first word long before the rest (measured: 1.5 s)
AFTER_PREVIOUS_SEC = 0.25   # the search for a joined line's voice starts this long after the end of the line before it
FRAGMENT_MOVE_MIN = 0.3     # the joined line starts later than the first part claimed only when the voice really starts this much later


def _ends_sentence(text, source_language="en"):
    if languages.get(source_language).code != "en":
        return lang_text.ends_sentence(text, source_language, profile="tidy")
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


def merge_split_phrases(rows, source_language="en"):
    """Join neighbouring lines of the same speaker when the first stops in the middle of a sentence."""
    out, joined = [], 0
    for row in rows:
        if out:
            prev = out[-1]
            gap = float(row["start"]) - float(prev["end"])
            if (_same_voice(prev, row) and gap <= MERGE_GAP and not _ends_sentence(prev.get("text"), source_language=source_language)
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


def voiced_onset(vocals, lo, hi):
    """Where the first steady stretch of a voiced sound (a pitch, not a rustle or a clink) starts between lo and hi seconds, or None.
    The level alone is too late for a quiet first word ("How's"): the pitch shows the voice a fraction of a second earlier."""
    try:
        import numpy as np
        import soundfile as sf
        import pitch_check
        if hi - lo < MIN_VOICE_SEC:
            return None
        sr = sf.info(str(vocals)).samplerate
        x, _ = sf.read(str(vocals), start=int(max(0.0, lo) * sr), frames=int((hi - lo) * sr), dtype="float32", always_2d=True)
        f0 = pitch_check.f0_frames(x.mean(axis=1), sr)
        voiced = np.nonzero(f0)[0]
        return None if voiced.size == 0 else max(0.0, lo) + float(voiced[0]) * pitch_check.HOP_SEC
    except Exception:
        return None


def merge_fragments(rows, vocals=None, source_language="en"):
    """Join a short unfinished first part with the line after it (same speaker, up to FRAGMENT_MAX_GAP later) when that line
    starts with a small letter, i.e. continues the sentence. The first part must carry no punctuation at its end: a comma or
    a dash is a pause the speaker made, not a cut by the recogniser. Returns (rows, joined)."""
    out, joined = [], 0
    for row in rows:
        if out:
            prev = out[-1]
            ta, tb = str(prev.get("text") or "").strip(), str(row.get("text") or "").strip()
            gap = float(row["start"]) - float(prev["end"])
            if (ta and tb and _same_voice(prev, row) and gap > MERGE_GAP and gap <= FRAGMENT_MAX_GAP
                    and len(_tokens(ta)) <= FRAGMENT_MAX_WORDS and ta[-1].isalnum()
                    and tb[0].isalpha() and tb[0].islower()
                    and float(row["end"]) - float(prev["start"]) <= MAX_LINE_SEC
                    and not (prev.get("locked") or row.get("locked"))):
                start = float(prev["start"])
                if vocals is not None:
                    # the line before may still be sounding a moment after its stated end ("Good." trails to 17.45 s although its
                    # end says 17.3 s): look for the voice only after that
                    before = out[-2] if len(out) >= 2 else None
                    lo = start if before is None else max(start, float(before["end"]) + AFTER_PREVIOUS_SEC)
                    onset = voiced_onset(vocals, lo, float(row["end"]) + 0.2)
                    if onset is not None and onset - start >= FRAGMENT_MOVE_MIN:
                        start = min(float(row["start"]), max(start, onset - 0.05))
                prev["start"] = round(start, 2)
                prev["end"] = row["end"]
                prev["text"] = _join_text(prev.get("text"), row.get("text"))
                if "words" in prev or "words" in row:
                    prev["words"] = list(prev.get("words") or []) + list(row.get("words") or [])
                joined += 1
                continue
        out.append(row)
    return out, joined


def _tail_start(tokens, source_language="en"):
    """Index of the first word of the unfinished last sentence, or None when the line holds no finished sentence."""
    cut = None
    for i, tok in enumerate(tokens[:-1]):
        if (SENT_END.search(tok) and tok.lower() not in NOT_END) if languages.get(source_language).code == "en" else lang_text.ends_sentence(" ".join(tokens[:i + 1]), source_language, profile="tidy"):
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


def move_unfinished_beginnings(rows, vocals=None, source_language="en"):
    """A sentence has one speaker: when a line ends in the middle of a sentence and the next line has another speaker,
    the unfinished beginning of that sentence moves to the line that finishes it."""
    moved = 0
    for k in range(len(rows) - 1):
        a, b = rows[k], rows[k + 1]
        if _same_voice(a, b) or a.get("locked") or b.get("locked"):
            continue
        text = str(a.get("text") or "").strip()
        if not text or _ends_sentence(text, source_language=source_language) or INTERRUPTED.search(text) or not str(b.get("text") or "").strip():
            continue
        tokens = _tokens(text)
        cut = _tail_start(tokens, source_language=source_language)
        if cut is None or len(tokens) - cut > MAX_TAIL_WORDS:
            continue                            # no finished sentence before it (an interruption), or too long to be a stray beginning
        shift_tail(a, b, cut, vocals)
        moved += 1
    return moved


CUT_FIELDS = ("cut_check", "cut_reasons")     # cut_check = "start", "end" or "both": which edge of this line is next to a cut that may be wrong; dropped when the person edits or confirms


def clear_flags(rows):
    for r in rows:
        for k in CUT_FIELDS:
            r.pop(k, None)


def flag_cut(a, b, reason):
    """Marks the cut between line a and the next line b as one a person should look at (a: its end, b: its start)."""
    for row, side in ((a, "end"), (b, "start")):
        row["cut_check"] = side if row.get("cut_check") in (None, side) else "both"     # a line between two doubtful cuts: both
        reasons = list(row.get("cut_reasons") or [])
        if reason not in reasons:
            reasons.append(reason)
        row["cut_reasons"] = reasons


def flag_doubtful_cuts(rows, source_language="en"):
    """After the fixes above: a cut between two speakers that still falls in the middle of a sentence (the line before ends
    without a full stop and without a dash, or the line after begins with a small letter) is shown to the person."""
    flagged = 0
    for k in range(len(rows) - 1):
        a, b = rows[k], rows[k + 1]
        if _same_voice(a, b):
            continue
        ta, tb = str(a.get("text") or "").strip(), str(b.get("text") or "").strip()
        if not ta or not tb:
            continue
        open_end = not _ends_sentence(ta, source_language=source_language) and not INTERRUPTED.search(ta)
        small_start = tb[0].isalpha() and tb[0].islower() and not _ends_sentence(ta, source_language=source_language)
        if open_end or small_start:
            flag_cut(a, b, "open_end" if open_end else "small_start")
            flagged += 1
    return flagged


def tidy_lines(rows, vocals=None, source_language="en"):
    """Returns (rows, {"joined": n, "moved": n, "flagged": n, "fragments": n}). The rows are changed in place and renumbered."""
    report = {"joined": 0, "moved": 0, "flagged": 0}
    try:
        ordered = sorted(rows, key=lambda r: (float(r["start"]), float(r["end"])))
        merged, report["joined"] = merge_split_phrases([dict(r) for r in ordered], source_language=source_language)
        merged, report["fragments"] = merge_fragments(merged, vocals, source_language=source_language)
        report["moved"] = move_unfinished_beginnings(merged, vocals, source_language=source_language)
        clear_flags(merged)
        report["flagged"] = flag_doubtful_cuts(merged, source_language=source_language)
        for i, r in enumerate(merged):
            r["segment_id"] = f"seg_{i}"
        return merged, report
    except Exception:
        return rows, {"joined": 0, "moved": 0, "flagged": 0, "fragments": 0, "error": True}
