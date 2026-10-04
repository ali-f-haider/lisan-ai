"""Subtitle files (.srt / .vtt) made from the lines of a long dub.

The lines (English text, Arabic text, start, end) are the ones the user reviewed, so the subtitles match the dubbed
audio. Arabic subtitles are written WITHOUT the vowel marks (tashkeel): they are there to help the voice, a reader
does not want them on screen. Free of charge, nothing here calls an AI service.
"""
import re

MAX_LINE = 42          # characters on one subtitle line
MAX_CUE_LINES = 2      # lines on screen at once (one language)
MIN_CUE = 1.0          # seconds: a subtitle shorter than this is hard to read, so a long line is not cut into pieces below it
MIN_GAP = 0.04         # seconds between one subtitle and the next
LANGS = ("ar", "en", "both")
FORMATS = ("srt", "vtt")

_MARKS = re.compile("[ً-ٰٟ]")
_WS = re.compile(r"\s+")
RLM = "‏"         # right-to-left mark: keeps the punctuation of an Arabic line on the correct side in video players


def clean_arabic(t):
    """Arabic for the screen: no vowel marks, one space between words."""
    return _WS.sub(" ", _MARKS.sub("", str(t or ""))).strip()


def clean_english(t):
    return _WS.sub(" ", str(t or "")).strip()


def wrap(text, width=MAX_LINE):
    """Greedy word wrap into lines of at most `width` characters (a longer single word stays whole)."""
    lines, cur = [], ""
    for w in text.split(" "):
        if not w:
            continue
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _balance(lines):
    """Two lines of nearly equal length read better than one long and one very short line."""
    if len(lines) != 2:
        return lines
    words = (lines[0] + " " + lines[1]).split(" ")
    best, best_gap = lines, abs(len(lines[0]) - len(lines[1]))
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) <= MAX_LINE and len(b) <= MAX_LINE and abs(len(a) - len(b)) < best_gap:
            best, best_gap = [a, b], abs(len(a) - len(b))
    return best


def _pieces(text, start, end):
    """One row -> one or more (start, end, [lines]). A text that does not fit on MAX_CUE_LINES lines is cut at the
    word nearest to a sentence end, and the time is shared in proportion to the number of characters."""
    cap = MAX_LINE * MAX_CUE_LINES
    dur = max(0.0, end - start)
    n = max(1, -(-len(text) // cap))                 # pieces needed (ceiling)
    n = max(1, min(n, int(dur // MIN_CUE) or 1))     # but never pieces shorter than MIN_CUE
    if n == 1:
        lines = wrap(text)
        if len(lines) > MAX_CUE_LINES:               # too long for the time: keep it whole, three lines are still readable
            return [(start, end, lines)]
        return [(start, end, _balance(lines))]
    words = text.split(" ")
    target = len(text) / n
    chunks, cur, count = [], [], 0
    for w in words:
        cur.append(w)
        count += len(w) + 1
        ends_sentence = w[-1:] in ".!?؟…،,;:"
        if len(chunks) < n - 1 and (count >= target or (ends_sentence and count >= target * 0.7)):
            chunks.append(" ".join(cur))
            cur, count = [], 0
    if cur:
        chunks.append(" ".join(cur))
    total = sum(len(c) for c in chunks) or 1
    out, t = [], start
    for c in chunks:
        d = dur * len(c) / total
        out.append((t, t + d, _balance(wrap(c))))
        t += d
    return out


def build_cues(rows, lang="ar"):
    """rows -> [(start, end, [lines])] in time order. lang: ar, en or both."""
    lang = lang if lang in LANGS else "ar"
    items = []
    for r in sorted(rows or [], key=lambda x: (float(x.get("start") or 0), float(x.get("end") or 0))):
        try:
            a, b = float(r.get("start") or 0), float(r.get("end") or 0)
        except (TypeError, ValueError):
            continue
        if b <= a:
            b = a + 1.5
        ar, en = clean_arabic(r.get("arabic_text")), clean_english(r.get("text"))
        if lang == "ar":
            if not ar:
                continue
            for s, e, ln in _pieces(ar, a, b):
                items.append([s, e, [RLM + x + RLM for x in ln]])
        elif lang == "en":
            if not en:
                continue
            for s, e, ln in _pieces(en, a, b):
                items.append([s, e, ln])
        else:
            if not ar and not en:
                continue
            lines = []
            if ar:
                lines += [RLM + x + RLM for x in _balance(wrap(ar))]
            if en:
                lines += _balance(wrap(en))
            items.append([a, b, lines])
    # never overlap the next subtitle; a gap that is too small to keep MIN_GAP shortens this one, not the next
    for i in range(len(items) - 1):
        limit = items[i + 1][0] - MIN_GAP
        if items[i][1] > limit:
            items[i][1] = max(items[i][0] + 0.2, limit)
    return [(s, e, ln) for s, e, ln in items if ln]


def _stamp(sec, sep):
    ms = int(round(max(0.0, sec) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(cues):
    out = []
    for i, (s, e, lines) in enumerate(cues, 1):
        out.append(f"{i}\n{_stamp(s, ',')} --> {_stamp(e, ',')}\n" + "\n".join(lines) + "\n")
    return "\n".join(out)


def to_vtt(cues):
    out = ["WEBVTT\n"]
    for s, e, lines in cues:
        out.append(f"{_stamp(s, '.')} --> {_stamp(e, '.')}\n" + "\n".join(lines) + "\n")
    return "\n".join(out)


def export(rows, lang="ar", fmt="srt"):
    """Returns (text, number_of_subtitles). The text starts with a UTF-8 byte-order mark when it is written to a file."""
    cues = build_cues(rows, lang)
    return (to_vtt(cues) if fmt == "vtt" else to_srt(cues)), len(cues)
