from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.exceptions import HTTPException
from pathlib import Path
import shutil
import threading
import uuid

from config import (
    BASE_DIR, UPLOAD_DIR, OUTPUT_DIR, VIDEO_EXTENSIONS,
    GEMINI_RATE_IN_PER_M, GEMINI_RATE_OUT_PER_M, ELEVEN_CREDITS_PER_CHAR,
)
from app_state import jobs_progress, usage_bucket
from ffmpeg_utils import run_ffmpeg, get_media_duration, mix_two_audio
from media_paths import find_job_video, resolve_job_audio, job_background_audio
import whisper_service
import gemini_service
import eleven_service
import lipsync_service
from models import (
    Segment, GenerateRequest, VoicesRequest, TranslateRequest,
    CloneRequest, AnalyzeRequest, EmotionRequest, MergeVideoRequest, LipSyncRequest,
)

app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE_DIR / "index.html").read_text(encoding="utf-8")

@app.get("/app.js")
def app_js():
    return FileResponse(BASE_DIR / "app.js", media_type="application/javascript")

@app.get("/styles.css")
def styles_css():
    return FileResponse(BASE_DIR / "styles.css", media_type="text/css")

# ---------------- TRANSCRIPTION ----------------

@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), speaker_count: str = Form(""), hf_token: str = Form("")):
    job_id = str(uuid.uuid4())
    file_ext = Path(file.filename).suffix.lower()
    is_video = file_ext in VIDEO_EXTENSIONS
    input_path = UPLOAD_DIR / f"{job_id}{file_ext}" if is_video else UPLOAD_DIR / f"{job_id}.mp3"
    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    speaker_count_value = None
    if speaker_count.strip():
        try:
            speaker_count_value = max(1, int(speaker_count.strip()))
        except Exception:
            speaker_count_value = None

    jobs_progress[job_id] = {
        "status": "starting", "percent": 0, "segments": [], "full_duration": 0.0,
        "status_text": "Starting...", "warning": None, "detected_speakers": 0,
        "is_video": is_video, "has_background": False,
    }
    t = threading.Thread(
        target=whisper_service.transcribe_worker,
        args=(job_id, str(input_path), hf_token.strip(), speaker_count_value)
    )
    t.daemon = True
    t.start()
    return {"job_id": job_id}


@app.get("/api/progress/{job_id}")
def get_progress(job_id: str):
    return jobs_progress.get(job_id, {
        "status": "not_found", "percent": 0, "segments": [], "full_duration": 0.0,
        "status_text": "", "warning": None, "detected_speakers": 0,
        "is_video": False, "has_background": False,
    })


