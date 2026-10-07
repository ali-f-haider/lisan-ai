"""How a speaker speaks, measured from the recording (nothing is asked from an AI, nothing is charged).

The words of every line carry their start and end times (the transcription). From them: how many syllables per second the speaker
really says in a line, and in the moment around it (the lines before and after). From that:

* `pace(rows, i)`     "slow" | "normal" | "fast" for the line i in its moment.
* `tempo_cap(pace)`   how much a dubbed line may be played faster than natural before its wording is shortened instead: a slow,
                      monotonous speaker must not be fast-forwarded, a fast one may.
* `ground(emotion, pace)`  a delivery tag about speed ("rushed", "very fast", "slowly", "drawn out") must agree with the measured pace; the
                      AI that reads the TEXT (a frightened sentence sounds "rushed" on paper) is wrong when the speaker is slow.
Nothing here raises: when a line cannot be measured the answer is "unknown" and nothing is changed."""
import re

MIN_WORDS = 3                  # a line with fewer words says too little about the pace
MIN_SECONDS = 0.8
GAP_SEC = 0.4                  # a silence inside a line longer than this is not speaking
MOMENT = 4                     # lines before and after that make "this moment"
SLOW_BELOW = 4.3               # syllables per second (the median of conversation is about 5)
FAST_ABOVE = 6.0
# the fastest a dubbed line may be played before its wording is shortened (the hard limit is longdub_service.TEMPO_MAX)
CAP = {"slow": 1.08, "normal": 1.15, "fast": 1.25, "unknown": 1.15}

_VOWELS = re.compile(r"[aeiouy]+")
FAST_TAGS = ("rushed", "very fast")
SLOW_TAGS = ("slowly", "drawn out")


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


def line_rate(row):
    """Syllables per second spoken in the line, or None when it cannot be measured."""
    try:
        words = [w for w in (row.get("words") or []) if isinstance(w, dict) and w.get("start") is not None and w.get("end") is not None]
        text = row.get("text") or ""
        if len(words) >= MIN_WORDS:
            span = float(words[-1]["end"]) - float(words[0]["start"])
            gaps = 0.0
            for a, b in zip(words, words[1:]):
                g = float(b["start"]) - float(a["end"])
                if g > GAP_SEC:
                    gaps += g
            span -= gaps
            syl = syllables(" ".join(str(w.get("word") or "") for w in words))
        else:
            if len(text.split()) < MIN_WORDS:
                return None
            span = float(row["end"]) - float(row["start"])
            syl = syllables(text)
        if span < MIN_SECONDS or syl < 3:
            return None
        return syl / span
    except Exception:
        return None


def _median(v):
    v = sorted(v)
    n = len(v)
    return None if not n else (v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2]))


def moment_rate(rows, i):
    """Median speaking rate of line i and its neighbours (the moment the speaker is in), or None."""
    lo, hi = max(0, i - MOMENT), min(len(rows), i + MOMENT + 1)
    rates = [line_rate(r) for r in rows[lo:hi]]
    return _median([x for x in rates if x])


def pace(rows, i):
    try:
        own = line_rate(rows[i])
        around = moment_rate(rows, i)
        rate = around if around is not None else own
        if rate is None:
            return "unknown"
        if own is not None and around is not None:
            rate = 0.5 * (own + around)          # the line itself counts as much as its moment
        if rate < SLOW_BELOW:
            return "slow"
        if rate > FAST_ABOVE:
            return "fast"
        return "normal"
    except Exception:
        return "unknown"


def tempo_cap(p):
    return CAP.get(p, CAP["unknown"])


def ground(emotion, p):
    """The delivery string with the speed tags that disagree with the measured pace removed ("neutral" when nothing is left)."""
    try:
        parts = [x.strip() for x in str(emotion or "").split(",") if x.strip()]
        if not parts or p == "unknown":
            return emotion
        drop = set()
        if p != "fast":
            drop.update(FAST_TAGS)
        if p == "fast":
            drop.update(SLOW_TAGS)
        keep = [x for x in parts if x.lower() not in drop]
        if len(keep) == len(parts):
            return emotion
        return ", ".join(keep) if keep else "neutral"
    except Exception:
        return emotion
