"""POST /api/voices/match: the server side of Auto-Assign (see voice_match_service).

main.py adds it with two lines:

    import voice_match_routes
    voice_match_routes.register(app, job_guard=_job_guard, rate_limited=_rate_limited, rate_message=_RATE_LIMIT_MSG,
                                resolve_audio=resolve_job_audio, fetch_voices=lambda: eleven_service.fetch_voices(ELEVENLABS_API_KEY),
                                gemini_key=lambda: GEMINI_API_KEY)
"""
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import voice_match_service as svc

MAX_SPEAKERS = 12
MAX_SEGMENTS = 5000
RATE_MAX, RATE_WINDOW = 12, 600


class _Seg(BaseModel):
    speaker: str = ""
    start: float = 0.0
    end: float = 0.0


class _Speaker(BaseModel):
    name: str
    gender: Optional[str] = ""       # only what the person chose; never a default
    age: Optional[str] = ""


class MatchRequest(BaseModel):
    job_id: str
    speakers: List[_Speaker]
    segments: List[_Seg]
    taken: List[str] = []
    listen: bool = True


def _cut(src, start, dur, out):
    """16 kHz mono piece of an audio file (wav or mp3, by the output name)."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, float(start)):.3f}", "-t", f"{float(dur):.3f}",
                    "-i", str(src), "-vn", "-ar", "16000", "-ac", "1", str(out)], check=True, timeout=60)


def _convert(src, dst):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
                    str(dst)], check=True, timeout=30)


def register(app, *, job_guard, rate_limited, rate_message, resolve_audio, fetch_voices, gemini_key, listener=None):
    """Adds the route. `listener(job_id, mp3_path, key)` defaults to gemini_service.describe_speaker_voice."""

    @app.post("/api/voices/match")
    def voices_match(req: MatchRequest, request: Request):
        guard = job_guard(request, req.job_id, allow_empty=False)
        if guard:
            return guard
        if rate_limited(request, "voice_match", RATE_MAX, RATE_WINDOW):
            return JSONResponse({"error": rate_message}, status_code=429)
        if not req.speakers or len(req.speakers) > MAX_SPEAKERS or len(req.segments) > MAX_SEGMENTS:
            return JSONResponse({"error": "Too many speakers or lines to match."}, status_code=400)
        src = resolve_audio(req.job_id)
        if src is None:
            return JSONResponse({"error": "Audio file not found. Please transcribe again."}, status_code=404)
        voices = svc.library_voices(fetch_voices)
        if not voices:
            return JSONResponse({"error": "The voice library could not be loaded. Please try again."}, status_code=503)
        key = gemini_key() if callable(gemini_key) else gemini_key
        describe = None
        if req.listen and key and os.environ.get("VOICE_MATCH_LISTEN", "on").strip().lower() not in ("off", "0", "false", "no"):
            fn = listener
            if fn is None:
                import gemini_service
                fn = gemini_service.describe_speaker_voice
            describe = lambda job_id, mp3: fn(job_id, mp3, key)
        with tempfile.TemporaryDirectory(prefix="vmatch_") as work:
            try:
                result = svc.match(req.job_id, src, [s.model_dump() for s in req.speakers], [s.model_dump() for s in req.segments], voices,
                                   cut=_cut, describe=describe, fetch_wav=svc.make_wav_fetcher(work, _convert),
                                   work=Path(work) / "m", taken=set(req.taken))
            except Exception:
                return JSONResponse({"error": "Voices could not be matched right now. Please pick them yourself or try again."}, status_code=500)
        return result

    return voices_match