@app.get("/api/source/{job_id}")
def source_audio(job_id: str):
    path = resolve_job_audio(job_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Source audio not found")
    suffix = path.suffix.lower()
    if suffix == ".wav":
        media = "audio/wav"
    elif suffix == ".mp3":
        media = "audio/mpeg"
    elif suffix in [".mp4", ".mov", ".webm"]:
        media = "video/mp4"
    else:
        media = "application/octet-stream"
    return FileResponse(path=path, media_type=media, filename=path.name)


# ---------------- GEMINI (translate / emotions / usage) ----------------

@app.post("/api/translate")
async def translate(req: TranslateRequest):
    return gemini_service.translate_segments(req.job_id, req.segments, req.gemini_api_key.strip())


@app.post("/api/detect_emotions")
async def detect_emotions(req: EmotionRequest):
    api_key = req.gemini_api_key.strip()
    if not api_key:
        return {"error": "Missing Gemini API key."}
    if not req.segments:
        return {"error": "No segments found."}
    audio = resolve_job_audio(req.job_id)
    if audio is None:
        return {"error": "Audio file not found. Please transcribe again."}

    jobs_progress[f"emotions_{req.job_id}"] = {
        "status": "starting", "percent": 0, "current": 0,
        "total": len(req.segments), "emotions": {}, "errors": [],
    }
    t = threading.Thread(
        target=gemini_service.detect_emotions_worker,
        args=(req.job_id, str(audio), api_key, req.segments)
    )
    t.daemon = True
    t.start()
    return {"job_id": req.job_id}


@app.get("/api/progress/emotions/{job_id}")
def get_emotions_progress(job_id: str):
    return jobs_progress.get(f"emotions_{job_id}", {
        "status": "not_found", "percent": 0, "current": 0,
        "total": 0, "emotions": {}, "errors": [],
    })


@app.get("/api/usage/{job_id}")
def get_usage(job_id: str):
    b = usage_bucket(job_id)
    out_billable = b["gemini_out"] + b["gemini_thoughts"]
    cost = (b["gemini_in"] / 1e6 * GEMINI_RATE_IN_PER_M) + (out_billable / 1e6 * GEMINI_RATE_OUT_PER_M)
    return {
        "gemini_in_tokens": b["gemini_in"],
        "gemini_out_tokens": b["gemini_out"],
        "gemini_thoughts_tokens": b["gemini_thoughts"],
        "gemini_out_billable": out_billable,
        "gemini_audio_seconds": round(b["audio_sec"], 1),
        "gemini_cost_usd": round(cost, 6),
        "eleven_credits": b["eleven_chars"],
        "rates": {
            "gemini_in_per_m": GEMINI_RATE_IN_PER_M,
            "gemini_out_per_m": GEMINI_RATE_OUT_PER_M,
            "eleven_credits_per_char": ELEVEN_CREDITS_PER_CHAR,
        },
    }


# ---------------- SPEAKERS / CLONING ----------------

@app.post("/api/analyze_speakers")
async def analyze_speakers(req: AnalyzeRequest):
    if resolve_job_audio(req.job_id) is None:
        return {"error": "Audio file not found. Please transcribe again."}
    speakers = list(set(s.speaker for s in req.segments if s.text.strip()))
    analysis = []
    for speaker in speakers:
        segs = [s for s in req.segments if s.speaker == speaker]
        total_time = sum(s.end - s.start for s in segs)
        if total_time >= 10.0:
            status, message = "good", f"{total_time:.1f}s available"
        elif total_time >= 3.0:
            status, message = "warning", f"{total_time:.1f}s available (quality may be reduced)"
        else:
            status, message = "poor", f"{total_time:.1f}s available (quality will likely be very poor)"
        analysis.append({"speaker": speaker, "total_time": round(total_time, 1), "status": status, "message": message})
    analysis.sort(key=lambda x: x["speaker"])
    return {"analysis": analysis}


@app.post("/api/voices")
async def get_voices(req: VoicesRequest):
    return eleven_service.fetch_voices(req.api_key.strip())


@app.post("/api/clone")
async def clone_voices(req: CloneRequest):
    return eleven_service.clone_voices(
        req.job_id, req.segments, req.elevenlabs_api_key.strip(), req.speakers_to_clone
    )


# ---------------- GENERATION ----------------

@app.post("/api/generate")
async def generate(req: GenerateRequest):
    current = jobs_progress.get("generate")
    if current and current.get("status") in ["starting", "processing"]:
        return {"error": "Generation is already running. Please wait."}
    jobs_progress["generate"] = {"status": "starting", "percent": 0, "result": None, "error": None}
    t = threading.Thread(target=eleven_service.generate_worker, args=(req,))
    t.daemon = True
    t.start()
    return {"job_id": "generate"}


# ---------------- VIDEO MERGE ----------------

@app.post("/api/merge_video")
async def merge_video(req: MergeVideoRequest):
    try:
        video_path = find_job_video(req.job_id)
        if video_path is None:
            return {"error": "Original video file not found."}
        dubbed = OUTPUT_DIR / "final_dubbed.mp3"
        if not dubbed.exists():
            return {"error": "Dubbed audio not found. Please generate audio first."}

        background = job_background_audio(req.job_id)
        video_duration = get_media_duration(video_path)

        if background is not None:
            mixed = OUTPUT_DIR / f"mixed_audio_{req.job_id}.wav"
            mix_two_audio(dubbed, background, mixed)
            final_audio = mixed
        else:
            mixed = None
            final_audio = dubbed

        out = OUTPUT_DIR / "final_dubbed_video.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path), "-i", str(final_audio),
            "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
            "-t", str(video_duration),
            str(out)
        ]
        run_ffmpeg(cmd)

        if mixed is not None:
            try:
                mixed.unlink()
            except Exception:
                pass

        return {
            "status": "success",
            "video_file": str(out),
            "audio_file": str(dubbed),
            "has_background": background is not None,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------- LIP SYNC ----------------

@app.post("/api/lipsync")
async def lipsync(req: LipSyncRequest):
    key = f"lipsync_{req.job_id}"
    cur = jobs_progress.get(key)
    if cur and cur.get("status") in ("starting", "processing"):
        return {"error": "Lip-sync is already running for this project."}
    jobs_progress[key] = {"status": "starting", "percent": 0, "message": "Starting...", "error": None, "result": None}
    t = threading.Thread(
        target=lipsync_service.lipsync_worker,
        args=(req.job_id, req.provider, req.model, req.elevenlabs_api_key.strip(), req.synclabs_api_key.strip())
    )
    t.daemon = True
    t.start()
    return {"job_id": req.job_id}


@app.get("/api/progress/lipsync/{job_id}")
def get_lipsync_progress(job_id: str):
    return jobs_progress.get(f"lipsync_{job_id}", {
        "status": "not_found", "percent": 0, "message": "", "error": None, "result": None,
    })


# ---------------- DOWNLOADS ----------------

@app.get("/api/download/{filename}")
def download(filename: str):
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = "video/mp4" if filename.endswith(".mp4") else "audio/mpeg"
    return FileResponse(path=file_path, media_type=media_type, filename=filename)
    
    
from models import RegenerateLineRequest
@app.post("/api/regenerate_line")
def regenerate_line_route(req: RegenerateLineRequest):
    return eleven_service.regenerate_line(req)
    
from models import RemixRequest
@app.post("/api/remix_audio")
def remix_audio_route(req: RemixRequest):
    return eleven_service.remix_with_offsets(req)