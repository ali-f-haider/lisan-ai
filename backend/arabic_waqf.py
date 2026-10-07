"""Pausal form (waqf) of Arabic text with tashkeel.

A speaker who stops (at the end of a statement, at a comma, a colon, a question mark, or at the end of a line of the dub) does not
say the last vowel of the last word: كِتَابُهُ is said "kitābuh", not "kitābuhu". The ending carries a sukoon (or nothing), never a
fatha, a kasra or a damma. A diacritizer writes the full, connected form, so the voice then pronounces a short vowel at the end of every
sentence. `pausal(text)` rewrites the last word before every pause:

    كِتَابُهُ.   ->  كِتَابُهْ.          a short vowel on the last letter becomes a sukoon
    كِتَابٌ      ->  كِتَابْ            tanween damma / kasra is dropped, sukoon
    شُكْرًا      ->  شُكْرًا            a tanween fatha is an exception: it stays as it is (also جِدًّا, كَثِيرًا, أَبَدًا, رَحْمَةً)
    رَبِّ        ->  رَبّ               a shadda keeps only the shadda
    هُمْ، فِي، لَهَا   unchanged         (a sukoon or a long vowel is already a pausal ending)

Only words that carry tashkeel are touched (a word without any mark is left as it is), and the letters never change, only the marks on
the last letter. The function is idempotent. It never raises: on a problem the text comes back unchanged."""
import re

_FATHA, _DAMMA, _KASRA = "َ", "ُ", "ِ"
_TAN_FATHA, _TAN_DAMMA, _TAN_KASRA = "ً", "ٌ", "ٍ"
_SHADDA, _SUKOON = "ّ", "ْ"
_VOWELS = {_FATHA, _DAMMA, _KASRA, _TAN_FATHA, _TAN_DAMMA, _TAN_KASRA}
_LONG = set("اآىٱ")            # alef, alef with madda, alef maqsura, alef wasla: a long vowel, never gets a sukoon
_MARK = re.compile("[ً-ٰٕ]")
_LETTER = "ء-يٱ-ۓ"
_WORD = re.compile(f"[{_LETTER}][{_LETTER}ً-ٰٕـ]*")
# what may sit between the last word and the pause itself: closing quotes and brackets
_CLOSERS = "\"'»”’)]}」"
_PAUSE = re.compile(f"[{re.escape(_CLOSERS)}]*(?:[.،,؛;:!؟?…—–]|\\s*$|\\s*\\n)")


def _groups(word):
    """[(letter, [marks])] of a word (tatweel dropped)."""
    out = []
    for ch in word:
        if ch == "ـ":
            continue
        if _MARK.match(ch):
            if out:
                out[-1][1].append(ch)
        else:
            out.append((ch, []))
    return out


def _pausal_word(word):
    g = _groups(word)
    if len(g) < 2 or not _MARK.search(word):
        return word
    # A tanween fatha is an exception: the ending of شُكْرًا, جِدًّا, كَثِيرًا, أَبَدًا, رَحْمَةً... is already a pausal one ("ā") and is left exactly as it is.
    if any(_TAN_FATHA in marks for _, marks in g[-2:]):
        return word
    letter, marks = g[-1]
    new_marks = [m for m in marks if m not in _VOWELS]
    had_vowel = any(m in _VOWELS for m in marks)
    if had_vowel:
        if _SHADDA in new_marks:
            pass                                   # a doubled final consonant: the shadda alone
        elif letter in _LONG:
            pass                                   # a long vowel letter that carries a mark: the mark is dropped, nothing to add
        else:
            new_marks.append(_SUKOON)
    elif not marks and letter not in _LONG and letter not in "\u0648\u064A" and letter != "\u0621" and len(g) >= 3:
        # an unmarked last consonant of a word that is marked elsewhere: say it explicitly
        new_marks.append(_SUKOON)
    if new_marks.count(_SUKOON) > 1:
        new_marks = [m for i, m in enumerate(new_marks) if m != _SUKOON or i == new_marks.index(_SUKOON)]
    g[-1] = (letter, new_marks)
    return _join(g)


def _join(g):
    return "".join(letter + "".join(marks) for letter, marks in g)


def pausal(text):
    """Text with the last Arabic word before every pause (punctuation, end of text, end of line) in its pausal form."""
    try:
        if not isinstance(text, str) or not _MARK.search(text):
            return text
        out, last = [], 0
        for m in _WORD.finditer(text):
            if not _PAUSE.match(text, m.end()):
                continue
            out.append(text[last:m.start()])
            out.append(_pausal_word(m.group(0)))
            last = m.end()
        out.append(text[last:])
        return "".join(out)
    except Exception:
        return text
