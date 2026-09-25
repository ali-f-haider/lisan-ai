from pathlib import Path
from config import UPLOAD_DIR, OUTPUT_DIR

VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".avi")
AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")
REFERENCE_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


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


def job_reference_images(job_id):
    """Optional reference photos uploaded for Step 7 lip-sync (see
    /api/lipsync/reference-images in main.py) -- fed to the Wan 3.0 call as
    reference_image entries to help it keep the speaker's exact face and
    appearance, alongside the reference_video/reference_audio pair. Empty
    list if the user didn't upload any (they're optional)."""
    return sorted(p for p in OUTPUT_DIR.glob(f"lipsync_ref_{job_id}_*")
                  if p.suffix.lower() in REFERENCE_IMAGE_EXTS)