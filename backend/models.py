from pydantic import BaseModel, field_validator
from typing import List, Dict

class Segment(BaseModel):
    segment_id: str

    # Security review (2026-09-30): segment ids become part of file names
    # ("<segment_id>_stretched.mp3"), so slashes and ".." are refused.
    @field_validator("segment_id")
    @classmethod
    def _segment_id_ok(cls, v):
        v = str(v or "")
        if not v or len(v) > 200 or "/" in v or "\\" in v or "\x00" in v or ".." in v:
            raise ValueError("bad segment id")
        return v

    start: float
    end: float
    speaker: str = "Speaker 1"
    gender: str = "male"
    emotion: str = "neutral"
    text: str = ""
    arabic_text: str = ""
    # Per-line time-stretch limit (excellent/good/maximum) — set from the
    # Step 5.5 table (one per segment). Replaces the old single Step 5
    # dropdown that applied the same limit to every line in the job.
    tempo_mode: str = "excellent"

class GenerateRequest(BaseModel):
    job_id: str = ""
    segments: List[Segment]
    elevenlabs_api_key: str = ""
    gemini_api_key: str = ""
    tts_provider: str = "elevenlabs"
    gemini_voice: str = "Kore"
    default_voice_id: str = ""
    speaker_voices: Dict[str, str] = {}
    tempo_mode: str = "excellent"
    duration_mode: str = "exact"
    total_duration: float = 0.0
    cloned_voice_ids: List[str] = []

class VoicesRequest(BaseModel):
    api_key: str

class TranslateRequest(BaseModel):
    job_id: str = ""
    segments: List[Segment]
    gemini_api_key: str

class CloneRequest(BaseModel):
    job_id: str
    elevenlabs_api_key: str
    segments: List[Segment]
    speakers_to_clone: List[str] = []

class AnalyzeRequest(BaseModel):
    job_id: str
    segments: List[Segment]

class EmotionRequest(BaseModel):
    job_id: str
    segments: List[Segment]
    gemini_api_key: str

class MergeVideoRequest(BaseModel):
    job_id: str

class LipSyncRequest(BaseModel):
    job_id: str
    provider: str = "elevenlabs"
    model: str = ""
    elevenlabs_api_key: str = ""
    synclabs_api_key: str = ""
    
    
class RegenerateLineRequest(BaseModel):
    job_id: str = ""
    segment: Segment
    segments: List[Segment] = []
    elevenlabs_api_key: str
    voice_id: str = ""
    tempo_mode: str = "excellent"
    duration_mode: str = "exact"
    total_duration: float = 0.0
    
class RemixRequest(BaseModel):
    job_id: str = ""
    segments: List[Segment] = []
    offsets: Dict[str, float] = {}
    total_duration: float = 0.0
    duration_mode: str = "exact"