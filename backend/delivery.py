"""Conservative, local evidence about delivery; no requests and no credits.

Short lines and lines dominated by pauses cannot establish a speaking pace.
Speed tags must agree with timing evidence and with the other delivery tags.
"""
from itertools import islice
import math
import re

MIN_WORDS = 5
MIN_SECONDS = 1.5
GAP_SEC = 0.25
PAUSE_FRACTION = 0.25          # conservative ambiguity guard, not an accuracy score
MOMENT = 4
MIN_NEIGHBOURS = 3             # includes the current line
SLOW_BELOW = 4.3
FAST_ABOVE = 6.0
# the fastest a dubbed line may be played before its wording is shortened (the hard limit is longdub_service.TEMPO_MAX)
CAP = {"slow": 1.08, "normal": 1.15, "fast": 1.25, "unknown": 1.15}

_VOWELS = re.compile(r"[aeiouy]+")
FAST_TAGS = ("rushed", "very fast")
SLOW_TAGS = ("slowly", "drawn out")
# Automatic "slowly" / "drawn out" are switched OFF (Ali's finding, 2026-10-08): the word times of the transcription spread real pauses
# over the neighbouring words, so a speaker who pauses to think is measured as a slow speaker, and the dub then stretches a whole
# sentence to imitate it. A speed instruction is added only for a FAST speaker until the pace is measured from the audio itself
# (pauses taken out, judged against the speaker's own usual pace). A user can still pick any delivery by hand. Set True to switch back.
AUTO_SLOW_ALLOWED = False
URGENT_TAGS = frozenset(("anxious", "fearful", "terrified", "angry", "shouting", "yelling", "screaming",
                         "commanding", "pleading", "excited", "frustrated", "appalled", "surprised", "rushed", "very fast"))


def syllables(text):
    n = 0
    for w in re.findall(r"[A-Za-z']+", text or ""):
        w = w.lower().strip("'")
        if not w:
            continue
        k = len(_VOWELS.findall(w))
        if w.endswith("e") and not w.endswith(("le", "ee")) and k > 1:
            k -= 1
        n += max(1, k)
    return n


def _line_stats(row):
    try:
        words = []
        for w in row.get("words") or []:
            if not isinstance(w, dict) or not str(w.get("word") or "").strip():
                continue
            a, b = float(w["start"]), float(w["end"])
            if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a:
                return None
            words.append((a, b, str(w["word"])))
        if len(words) < MIN_WORDS:
            return None
        words.sort()
        end = words[0][1]
        gaps = 0.0
        for a, b, _ in words[1:]:
            gap = a - end
            if gap >= GAP_SEC:
                gaps += gap
            end = max(end, b)
        total = end - words[0][0]
        spoken = total - gaps
        syl = syllables(" ".join(w[2] for w in words))
        if spoken < MIN_SECONDS or syl < MIN_WORDS:
            return None
        return syl / spoken, gaps / total
    except Exception:
        return None


def line_rate(row):
    """Diagnostic syllables per voiced second; large pauses are excluded."""
    stats = _line_stats(row)
    return stats[0] if stats else None


def _median(v):
    v = sorted(v)
    n = len(v)
    return None if not n else (v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2]))


def _usable_rate(row):
    stats = _line_stats(row)
    return stats[0] if stats and stats[1] < PAUSE_FRACTION else None


def moment_rate(rows, i):
    """Median of nearby lines by this speaker; mixed fallback needs three usable lines."""
    try:
        speaker = rows[i].get("speaker")
        if speaker:
            before = list(islice((j for j in range(i - 1, -1, -1) if rows[j].get("speaker") == speaker), MOMENT))
            after = list(islice((j for j in range(i + 1, len(rows)) if rows[j].get("speaker") == speaker), MOMENT))
            rates = [_usable_rate(rows[j]) for j in before + [i] + after]
            rates = [x for x in rates if x is not None]
            if len(rates) >= MIN_NEIGHBOURS:
                return _median(rates)
        lo, hi = max(0, i - MOMENT), min(len(rows), i + MOMENT + 1)
        rates = [_usable_rate(r) for r in rows[lo:hi]]
        rates = [x for x in rates if x is not None]
        return _median(rates) if len(rates) >= MIN_NEIGHBOURS else None
    except Exception:
        return None


def pace(rows, i):
    try:
        own = _usable_rate(rows[i])
        around = moment_rate(rows, i)
        if own is None or around is None:
            return "unknown"
        if own < SLOW_BELOW and around < SLOW_BELOW:
            return "slow"
        if own > FAST_ABOVE and around > FAST_ABOVE:
            return "fast"
        return "normal"
    except Exception:
        return "unknown"


def tempo_cap(p):
    return CAP.get(p, CAP["unknown"])


def ground(emotion, p):
    """Remove unsupported or contradictory speed tags; never infer a new emotion."""
    try:
        if emotion is None or emotion == "":
            return emotion
        if not isinstance(emotion, str):
            return "neutral"
        parts = [x.strip() for x in emotion.split(",") if x.strip()]
        urgent = any(x.lower() in URGENT_TAGS for x in parts)
        keep = [x for x in parts if not (x.lower() in FAST_TAGS and p != "fast")
                and not (x.lower() in SLOW_TAGS and not (AUTO_SLOW_ALLOWED and p == "slow" and not urgent))]
        return ", ".join(keep) if keep else "neutral"
    except Exception:
        return "neutral"
