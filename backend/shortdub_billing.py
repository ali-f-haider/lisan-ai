"""Pure calculations for a short dub's confirmed price (no network calls)."""
import math

AI_KEYS = ("gemini_in", "gemini_out", "gemini_thoughts", "audio_sec")


def ai_snapshot(bucket):
    return {key: max(0, float(bucket.get(key, 0) or 0)) for key in AI_KEYS}


def studio_quote(characters, bucket, config):
    voice = sum(math.ceil(count / max(1, int(config.get(rate, 60) or 60)))
                for engine, rate in (("elevenlabs", "charsPerCredit"), ("inworld", "inworldCharsPerCredit"))
                if (count := characters.get(engine, 0)) > 0)
    snapshot = ai_snapshot(bucket)
    settled = bucket.get("shortdub_ai_settled") or {}
    pending = {key: max(0, snapshot[key] - float(settled.get(key, 0) or 0)) for key in AI_KEYS}
    # Gemini bills its thinking tokens at the output rate, so they count with the answer tokens.
    usd = ((pending["gemini_in"] + int(pending["audio_sec"] * 258)) / 1e6 * .30
           + (pending["gemini_out"] + pending["gemini_thoughts"]) / 1e6 * 2.50)
    try:
        multiplier = float(config.get("geminiCreditsPerCent", 1))
    except (TypeError, ValueError):
        multiplier = 1.0
    multiplier = min(100, max(.1, multiplier)) if math.isfinite(multiplier) else 1.0
    ai = math.ceil(usd * multiplier / .01) if usd > 0 else 0
    return {"credits": voice + ai, "voice_credits": voice, "analysis_credits": ai,
            "ai_snapshot": snapshot}


def debit_confirmed(result):
    # True means the subscription bucket paid in full; an integer is the
    # permanent-credit RPC's remaining balance. Zero is a valid balance.
    return result is True or (type(result) is int and result >= 0)
