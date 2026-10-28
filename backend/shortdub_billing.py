"""Pure calculations for a short dub's confirmed price (no network calls)."""
import math

AI_KEYS = ("gemini_in", "gemini_out", "gemini_thoughts", "audio_sec")


def ai_snapshot(bucket):
    return {key: max(0, float(bucket.get(key, 0) or 0)) for key in AI_KEYS}


def flash_rates():
    """(input, output, audio input) $ per 1M tokens: the cost meter's Gemini 3.6 Flash price for today (resource_meter.flash_rates:
    $0.75 / $3.75 until 31 Dec 2026, $1.50 / $7.50 from 1 Jan 2027). Before 1.83.9 the old 2.5 Flash prices ($0.30 / $2.50) were used."""
    try:
        import resource_meter
        rin, rout, raud = resource_meter.flash_rates()
        return float(rin), float(rout), float(raud)
    except Exception:
        return 0.75, 3.75, 0.75


def gemini_usd(gemini_in=0, gemini_out=0, gemini_thoughts=0, audio_sec=0):
    """Dollars for the Gemini part of a short dub (a second of audio is 258 tokens; thinking is billed like the answer)."""
    rin, rout, raud = flash_rates()
    return (float(gemini_in) / 1e6 * rin + int(float(audio_sec) * 258) / 1e6 * raud
            + (float(gemini_out) + float(gemini_thoughts)) / 1e6 * rout)


def studio_quote(characters, bucket, config):
    voice = sum(math.ceil(count / max(1, int(config.get(rate, 60) or 60)))
                for engine, rate in (("elevenlabs", "charsPerCredit"), ("inworld", "inworldCharsPerCredit"))
                if (count := characters.get(engine, 0)) > 0)
    snapshot = ai_snapshot(bucket)
    settled = bucket.get("shortdub_ai_settled") or {}
    pending = {key: max(0, snapshot[key] - float(settled.get(key, 0) or 0)) for key in AI_KEYS}
    # Gemini bills its thinking tokens at the output rate, so they count with the answer tokens.
    usd = gemini_usd(pending["gemini_in"], pending["gemini_out"], pending["gemini_thoughts"], pending["audio_sec"])
    try:
        multiplier = float(config.get("geminiCreditsPerCent", 1))
    except (TypeError, ValueError):
        multiplier = 1.0
    multiplier = min(100, max(.1, multiplier)) if math.isfinite(multiplier) else 1.0
    ai = math.ceil(round(usd * multiplier / .01, 6)) if usd > 0 else 0      # round first: 156.00000000000003 is 156 credits, not 157
    return {"credits": voice + ai, "voice_credits": voice, "analysis_credits": ai,
            "ai_snapshot": snapshot}


def debit_confirmed(result):
    # True means the subscription bucket paid in full; an integer is the
    # permanent-credit RPC's remaining balance. Zero is a valid balance.
    return result is True or (type(result) is int and result >= 0)
