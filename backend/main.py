import json
import os
import secrets
import shutil
import threading
import urllib.request
import uuid
from pathlib import Path
from typing import Dict, List

from fastapi import FastAPI, UploadFile, File, Form, Request, Response
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from config import (BASE_DIR, UPLOAD_DIR, OUTPUT_DIR,
                    GEMINI_API_KEY, ELEVENLABS_API_KEY, HF_TOKEN, APP_PASSWORD)
from app_state import jobs_progress, usage_bucket
from models import Segment
import whisper_service
import gemini_service
import eleven_service
import ffmpeg_utils
import lipsync_service
from media_paths import resolve_job_audio, find_job_video, job_background_audio

app = FastAPI()

VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".avi")
GEMINI_TEXT_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]

# ==================== ALL MODELS ====================

class LoginRequest(BaseModel):
    password: str = ""

class AuthRequest(BaseModel):
    access_token: str = ""
    refresh_token: str = ""

class AnalyzeRequest(BaseModel):
    job_id: str
    segments: List[Segment]

class CloneRequest(BaseModel):
    job_id: str
    segments: List[Segment]
    speakers_to_clone: List[str] = []

class TranslateRequest(BaseModel):
    job_id: str = ""
    segments: List[Segment]

class EmotionRequest(BaseModel):
    job_id: str
    segments: List[Segment]

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

class RegenerateLineRequest(BaseModel):
    job_id: str = ""
    segment: Segment
    segments: List[Segment] = []
    elevenlabs_api_key: str = ""
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

class MergeRequest(BaseModel):
    job_id: str

class LipSyncRequest(BaseModel):
    job_id: str
    provider: str = "synclabs"
    model: str = "lipsync-2"
    sync_key: str = ""

class TashkeelItem(BaseModel):
    segment_id: str
    arabic_text: str

class TashkeelRequest(BaseModel):
    items: List[TashkeelItem]

# ==================== AUTH ====================

_sessions = set()
_valid_tokens = {}
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


def _verify_supabase_token(access_token: str) -> bool:
    if not SUPABASE_URL or not access_token:
        return False
    if access_token in _valid_tokens:
        return True
    try:
        url = f"{SUPABASE_URL}/auth/v1/user"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {access_token}",
            "apikey": SUPABASE_ANON_KEY
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 200:
                _valid_tokens[access_token] = True
                return True
    except Exception:
        pass
    return False


def _is_logged_in(request: Request) -> bool:
    cookie = request.cookies.get("session", "")
    if not cookie or cookie not in _sessions:
        return False
    sb_token = _valid_tokens.get(cookie, "")
    if sb_token and _verify_supabase_token(sb_token):
        return True
    if APP_PASSWORD:
        return True
    return False


PUBLIC_PATHS = frozenset([
    "/", "/login", "/auth/callback", "/help", "/debug-keys",
    "/api/login", "/api/auth/session", "/api/auth/check"
])


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/api/auth/"):
            return await call_next(request)
        if path.endswith(".css") or path.endswith(".js") or path.endswith(".svg") or path.endswith(".woff2"):
            return await call_next(request)
        if not _is_logged_in(request):
            if path.startswith("/api/"):
                return JSONResponse({"error": "Not logged in"}, status_code=401)
            return HTMLResponse('<script>window.location.href="/login";</script>', status_code=200)
        return await call_next(request)

app.add_middleware(AuthMiddleware)

# ==================== DEBUG ====================

@app.get("/debug-keys")
def debug_keys():
    return {
        "SUPABASE_URL": SUPABASE_URL[:25] + "..." if SUPABASE_URL else "EMPTY",
        "SUPABASE_ANON_KEY": SUPABASE_ANON_KEY[:25] + "..." if SUPABASE_ANON_KEY else "EMPTY",
        "APP_PASSWORD": "SET" if APP_PASSWORD else "EMPTY",
        "GEMINI_API_KEY": "SET" if GEMINI_API_KEY else "EMPTY",
        "ELEVENLABS_API_KEY": "SET" if ELEVENLABS_API_KEY else "EMPTY",
        "HF_TOKEN": "SET" if HF_TOKEN else "EMPTY",
    }

# ==================== AUTH ROUTES ====================

@app.post("/api/auth/session")
def auth_session(req: AuthRequest, response: Response):
    if _verify_supabase_token(req.access_token):
        tok = secrets.token_hex(32)
        _sessions.add(tok)
        _valid_tokens[tok] = req.access_token
        response.set_cookie("session", tok, httponly=True, max_age=86400 * 7, samesite="lax")
        return {"ok": True}
    return JSONResponse({"error": "Invalid session"}, status_code=401)


