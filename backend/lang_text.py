"""Pure source-text tools; English profiles preserve their OWN legacy call sites.

Tokens partition the original string, including spacing and punctuation. Their
matching forms may differ; original text is never reconstructed from those
forms. Language evidence is advisory, never an audio-language detector.
"""
from dataclasses import dataclass
import html
import re
import unicodedata
import languages


TIDY = "tidy"          # line_tidy and line_planner
SUBTITLE = "subtitle"  # subs_align and subtitle import
_TIDY_END = re.compile(r"[.!?؟…][\"'”’)\]]*\s*$")
_SUB_END = re.compile(r"(?<![.…])[.!?؟][\"'”’)\]]*$")
_EN_ABBR = frozenset(languages.get_record("en").abbreviations)
_LATIN_LEGACY = re.compile(r"[A-Za-zÀ-ɏ]")
_LETTER_LEGACY = re.compile(r"[^\W\d_]", re.UNICODE)
_CLOSERS = '\"\'”’)]}»“'


def _text(value):
    return value if isinstance(value, str) else ""


def fold_accents(text, lang=None):
    """Matching only: strip combining accents and expand common Latin ligatures."""
    text = _text(text)
    if languages.get_record(lang).code == "pl":
        text = text.translate(str.maketrans({"ł": "l", "Ł": "L"}))
    text = text.replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae").replace("Æ", "AE")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def legacy_norm(text):
    # Exact subs_align.norm for string inputs. English accent folding is NOT new.
    t = _text(text).lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"[^\w']", "", t, flags=re.U)
    return t.replace("'", "").replace("_", "")


def legacy_surface_tokens(text):
    text = re.sub(r"(?<=\S)([—–])(?=\S)", r"\1 ", _text(text))
    out = []
    for t in text.split():
        if legacy_norm(t):
            out.append(t)
        elif out:
            out[-1] += t
    return out


_MARKUP_TAGS = r"(?:font|i|b|u|s|em|strong|span|br|p|div|c|v|lang|ruby|rt|rb)"


def strip_markup(text):
    """Subtitle formatting out of one line. Entities are decoded until nothing changes (a file that was escaped twice shows
    its tags as text otherwise), then every tag goes -- also one that was cut off before its ">" and the left-over
    start of an attribute on a following line (`#ffff00">`)."""
    s = _text(text)
    for _ in range(3):
        t = html.unescape(s)
        if t == s:
            break
        s = t
    s = re.sub(r"</?" + _MARKUP_TAGS + r"\b[^>]*>", "", s, flags=re.I)
    # a tag cut off before its ">": only the tag and its attributes go, the words after it stay
    s = re.sub(r"</?" + _MARKUP_TAGS + r"\b(?:\s*[\w:-]+\s*=\s*[\"']?[#\w.%-]*[\"']?)*", "", s, flags=re.I)
    s = re.sub(r"<[^>]*>", "", s)
    return re.sub(r"^[#\w]{0,12}[\"']\s*>", "", s)


