# Shared mutable state for the whole app (progress bars, usage accounting).
jobs_progress = {}
USAGE = {}
diarization_pipelines = {}


def usage_bucket(job_id: str) -> dict:
    key = job_id or "session"
    if key not in USAGE:
        USAGE[key] = {
            "gemini_in": 0,
            "gemini_out": 0,
            "gemini_thoughts": 0,
            "eleven_chars": 0,
            "audio_sec": 0.0,
        }
    return USAGE[key]


def record_gemini(job_id: str, data):
    """Add real token counts from a Gemini response's usageMetadata."""
    if not isinstance(data, dict):
        return
    u = data.get("usageMetadata") or {}
    b = usage_bucket(job_id)
    b["gemini_in"] += int(u.get("promptTokenCount", 0) or 0)
    b["gemini_out"] += int(u.get("candidatesTokenCount", 0) or 0)
    b["gemini_thoughts"] += int(u.get("thoughtsTokenCount", 0) or 0)