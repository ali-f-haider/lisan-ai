"""Compatibility wrapper for the shared Gemini audio-listening classifier."""
import gemini_service


def inspect(job_id, audio, selected, api_key):
    return gemini_service.inspect_audio_style(job_id, audio, selected, api_key)