@app.get("/api/auth/check")
def auth_check(request: Request):
    if _is_logged_in(request):
        return {"ok": True}
    return JSONResponse({"error": "Not logged in"}, status_code=401)


@app.post("/api/login")
def login_legacy(req: LoginRequest, response: Response):
    if APP_PASSWORD and req.password == APP_PASSWORD:
        tok = secrets.token_hex(32)
        _sessions.add(tok)
        response.set_cookie("session", tok, httponly=True, max_age=86400 * 7, samesite="lax")
        return {"ok": True}
    return {"ok": False}


@app.get("/login")
def login_page():
    html = (BASE_DIR / "login.html").read_text(encoding="utf-8")
    inject = f'<script>window.__SUPABASE_URL="{SUPABASE_URL}";window.__SUPABASE_KEY="{SUPABASE_ANON_KEY}";</script>'
    html = html.replace("</head>", inject + "</head>")
    return HTMLResponse(html)


@app.get("/auth/callback")
def auth_callback():
    html = (BASE_DIR / "auth_callback.html").read_text(encoding="utf-8")
    inject = f'<script>window.__SUPABASE_URL="{SUPABASE_URL}";window.__SUPABASE_KEY="{SUPABASE_ANON_KEY}";</script>'
    html = html.replace("</head>", inject + "</head>")
    return HTMLResponse(html)

# ==================== GEMINI HELPER ====================

def _gemini_text(prompt: str):
    last = ""
    for m in GEMINI_TEXT_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={GEMINI_API_KEY}"
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
        }).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            txt = data["candidates"][0]["content"]["parts"][0]["text"]
            if txt:
                return txt
            last = "empty response"
        except Exception as e:
            last = str(e)
    raise Exception(last or "Gemini call failed")

# ==================== STATIC FILES ====================

@app.get("/")
def landing():
    return FileResponse(BASE_DIR / "landing.html")

@app.get("/app")
def home():
    return FileResponse(BASE_DIR / "index.html")

@app.get("/app.js")
def app_js():
    return FileResponse(BASE_DIR / "app.js", media_type="application/javascript")

@app.get("/styles.css")
def styles():
    return FileResponse(BASE_DIR / "styles.css", media_type="text/css")

@app.get("/help")
def help_page():
    return FileResponse(BASE_DIR / "help.html", media_type="text/html")

# ==================== API ROUTES ====================

@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), speaker_count: int = Form(2), hf_token: str = Form("")):
    job_id = str(uuid.uuid4())
    ext = Path(file.filename or "audio.mp4").suffix.lower() or ".mp4"
    dest = UPLOAD_DIR / f"{job_id}{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    jobs_progress[job_id] = {"status": "processing", "percent": 0,
                             "status_text": "Upload done, starting transcription...",
                             "is_video": ext in VIDEO_EXTS}
    threading.Thread(target=whisper_service.transcribe_worker,
                     args=(job_id, str(dest), HF_TOKEN, speaker_count), daemon=True).start()
    return {"job_id": job_id}

@app.get("/api/progress/{job_id}")
def progress(job_id: str):
    return jobs_progress.get(job_id, {"status": "not_found"})

@app.get("/api/source/{job_id}")
def source(job_id: str):
    p = resolve_job_audio(job_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p)

@app.post("/api/voices")
def voices(payload: dict = {}):
    return eleven_service.fetch_voices(ELEVENLABS_API_KEY)

@app.post("/api/analyze_speakers")
def analyze_speakers(req: AnalyzeRequest):
    if resolve_job_audio(req.job_id) is None:
        return {"error": "Audio file not found. Please transcribe again."}
    analysis = []
    for speaker in sorted(set(s.speaker for s in req.segments if (s.text or "").strip())):
        segs = [s for s in req.segments if s.speaker == speaker]
        total = round(sum(max(0, s.end - s.start) for s in segs), 1)
        analysis.append({"speaker": speaker, "total_time": total, "num_segments": len(segs),
                         "status": "good" if total >= 10 else ("warning" if total >= 1 else "bad"),
                         "message": f"{total}s available"})
    return {"analysis": analysis}

@app.post("/api/clone")
def clone(req: CloneRequest):
    return eleven_service.clone_voices(req.job_id, req.segments, ELEVENLABS_API_KEY, req.speakers_to_clone)

@app.post("/api/translate")
def translate(req: TranslateRequest):
    return gemini_service.translate_segments(req.job_id, req.segments, GEMINI_API_KEY)

