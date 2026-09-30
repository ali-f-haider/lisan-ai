"""Checks that the speech of a video really is English before it is dubbed.

The transcription is fixed to English. On Arabic (or any other language) speech that setting makes the
recogniser either translate what it hears into English (so the "English" text is a guess) or write the
words in their own alphabet. Dubbing such a video gives wrong text and, for Arabic, dubs Arabic into
Arabic. This module only WARNS; it never blocks and never changes a transcript. Two independent checks:

1. Language detection (Whisper's own detector) on up to a few 30 s stretches of real speech.
2. A model-free look at the English text: lines written mostly in Arabic / other non-Latin letters.

Every function is best-effort and never raises.
"""
import os
import re

ENABLED = os.environ.get("LANG_CHECK", "1") != "0"     # LANG_CHECK=0 switches the check off

SAMPLE = 16000
WINDOW_SEC = 30
MIN_SPEECH_SEC = 4.0          # a stretch with less speech than this is not judged
FLAG_PROB = 0.75              # not English AND at least this sure -> the stretch is flagged
MIN_FLAGGED_SPEECH_SEC = 8.0  # ...and the flagged stretches must hold at least this much speech
MIN_FLAGGED_SHARE = 0.15      # ...and at least this share of all the speech that was judged
MAX_WINDOWS = 8

LANG_NAMES = {
    "ar": "Arabic", "fa": "Persian", "ur": "Urdu", "hi": "Hindi", "tr": "Turkish", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "pt": "Portuguese", "ru": "Russian", "nl": "Dutch", "pl": "Polish",
    "uk": "Ukrainian", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "he": "Hebrew", "id": "Indonesian",
    "sv": "Swedish", "el": "Greek", "ro": "Romanian", "cs": "Czech", "bn": "Bengali", "ps": "Pashto",
    "ku": "Kurdish", "az": "Azerbaijani", "sw": "Swahili", "ta": "Tamil", "vi": "Vietnamese", "th": "Thai",
}


def lang_name(code):
    return LANG_NAMES.get(str(code or "").lower(), str(code or "another language"))


def load_audio(path):
    from faster_whisper.audio import decode_audio
    return decode_audio(str(path), sampling_rate=SAMPLE)


def judge_chunk(model, chunk):
    """Language of one stretch of audio (16 kHz mono float array), or None when it holds too little speech
    or anything goes wrong. Returns {"lang", "prob", "en", "speech"}."""
    try:
        from faster_whisper.vad import get_speech_timestamps
        ts = get_speech_timestamps(chunk)
        speech = sum(int(t["end"]) - int(t["start"]) for t in ts) / float(SAMPLE)
        if speech < MIN_SPEECH_SEC:
            return None
        lang, prob, allp = model.detect_language(audio=chunk, vad_filter=True)
        en = 0.0
        for code, p in allp or []:
            if code == "en":
                en = float(p)
                break
        return {"lang": str(lang), "prob": round(float(prob), 2), "en": round(en, 2), "speech": round(speech, 1)}
    except Exception:
        return None


def judge_file(model, path, max_windows=MAX_WINDOWS):
    """Judges up to `max_windows` stretches of 30 s spread evenly over a file.
    Returns a list of {"t0", "t1", "lang", "prob", "en", "speech"}."""
    out = []
    try:
        audio = load_audio(path)
        n = len(audio)
        win = WINDOW_SEC * SAMPLE
        if n < SAMPLE:
            return out
        count = max(1, min(int(max_windows), (n + win - 1) // win))
        starts = [0] if count == 1 else [int(i * (n - win) / (count - 1)) if n > win else 0 for i in range(count)]
        seen = set()
        for s in starts:
            if s in seen:
                continue
            seen.add(s)
            j = judge_chunk(model, audio[s:s + win])
            if j:
                j["t0"], j["t1"] = round(s / float(SAMPLE), 1), round(min(n, s + win) / float(SAMPLE), 1)
                out.append(j)
    except Exception:
        pass
    return out


def is_flagged(j):
    return bool(j) and j.get("lang") != "en" and float(j.get("prob", 0)) >= FLAG_PROB


def _fmt(sec):
    sec = max(0, int(round(float(sec))))
    return f"{sec // 60}:{sec % 60:02d}"


def summarize(judged, ranges=None):
    """judged = list of judge results; ranges = optional list of (t0, t1) covering the same stretches in the
    whole video (the long flow judges one stretch per piece and reports the piece's time range).
    Returns (message or None, detail text for the log)."""
    try:
        judged = [j for j in (judged or []) if j]
        if not judged:
            return None, "no stretch with enough speech to judge"
        total = sum(float(j.get("speech", 0)) for j in judged)
        flagged = [j for j in judged if is_flagged(j)]
        f_speech = sum(float(j.get("speech", 0)) for j in flagged)
        detail = (f"{len(judged)} stretches judged, {len(flagged)} not English"
                  + ("".join(f" ({lang_name(j['lang'])} {int(float(j['prob']) * 100)}%)" for j in flagged[:4])))
        if not flagged or f_speech < MIN_FLAGGED_SPEECH_SEC or total <= 0 or f_speech / total < MIN_FLAGGED_SHARE:
            return None, detail
        langs = sorted({lang_name(j["lang"]) for j in flagged})
        where = ""
        spans = []
        for j in flagged:
            a, b = (j.get("t0"), j.get("t1"))
            if a is not None and b is not None:
                spans.append((float(a), float(b)))
        if spans:
            spans.sort()
            merged = [list(spans[0])]
            for a, b in spans[1:]:
                if a <= merged[-1][1] + 1.0:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            where = " around " + ", ".join(f"{_fmt(a)}-{_fmt(b)}" for a, b in merged[:4])
        share = int(round(100.0 * f_speech / total))
        msg = (f"Part of this video does not seem to be spoken in English: about {share}% of the speech{where} sounds like "
               f"{' / '.join(langs)}. The English text for those parts is only a machine guess (it may be a translation "
               f"of what was said), and dubbing {langs[0] if len(langs) == 1 else 'that speech'} into Arabic may make little "
               f"sense. Please check the text, or use only the English parts of the video.")
        return msg, detail
    except Exception as ex:
        return None, f"skipped ({ex})"


# ------------------------------------------------ model-free check of the English text

_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")


def non_latin_share(text):
    """Share (0..1) of the letters of a text that are not Latin letters (Arabic, Cyrillic, Chinese ...)."""
    letters = _LETTER.findall(text or "")
    if not letters:
        return 0.0
    non = [c for c in letters if not _LATIN.match(c)]
    return len(non) / float(len(letters))


def text_check(rows):
    """Looks at the English text of the lines. Returns (message or None, detail).
    A line counts when more than half of its letters are not Latin."""
    try:
        rows = [r for r in (rows or []) if (r.get("text") or "").strip()]
        if not rows:
            return None, "no lines"
        bad = [r for r in rows if non_latin_share(r.get("text")) > 0.5]
        detail = f"{len(bad)} of {len(rows)} lines are written in non-Latin letters"
        if len(bad) < 2 or len(bad) / float(len(rows)) < 0.10:
            return None, detail
        t0 = min(float(r.get("start", 0)) for r in bad)
        msg = (f"{len(bad)} of {len(rows)} lines of the English text are not written in English letters (for example the "
               f"line at {_fmt(t0)}). That usually means this part of the video is spoken in another language, for example "
               f"Arabic. Please check those lines before dubbing.")
        return msg, detail
    except Exception as ex:
        return None, f"skipped ({ex})"
