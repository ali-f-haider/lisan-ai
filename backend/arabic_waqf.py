"""Pausal form (waqf) of Arabic text with tashkeel, for the VOICE ONLY.

The written text (the project, the editor, the subtitles, the translation files) is never changed by this module. `pausal(text)` returns a
temporary copy that is sent to the voice engine: the last word before a real stop is written the way it is spoken when the speaker stops.

    كِتَابُهُ.   ->  كِتَابُهْ.          a short vowel on the last letter becomes a sukoon
    كِتَابٌ      ->  كِتَابْ            tanween damma / kasra is dropped, sukoon
    بَيْتٌ       ->  بَيْتْ             a ت stays a ت
    حَقٌّ        ->  حَقّ              the shadda is kept
    شُكْرًا      ->  شُكْرَا            tanween fatha of a SPECIAL word (the list below: thanks, greetings, adverbs): the nun is dropped and the alef is spoken
    كِتَابًا      ->  كِتَابْ            any other word with a tanween fatha: no alef, a sukoon (also مَاءً -> مَاءْ)
    فَتًى        ->  فَتَى              (alef maqsura keeps its long vowel; special: جِدًّا -> جِدَّا, مَسَاءً -> مَسَاءَا)
    مَدْرَسَةٌ   ->  مَدْرَسَهْ         ة is a silent ه: also with tanween fatha
    امْرَأَةٌ ... لَافِتَةٌ   the ة of these words is a silent ه everywhere in the voice copy (not before an ال word: امْرَأَةُ الرَّجُلِ)
    هُنَا، فِي، يَدْعُو   unchanged      long vowels stay long
    قَاضٍ، دَاعٍ         unchanged      defective nouns (منقوص) need their own treatment: never turned into قَاضْ

Where a stop is: the stop is real at . ! ؟ ? … (a dot inside a number, a link or an abbreviation is not). A comma, semicolon or colon is a stop only
when the line is set to "stop". The end of a line is a stop only when the line says so: it ends a sentence, or the original speaker really paused
(`gap` = seconds of silence after the line, 0.5 s or more), or the line is set to "stop". A sentence that continues in the next line is joined
(wasl): its last word keeps its vowel. A line set to "join" never gets the stop form at its end.

Only words that carry tashkeel are touched. Only the marks on the last letter change (and ة -> ه, and an alef is added after a tanween fatha that
has none). The function is idempotent and never raises: on a problem the text comes back unchanged."""
import re

_FATHA, _DAMMA, _KASRA = "َ", "ُ", "ِ"
_TAN_FATHA, _TAN_DAMMA, _TAN_KASRA = "ً", "ٌ", "ٍ"
_SHADDA, _SUKOON = "ّ", "ْ"
_VOWELS = {_FATHA, _DAMMA, _KASRA, _TAN_FATHA, _TAN_DAMMA, _TAN_KASRA}
_LONG = set("اآىٱ")            # alef, alef with madda, alef maqsura, alef wasla: a long vowel, never gets a sukoon
_MARK = re.compile("[ً-ٰٕ]")
_LETTER = "ء-يٱ-ۓ"
_WORD = re.compile(f"[{_LETTER}][{_LETTER}ً-ٰٕـ]*")
_ARABIC_LETTER = re.compile(f"[{_LETTER}]")
_CLOSERS = "\"'»”’)]}」"            # what may sit between the last word and the stop itself
_STRONG = ".!؟?…"
_WEAK = "،,؛;:—–"
GAP_PAUSE = 0.5                                  # seconds of silence after a line that make its end a real stop
MODES = ("auto", "stop", "join")

# The special words (thanks, greetings, adverbs of manner / time / degree / place ...): their tanween fatha is spoken as an alef at a stop ("شُكْرَا").
# Every OTHER word with a tanween fatha (كِتَابًا, مَاءً) gets no alef at a stop: a sukoon ("كِتَابْ"). Set ORDINARY_TANWEEN_FATHA = "alef" to give every
# word the alef again. Words are written without marks; a leading و / ف is ignored. More words: one per line in waqf_extra_words.txt next to this file.
ORDINARY_TANWEEN_FATHA = "sukoon"
_SPECIAL = """
شكرا أهلا سهلا مرحبا عفوا عذرا وداعا حسنا مهلا صبرا رفقا عجبا تبا
جدا كثيرا قليلا جزيلا تماما كليا جزئيا تقريبا نسبيا
حقا فعلا صدقا يقينا قطعا حتما طبعا
دائما أبدا أحيانا غالبا نادرا مرارا تكرارا مجددا
غدا صباحا مساء ليلا نهارا قريبا لاحقا سابقا حاليا حديثا قديما أخيرا أولا ثانيا ثالثا رابعا
سريعا بطيئا فورا عاجلا تدريجيا تلقائيا عمدا سهوا سرا جهرا علنا
معا جميعا سويا منفردا منفصلا متصلا تباعا
أيضا بعيدا يمينا يسارا أماما خلفا جانبا
عموما خصوصا تحديدا أساسا أصلا إجمالا
عمليا نظريا علميا منطقيا واقعيا رسميا شخصيا قانونيا اجتماعيا اقتصاديا سياسيا تقنيا لغويا
شيئا يوما
""".split()
_special_cache = []


