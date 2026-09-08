from pathlib import Path
from config import UPLOAD_DIR

VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".avi")
AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")


def find_job_video(job_id):
    c = sorted(p for p in UPLOAD_DIR.glob(f"{job_id}*")
               if p.suffix.lower() in VIDEO_EXTS and "_separated" not in p.name)
    return c[0] if c else None


def resolve_job_audio(job_id):
    c = sorted(p for p in UPLOAD_DIR.glob(f"{job_id}*")
               if p.suffix.lower() in AUDIO_EXTS and "_separated" not in p.name)
    if c:
        return c[0]
    return find_job_video(job_id)


def job_background_audio(job_id):
    cands = sorted(UPLOAD_DIR.glob(f"{job_id}_separated/**/no_vocals.wav"))
    if not cands:
        cands = sorted(UPLOAD_DIR.glob(f"**/{job_id}*/no_vocals.wav"))
    if not cands:
        cands = sorted(UPLOAD_DIR.glob(f"{job_id}*/**/no_vocals.wav"))
    return cands[0] if cands else None