"""Checking the number of speakers the user gave against the voices the detector finds.

The detector is run first with the stated number as a minimum. When it finds MORE real voices than the user said, the job pauses and
asks: "You said N, we found M. Change the number to M?" Whatever the user answers is then a fact for the rest of the job: voice
detection is asked for exactly that many voices and the checks are told exactly that many people speak (see longdub_service).
A voice only counts when it has real speech: at least MIN_VOICE_SEC seconds and MIN_VOICE_SHARE of all the speech, so a cough or a
one-word background voice never starts a question. Fewer voices than stated is never asked (a quiet speaker is easily missed).
Pure functions on the detector's turns ({"start", "end", "speaker"}); no models, no requests."""

MIN_VOICE_SEC = 5.0
MIN_VOICE_SHARE = 0.04


def voice_seconds(turns):
    """{voice: seconds of speech}."""
    out = {}
    for t in turns or []:
        try:
            sec = max(0.0, float(t["end"]) - float(t["start"]))
        except (KeyError, TypeError, ValueError):
            continue
        sp = t.get("speaker") if isinstance(t, dict) else None
        if sp is None:
            continue
        out[sp] = out.get(sp, 0.0) + sec
    return out


def clusters(turns):
    """How many distinct voices the detector labelled (tiny ones included)."""
    return len(voice_seconds(turns))


def real_voices(turns):
    """Seconds of speech of each voice that counts, biggest first."""
    secs = voice_seconds(turns)
    total = sum(secs.values())
    if total <= 0:
        return []
    keep = [s for s in secs.values() if s >= MIN_VOICE_SEC and s / total >= MIN_VOICE_SHARE]
    if not keep:
        keep = [max(secs.values())]
    return sorted(keep, reverse=True)


def ask_for(turns, stated):
    """None when nothing needs asking, else {"stated": N, "found": M, "seconds": [...]} with M > N."""
    try:
        n = int(stated)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    found = real_voices(turns)
    if len(found) <= n:
        return None
    return {"stated": n, "found": len(found), "seconds": [round(s) for s in found]}


def valid_choice(value, ask):
    """The answer (the number the user keeps or takes) as an int, or None when it is neither of the two offered."""
    try:
        if isinstance(value, bool):
            return None
        k = int(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(ask, dict):
        return None
    return k if k in (ask.get("stated"), ask.get("found")) else None