def _norm(bare):
    return bare.translate(str.maketrans("أإآٱ", "اااا"))


def _special_words():
    if not _special_cache:
        words = set(_norm(w) for w in _SPECIAL)
        try:
            import os
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "waqf_extra_words.txt")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.split("#", 1)[0].strip()
                        for w in line.split():
                            words.add(_norm(_MARK.sub("", w)))
        except Exception:
            pass
        _special_cache.append(words)
    return _special_cache[0]


def _is_special(g):
    """True for a word of the special list (with or without a leading و / ف) and for the adverb of manner in -يًّا (رَسْمِيًّا, تِقْنِيًّا)."""
    bare = _norm(_bare(g))
    words = _special_words()
    if bare in words or (len(bare) > 3 and bare[0] in "وف" and bare[1:] in words):
        return True
    return len(g) >= 4 and g[-1][0] == "ا" and g[-2][0] == "ي" and _SHADDA in g[-2][1]


# Words whose final ة is not spoken at all, wherever they are in the line (not only at a stop): امرأة, لافتة. A prefix (و ف ب ل ك, ال) is allowed.
# A word followed by an ال word (the idafa: "امْرَأَةُ الرَّجُلِ") keeps its ة, which is spoken there as a ت.
_SILENT_TA_STEMS = ("امرأة", "مرأة", "لافتة")
_SILENT_TA = re.compile("^[وفبلك]{0,2}(?:ال)?(?:%s)$" % "|".join(_norm(w) for w in _SILENT_TA_STEMS))
_NEXT_AL = re.compile("\\s+(?:و|ف)?[اأإٱ][ً-ٰٕ]*ل")


def _silent_ta(text):
    out, last = [], 0
    for m in _WORD.finditer(text):
        word = m.group(0)
        g = _groups(word)
        if len(g) < 3 or g[-1][0] != "ة":
            continue
        if not _SILENT_TA.match(_norm(_bare(g))):
            continue
        if _NEXT_AL.match(text, m.end()):
            continue
        g[-1] = ("ه", [_SUKOON] if _MARK.search(word) else [])
        out.append(text[last:m.start()])
        out.append(_join(g))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


# defective nouns (اسم منقوص): the written tanween sits on a letter whose ي is not written; they are left exactly as they are
_NAQIS = {"قاض", "داع", "هاد", "باق", "غال", "عال", "ساع", "راع", "ماش", "وال", "واد", "ثان", "لاه", "سام", "ناج", "رام", "باغ", "هاو"}


def gap_after(end, next_start):
    """Seconds of silence after a line that ends at `end`; the next line starts at `next_start` (None: it is the last line, a long silence)."""
    try:
        if next_start is None:
            return 99.0
        return max(0.0, float(next_start) - float(end))
    except Exception:
        return None


def clean_mode(value):
    v = str(value or "").strip().lower()
    return v if v in MODES else "auto"


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


def _join(g):
    return "".join(letter + "".join(marks) for letter, marks in g)


def _bare(g):
    return "".join(letter for letter, _ in g)


def _without_vowels(marks):
    return [m for m in marks if m not in _VOWELS]


