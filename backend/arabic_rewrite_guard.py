"""Conservative checks for explicit gender reversals in an automatic rewrite.

This is not a complete Arabic grammar checker. It rejects detectable changes;
it never edits the user's text or guesses gender from a neighbouring speaker.
"""
import re
import unicodedata

_WORDS = re.compile(r"[\u0621-\u064a\u066e-\u06d3\u064b-\u065f\u0670]+")
_MARKS = re.compile(r"[\u064b-\u065f\u0670\u0640]")
_ADDRESS = {"لك", "بك", "منك", "إليك", "اليك", "عليك", "عنك", "فيك", "معك",
            "عندك", "لديك", "إنك", "انك", "أنك", "نفسك", "وحدك", "سيادتك",
            "سعادتك", "حضرتك"}
_TITLES = {"قاضي", "طبيب", "دكتور", "معلم", "مدير", "رئيس", "وزير", "سفير", "أستاذ"}
_SELF_ADJECTIVES = {"واضح", "مستعد", "متأكد", "مسرور", "سعيد", "آسف", "موافق",
                    "واثق", "مرتاح", "متعب", "مشغول", "قلق", "مطمئن", "خائف", "متأخر"}


def _plain(text):
    return _MARKS.sub("", unicodedata.normalize("NFC", str(text or "")))


def _unprefix(word):
    return word[1:] if word[:1] in ("و", "ف") else word


def _marks(text):
    found = set()
    for token in _WORDS.findall(unicodedata.normalize("NFC", str(text or ""))):
        plain = _plain(token)
        word = plain if plain in _ADDRESS else _unprefix(plain)
        # Only unambiguous address forms; e.g. شباكَ is not a pronoun suffix.
        if word in _ADDRESS:
            ending = re.search(r"ك([\u064b-\u065f\u0670]*)$", token)
        elif word in ("أنت", "انت"):
            ending = re.search(r"ت([\u064b-\u065f\u0670]*)$", token)
        else:
            continue
        if ending:
            if "ِ" in ending[1]:
                found.add("female")
            if "َ" in ending[1]:
                found.add("male")
    return found


def _titles(text):
    plain = _plain(text)
    found = set()
    for word in re.findall(r"[\u0621-\u064a]+", plain):
        word = _unprefix(word)
        if word == "سيدتي":
            found.add("female")
        elif word == "سيدي":
            found.add("male")
    # A title is evidence only inside a direct-address construction.
    for match in re.finditer(r"(?:^|\s)(?:و|ف)?(?:يا|سيادة|سعادة|حضرة)\s+([\u0621-\u064a]+)", plain):
        word = match[1].removeprefix("ال")
        if word in _TITLES:
            found.add("male")
        elif word.endswith("ة") and word[:-1] in _TITLES:
            found.add("female")
    return found


def address_gender(text):
    """A single explicit Arabic address gender, or unknown for mixed/neutral text."""
    found = _marks(text) | _titles(text)
    return next(iter(found)) if len(found) == 1 else ""


def _self_forms(text):
    # Do not infer gender from every final ة or from a noun: أنا شخص واضح is valid.
    plain = _plain(text)
    found = set()
    for match in re.finditer(r"(?:^|\s)(?:و|ف)?(?:أنا|انا|لأكون|لاكون|أكون|اكون)\s+([\u0621-\u064a]+)", plain):
        word = match[1]
        if word.endswith("ا") and word[:-1] in _SELF_ADJECTIVES:
            word = word[:-1]  # written alif after accusative tanween, e.g. واضحًا
        if word in _SELF_ADJECTIVES:
            found.add("male")
        elif word.endswith("ة") and word[:-1] in _SELF_ADJECTIVES:
            found.add("female")
    return found


def conflict(reviewed, candidate, speaker_gender=None, addressee_gender=None):
    """Reason to keep the reviewed wording; an empty string means no known conflict.

    Explicit reviewed address forms take precedence over a guessed addressee.
    Mixed address forms are not forced into a single gender. This can reject a
    valid paraphrase; retaining the reviewed text is safer than silently changing it.
    """
    expected = address_gender(reviewed)
    old_address = _marks(reviewed) | _titles(reviewed)
    if not expected and not old_address and addressee_gender in ("male", "female"):
        expected = addressee_gender
    if expected:
        opposite = "male" if expected == "female" else "female"
        if opposite in ((_marks(candidate) | _titles(candidate)) - old_address):
            return "address gender changed"
    if speaker_gender in ("male", "female"):
        opposite = "male" if speaker_gender == "female" else "female"
        if opposite in (_self_forms(candidate) - _self_forms(reviewed)):
            return "speaker gender changed"
    return ""