def legacy_clean_line(text):
    """Exact string-input copy of subs_align._clean_line, including its quirks."""
    s = strip_markup(text)
    s = re.sub(r"\{[^}]*\}", "", s)
    s = s.replace("‎", "").replace("‏", "").replace("‪", "").replace("‬", "")
    if "♪" in s or "♫" in s or "♩" in s:
        return "", False
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = s.strip()
    dash = False
    m = re.match(r"^[" + languages.get_record("en").dialogue_dashes + r"]+\s*", s)
    if m:
        dash = True
        s = s[m.end():]
    s = re.sub(r"^[A-Z][A-Z0-9 .'\-]{1,24}:\s+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return (s, dash) if re.search(r"\w", s) else ("", dash)


def ends_sentence(text, lang=None, *, profile=TIDY):
    text = _text(text)
    language = languages.get_record(lang)
    if language.code == "en":
        if profile == SUBTITLE:
            # Deliberately no rstrip: the existing subtitle helper rejects it.
            return bool(_SUB_END.search(text)) and text.strip().lower() not in _EN_ABBR
        t = text.strip()
        return bool(_TIDY_END.search(t)) and t.split()[-1].lower() not in _EN_ABBR if t else False
    t = text.rstrip().rstrip(_CLOSERS).rstrip()
    if not t or t[-1] not in language.sentence_end:
        return False
    if profile == SUBTITLE and (t.endswith("...") or t.endswith("…")):
        return False
    folded = fold_accents(t, language).casefold()
    # These enumeration endings may supply the sentence stop themselves. New
    # sources only: old-language punctuation remains byte-compatible.
    terminal = ("etc.", "enz.") if language.code == "nl" else ("itd.", "itp.") if language.code == "pl" else ()
    if any(re.search(r"(?<!\w)" + re.escape(a) + r"$", folded) for a in terminal) and len(t.split()) > 1:
        return True
    for abbreviation in language.abbreviations:
        # A timed word arrives before the remainder of a multiword abbreviation.
        # Its registered prefixes are conservative non-stops too (e.g. z. -> z. B.).
        pieces = abbreviation.split()
        for count in range(1, len(pieces) + 1):
            prefix = " ".join(pieces[:count])
            pat = re.escape(fold_accents(prefix, language).casefold()).replace(r"\ ", r"\s*")
            if re.search(r"(?<!\w)" + pat + r"$", folded):
                return False
    if language.code in ("de", "pl") and re.search(r"(?<![\w.,])\d+\.$", t):
        return False  # conservative ordinal ambiguity; documented, needs review
    return True


def starts_sentence(text, lang=None):
    """Capital/inverted-mark hint only, not proof of a new speaker."""
    text = _text(text).lstrip()
    if languages.get_record(lang).code == "en":
        # Mirrors the first-character case test at line_tidy.flag_doubtful_cuts.
        return bool(text) and text[0].isalpha() and not text[0].islower()
    marks = languages.get_record(lang).inverted_marks
    if marks and text.startswith(tuple(marks)):
        return True
    text = text.lstrip('"\'“‘«„»([{ ')
    return bool(text) and text[0].isalpha() and text[0].isupper()


def _protected_dots(text, language):
    protected = set()
    for abbreviation in language.abbreviations:
        pat = re.escape(abbreviation).replace(r"\ ", r"\s*")
        for m in re.finditer(r"(?<!\w)" + pat + r"(?!\w)", text, re.I):
            dots = [i for i in range(m.start(),m.end()) if text[i] == "."]
            terminal = ("etc.", "enz.") if language.code == "nl" else ("itd.", "itp.") if language.code == "pl" else ()
            if abbreviation in terminal and dots:
                tail = text[m.end():].lstrip(_CLOSERS).lstrip()
                if not tail or tail[0].isupper():
                    dots.pop()
            protected.update(dots)
    for m in re.finditer(r"\d[.,]\d", text):
        protected.add(m.start()+1)
    if language.code in ("de", "pl"):
        for m in re.finditer(r"(?<![\w.,])\d+\.(?!\d)", text):
            protected.add(m.end()-1)
    return protected


def split_sentences(text, lang=None, *, profile=TIDY):
    """Lossless spans; join the return values to get the exact original string.

    There is no old string-level splitter. English uses its selected ending
    helper on whitespace-delimited words; existing timed-word splitters must
    keep their English branch unchanged during integration.
    """
    text = _text(text)
    if not text:
        return []
    language = languages.get_record(lang)
    if language.code == "en":
        cuts = []
        for m in re.finditer(r"\S+\s*", text):
            if ends_sentence(m.group().rstrip(), "en", profile=profile):
                cuts.append(m.end())
    else:
        protected = _protected_dots(text, language)
        cuts, i = [], 0
        while i < len(text):
            if text[i] not in language.sentence_end or i in protected:
                i += 1
                continue
            j = i+1
            while j < len(text) and text[j] in language.sentence_end + _CLOSERS:
                j += 1
            # A closing guillemet can follow a French no-break/thin space.
            # Include it in the same span without rewriting that spacing.
            while j < len(text):
                k = j
                while k < len(text) and text[k].isspace():
                    k += 1
                if k < len(text) and text[k] in _CLOSERS:
                    j = k + 1
                else:
                    break
            if profile == SUBTITLE and all(c in ".…" + _CLOSERS for c in text[i:j]) and ("…" in text[i:j] or "..." in text[i:j]):
                i = j
                continue
            if j < len(text) and not text[j].isspace():
                i = j
                continue
            while j < len(text) and text[j].isspace():
                j += 1
            cuts.append(j)
            i = j
    result, start = [], 0
    for end in cuts:
        if end > start:
            result.append(text[start:end])
            start = end
    if start < len(text):
        result.append(text[start:])
    return result or [text]


@dataclass(frozen=True)
class Token:
    original: str
    matching: str
    start: int
    end: int
    kind: str


def tokenize(text, lang=None):
    """Includes punctuation and gap tokens so rejoining NEVER loses a character.

    English matching follows existing row tokenisation, without accent folding.
    Other languages split elisions and compounds, keeping their separators as
    empty-matching tokens. Offsets are Python character offsets, not bytes.
    """
    text = _text(text)
    if not text:
        return []
    language = languages.get_record(lang)
    if language.code == "en":
        return [Token(m.group(), legacy_norm(m.group()) if not m.group().isspace() else "",
                      m.start(), m.end(), "gap" if m.group().isspace() else "word")
                for m in re.finditer(r"\s+|\S+", text)]
    out, i = [], 0
    while i < len(text):
        start = i
        cat = unicodedata.category(text[i])
        if cat[0] in "LMN":
            i += 1
            while i < len(text):
                c = text[i]
                if unicodedata.category(c)[0] in "LMN":
                    i += 1
                elif c in ".," and text[i-1].isdigit() and i+1<len(text) and text[i+1].isdigit():
                    i += 1
                elif c in language.apostrophes and i+1<len(text) and text[i+1].isalpha() and fold_accents(text[start:i], language).casefold() not in language.elisions:
                    i += 1
                else:
                    break
            original = text[start:i]
            matching = fold_accents(original, language).casefold().replace("’", "'").replace("‘", "'")
            out.append(Token(original, matching, start, i, "word"))
        else:
            i += 1
            out.append(Token(text[start:i], "", start, i, "gap" if text[start:i].isspace() else "punctuation"))
    return out


def normalise_for_match(text, lang=None):
    return " ".join(t.matching for t in tokenize(text,lang) if t.matching)


def _label(text):
    m = re.match(languages.get_record("fr").speaker_label, text)
    if m:
        value = m.group().split(":",1)[0].strip()
        if any(c.isalpha() for c in value) and value == value.upper():
            return value, m.end()
    return "", 0


def clean_subtitle_line(text, lang=None):
    if languages.get_record(lang).code == "en":
        return legacy_clean_line(text)
    language = languages.get_record(lang)
    s = strip_markup(text)
    s = re.sub(r"\{[^}]*\}", "", s)
    s = s.translate({ord(c): None for c in "\u200e\u200f\u202a\u202b\u202c\u202d\u202e"})
    if any(mark in s for mark in language.music_marks):
        return "", False
    def description(m):
        inner = m.group()[1:-1]
        return " " if any(re.search(pattern,inner) for pattern in language.sound_description) else m.group()
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)", description, s).strip()
    m = re.match(r"^[" + language.dialogue_dashes + r"]+\s*",s)
    dash = bool(m)
    if m:
        s = s[m.end():]
    label, end = _label(s)
    if label:
        s = s[end:]
    s = re.sub(r"\s+", " ", s).strip()
    return (s,dash) if any(c.isalnum() for c in s) else ("",dash)