def _pausal_word(word):
    g = _groups(word)
    if len(g) < 2 or not _MARK.search(word):
        return word
    letter, marks = g[-1]
    prev_letter, prev_marks = g[-2]
    # 1. ة is a silent ه at a stop, whatever it carries (also a tanween fatha)
    if letter == "ة":
        g[-1] = ("ه", [_SUKOON])
        return _join(g)
    # 2. tanween fatha: a special word says the alef ("شُكْرَا"), any other word gets a sukoon and no alef ("كِتَابْ")
    if _TAN_FATHA in marks or _TAN_FATHA in prev_marks:
        alef = ORDINARY_TANWEEN_FATHA == "alef" or _is_special(g)

        def settle(i):                                                      # the letter at g[i] ends the word as a plain consonant
            lt, mk = g[i]
            mk = _without_vowels(mk)
            if _SHADDA not in mk and _SUKOON not in mk:
                mk = mk + [_SUKOON]
            g[i] = (lt, mk)
        if letter == "ا" and _TAN_FATHA in marks:                            # شُكْراً: the tanween is written on the alef
            if alef:
                g[-1] = (letter, _without_vowels(marks))
                g[-2] = (prev_letter, _without_vowels(prev_marks) + [_FATHA])
            else:
                g.pop()
                settle(len(g) - 1)
            return _join(g)
        if _TAN_FATHA in prev_marks and letter in "اى":                       # شُكْرًا, كِتَابًا, فَتًى, جِدًّا
            if letter == "ى" or alef:
                g[-2] = (prev_letter, _without_vowels(prev_marks) + [_FATHA])
            else:
                g.pop()
                settle(len(g) - 1)
            return _join(g)
        if _TAN_FATHA in marks and letter not in _LONG:                      # مَاءً: no alef is written
            if alef:
                g[-1] = (letter, _without_vowels(marks) + [_FATHA])
                g.append(("ا", []))
            else:
                settle(len(g) - 1)
            return _join(g)
        return word                                                          # anything else: not touched
    # 3. tanween damma / kasra of a defective noun: not touched
    if (_TAN_DAMMA in marks or _TAN_KASRA in marks) and _bare(g) in _NAQIS:
        return word
    new_marks = _without_vowels(marks)
    had_vowel = any(m in _VOWELS for m in marks)
    if had_vowel:
        if _SHADDA in new_marks:
            pass                                   # a doubled final consonant: the shadda alone
        elif letter in _LONG:
            pass                                   # a long vowel letter that carries a mark: the mark is dropped, nothing to add
        else:
            new_marks.append(_SUKOON)
    elif not marks and letter not in _LONG and letter not in "وي" and letter != "ء" and len(g) >= 3:
        # an unmarked last consonant of a word that is marked elsewhere: say it explicitly
        new_marks.append(_SUKOON)
    if new_marks.count(_SUKOON) > 1:
        new_marks = [m for i, m in enumerate(new_marks) if m != _SUKOON or i == new_marks.index(_SUKOON)]
    g[-1] = (letter, new_marks)
    return _join(g)


def _stop_after(text, pos):
    """What follows the word that ends at `pos`: ('strong'|'weak'|'end'|None, position after the stop)."""
    n, i = len(text), pos
    while i < n and text[i] in _CLOSERS:
        i += 1
    if i >= n:
        return "end", i
    c = text[i]
    if c in _STRONG:
        if c == ".":
            j = i + 1
            while j < n and text[j] == ".":
                j += 1
            if j < n and not (text[j].isspace() or text[j] in _CLOSERS):
                return None, i                     # a dot inside a link, a number or an abbreviation
        return "strong", i + 1
    if c in _WEAK:
        return "weak", i + 1
    if c.isspace():
        j = i
        newline = False
        while j < n and text[j].isspace():
            newline = newline or text[j] in "\r\n"
            j += 1
        if j >= n or newline:
            return "end", j
    return None, i


def pausal(text, mode="auto", gap=None):
    """Copy of `text` for the voice: the last Arabic word before every real stop in its pausal form.
    mode: "auto" (decide by the punctuation and the silence after the line), "stop" (every punctuation mark and the end of the line are stops),
    "join" (the end of the line is not a stop). gap: seconds of silence after the line, None when unknown."""
    try:
        if not isinstance(text, str):
            return text
        text = _silent_ta(text)
        if not _MARK.search(text):
            return text
        mode = clean_mode(mode)
        out, last = [], 0
        for m in _WORD.finditer(text):
            kind, after = _stop_after(text, m.end())
            if kind is None:
                continue
            tail = not _ARABIC_LETTER.search(text, after)          # nothing more to say after this word: it is the end of the line
            if tail:
                if mode == "join":
                    stop = False
                elif mode == "stop" or kind == "strong":
                    stop = True
                else:
                    stop = gap is not None and gap >= GAP_PAUSE
            elif kind == "strong":
                stop = True
            else:
                stop = mode == "stop"
            if not stop:
                continue
            out.append(text[last:m.start()])
            out.append(_pausal_word(m.group(0)))
            last = m.end()
        out.append(text[last:])
        return "".join(out)
    except Exception:
        return text