@app.post("/api/detect_emotions")
def detect_emotions(req: EmotionRequest):
    audio = resolve_job_audio(req.job_id)
    if audio is None:
        return {"error": "Audio not found."}
    jobs_progress[f"emotions_{req.job_id}"] = {"status": "processing", "percent": 0,
                                               "current": 0, "total": len(req.segments)}
    threading.Thread(target=gemini_service.detect_emotions_worker,
                     args=(req.job_id, str(audio), GEMINI_API_KEY, req.segments), daemon=True).start()
    return {"status": "started"}

@app.get("/api/progress/emotions/{job_id}")
def emotions_progress(job_id: str):
    return jobs_progress.get(f"emotions_{job_id}", {"status": "not_found"})

@app.post("/api/tashkeel")
def tashkeel(req: TashkeelRequest):
    if not req.items:
        return {"error": "Nothing to process."}
    prompt = ("You are an Arabic diacritization (tashkeel) engine.\n"
              "Add full, correct Arabic tashkeel (harakat) to each text below.\n"
              "STRICT RULES:\n- Do NOT translate.\n- Do NOT change, add, remove, or reorder any words.\n"
              "- Keep punctuation exactly as is.\n"
              '- Return ONLY valid JSON array: [{"segment_id": "...", "arabic_text": "..."}]\n'
              "Texts:\n" + json.dumps([i.dict() for i in req.items], ensure_ascii=False, indent=1))
    txt = _gemini_text(prompt).strip()
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[1] if "\n" in txt else txt[3:]
    if txt.endswith("```"):
        txt = txt[:-3]
    return {"items": json.loads(txt.strip())}

@app.post("/api/generate")
def generate(req: GenerateRequest):
    req.elevenlabs_api_key = ELEVENLABS_API_KEY
    req.gemini_api_key = GEMINI_API_KEY
    jobs_progress["generate"] = {"status": "processing", "percent": 0, "result": None, "error": None}
    threading.Thread(target=eleven_service.generate_worker, args=(req,), daemon=True).start()
    return {"status": "started"}

@app.get("/api/progress/generate")
def generate_progress():
    return jobs_progress.get("generate", {"status": "not_found"})

@app.post("/api/regenerate_line")
def regenerate_line(req: RegenerateLineRequest):
    req.elevenlabs_api_key = ELEVENLABS_API_KEY
    return eleven_service.regenerate_line(req)

@app.post("/api/remix_audio")
def remix_audio(req: RemixRequest):
    return eleven_service.remix_with_offsets(req)

@app.post("/api/merge_video")
def merge_video(req: MergeRequest):
    video = find_job_video(req.job_id)
    dub = OUTPUT_DIR / "final_dubbed.mp3"
    if video is None or not dub.exists():
        return {"error": "Missing video or dubbed audio. Run Generate first."}
    bg = job_background_audio(req.job_id)
    final = OUTPUT_DIR / "final_dubbed_video.mp4"
    if bg is not None:
        mixed = OUTPUT_DIR / f"merge_mixed_{req.job_id}.wav"
        ffmpeg_utils.mix_two_audio(dub, bg, mixed)
        ffmpeg_utils.mux_audio_into_video(video, mixed, final)
        try: mixed.unlink()
        except Exception: pass
    else:
        ffmpeg_utils.mux_audio_into_video(video, dub, final)
    return {"status": "success", "has_background": bg is not None}

@app.post("/api/lipsync")
def lipsync(req: LipSyncRequest):
    jobs_progress[f"lipsync_{req.job_id}"] = {"status": "processing", "percent": 5,
                                              "message": "Preparing...", "error": None,
                                              "result": None, "generation_id": None}
    threading.Thread(target=lipsync_service.lipsync_worker,
                     args=(req.job_id, req.provider, req.model, ELEVENLABS_API_KEY, req.sync_key),
                     daemon=True).start()
    return {"status": "started"}

@app.get("/api/progress/lipsync/{job_id}")
def lipsync_progress(job_id: str):
    return jobs_progress.get(f"lipsync_{job_id}", {"status": "not_found"})

@app.get("/api/usage/{job_id}")
def usage(job_id: str):
    b = usage_bucket(job_id)
    in_tok = int(b.get("gemini_in", 0)) + int(b.get("audio_sec", 0) * 258)
    out_tok = int(b.get("gemini_out", 0))
    cost = in_tok / 1e6 * 0.30 + out_tok / 1e6 * 2.50
    return {"eleven_credits": int(b.get("eleven_chars", 0)),
            "gemini_in_tokens": in_tok, "gemini_out_billable": out_tok,
            "gemini_cost_usd": round(cost, 6)}

@app.get("/api/download/{filename}")
def download(filename: str):
    p = OUTPUT_DIR / filename
    if not p.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p, filename=filename)