def strip_subtitle_markup(text, lang=None):
    return clean_subtitle_line(text,lang)[0]


def speaker_change_in_cue(cue_lines, lang=None, *, has_previous=False):
    """Indexes of spoken cue lines with explicit change evidence.

    English dash logic matches build_stream(auto). A first dash starts a new
    unit only when an earlier cue has spoken words. Quotes alone prove nothing.
    """
    if not isinstance(cue_lines,(list,tuple)):
        return []
    out, spoken, last_label = [], bool(has_previous), ""
    for i, raw in enumerate(cue_lines):
        text, dash = clean_subtitle_line(raw,lang)
        if not text:
            continue
        label, _ = _label(_text(raw).lstrip(' -–—'))
        if spoken and (dash or (languages.get_record(lang).code != "en" and label and last_label and label != last_label)):
            out.append(i)
        spoken = True
        if label:
            last_label = label
    return out


def _script_share(text, script):
    letters = [c for c in _text(text) if unicodedata.category(c).startswith("L")]
    return sum(script in unicodedata.name(c, "") for c in letters) / len(letters) if letters else 0.0


def arabic_script_share(text):
    return _script_share(text,"ARABIC")


def latin_share(text):
    return _script_share(text,"LATIN")


def looks_like(text, lang=None):
    text = _text(text)
    language = languages.get_record(lang)
    tokens = [t.matching for t in tokenize(text,language) if t.matching]
    stop = {fold_accents(w, language).casefold() for w in language.stopwords}
    hits = [t for t in tokens if fold_accents(t, language).casefold() in stop]
    letters = [c for c in text if unicodedata.category(c).startswith("L")]
    share = latin_share(text)
    evidence = {"language":language.code,"letters":len(letters),"latin_share":share,
                "arabic_share":arabic_script_share(text),"tokens":len(tokens),
                "stopword_hits":len(hits),"distinct_stopword_hits":len(set(hits)),
                "method":"script and curated function words; advisory, not a probability"}
    if language.code == "en":
        # Existing text check: above one-half non-Latin letters flags a LINE.
        # Keep the aggregate row thresholds in lang_check untouched.
        old = _LETTER_LEGACY.findall(text)
        non = sum(not _LATIN_LEGACY.match(c) for c in old)/len(old) if old else 0
        evidence["legacy_non_latin_share"] = non
        return ("mismatch" if non > 0.5 else "compatible"), evidence
    if not letters:
        return "inconclusive",evidence
    if share < 0.5:
        return "mismatch", evidence
    if language.code in ("nl", "pl"):
        # Staged-source screen only. Legacy sources retain their exact evidence
        # and verdicts. Scores are curated lexical hints, not probabilities.
        rivals = {}
        own = set(hits)
        other_words = set()
        for record in languages.registered():
            if record.code == language.code:
                continue
            words = {fold_accents(w, language).casefold() for w in record.stopwords}
            rivals[record.code] = len(set(tokens) & words)
            other_words.update(words)
        distinct = len(own - other_words)
        strongest = max(rivals.values(), default=0)
        evidence.update(competing_distinct_hits=rivals, distinctive_hits=distinct,
                        thresholds="estimated lexical screen; no calibrated accuracy")
        if share < 0.8 or len(tokens) < 4:
            return "inconclusive", evidence
        if strongest >= 3 and strongest >= len(own) + 2:
            return "mismatch", evidence
        if len(own) < 3 or distinct < 2 or len(own) <= strongest:
            return "inconclusive", evidence
        return "compatible", evidence
    if share < 0.8 or len(tokens)<4 or len(set(hits))<2:
        return "inconclusive", evidence
    return "compatible", evidence
