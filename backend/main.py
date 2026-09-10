import json
import os
import secrets
import shutil
import threading
import urllib.request
import uuid
import audio_enhance
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
    overlap_allowed: dict = {}
    total_duration: float = 0.0
    cloned_voice_ids: List[str] = []
    enhance_background: bool = True

class RegenerateLineRequest(BaseModel):
    job_id: str = ""
    segment: Segment
    segments: List[Segment] = []
    elevenlabs_api_key: str = ""
    voice_id: str = ""
    tempo_mode: str = "excellent"
    duration_mode: str = "exact"
    overlap_allowed: dict = {}
    total_duration: float = 0.0

class RemixRequest(BaseModel):
    job_id: str = ""
    segments: List[Segment] = []
    offsets: Dict[str, float] = {}
    total_duration: float = 0.0
    duration_mode: str = "exact"
    overlap_allowed: dict = {}

class MergeRequest(BaseModel):
    job_id: str
    enhance_background: bool = True

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
    "/", "/login", "/auth/callback", "/help", "/debug-keys", "/api/login",
    "/api/auth/session", "/api/auth/check", "/api/stripe/webhook",
    "/api/billing/packs", "/api/billing/checkout"
])

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/api/auth/"):
            return await call_next(request)
        if path.endswith((".css", ".js", ".svg", ".woff2", ".png", ".mp4", ".webm")):            return await call_next(request)
        if not _is_logged_in(request):
            if path.startswith("/api/"):
                return JSONResponse({"error": "Not logged in"}, status_code=401)
            return HTMLResponse('<script>window.location.href="/login";</script>', status_code=200)
        return await call_next(request)

app.add_middleware(AuthMiddleware)

# ==================== DEBUG ====================
@app.get("/api/user/info")
def user_info(request: Request):
    cookie = request.cookies.get("session", "")
    sb_token = _valid_tokens.get(cookie, "")
    if not sb_token or not SUPABASE_URL:
        return {"name": "Guest", "credits": -1, "is_guest": True}
    try:
        url = f"{SUPABASE_URL}/auth/v1/user"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {sb_token}",
            "apikey": SUPABASE_ANON_KEY
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            user_data = json.load(r)
        user_id = user_data.get("id", "")
        email = user_data.get("email", "User")
        display_name = email.split("@")[0] if email else "User"
        
        # Read credits securely via service key (bypasses RLS issues)
        credits = get_credits(user_id)
        if credits is None:
            credits = 100
            
        try:
            prof_url = f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=display_name"
            prof_req = urllib.request.Request(prof_url, headers={
                "Authorization": f"Bearer {sb_token}",
                "apikey": SUPABASE_ANON_KEY
            })
            with urllib.request.urlopen(prof_req, timeout=10) as pr:
                prof_data = json.load(pr)
            if prof_data:
                display_name = prof_data[0].get("display_name", display_name)
        except Exception:
            pass
            
        return {"name": display_name, "credits": credits, "is_guest": False}
    except Exception:
        return {"name": "Guest", "credits": -1, "is_guest": True}

@app.post("/api/logout")
def logout(response: Response):
    cookie = response.headers.get("set-cookie", "")
    tok = ""
    # Clear session
    response.delete_cookie("session")
    return {"ok": True}

@app.get("/demo_before.mp4")
def demo_before():
    return FileResponse(BASE_DIR / "demo_before.mp4", media_type="video/mp4")

@app.get("/demo_after.mp4")
def demo_after():
    return FileResponse(BASE_DIR / "demo_after.mp4", media_type="video/mp4")


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

# ==================== CREDITS, BILLING & AUTO-CLEANUP ====================
import math
import time as _time
try:
    import stripe
except Exception:
    stripe = None

SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

CREDIT_PACKS = {
    "starter":  {"amount_usd": 4.99,  "credits": 500},
    "standard": {"amount_usd": 14.99, "credits": 2000},
    "pro":      {"amount_usd": 39.99, "credits": 6000},
    "business": {"amount_usd": 99.99, "credits": 20000},
}

_session_users = {}   # our cookie token -> supabase user id
_job_charges = {}     # job_id -> {"credits_charged": n, "balance_after": m}
_abandoned_jobs = set()
_job_started = {}     # job_id -> timestamp

def _current_uid(request: Request):
    cookie = request.cookies.get("session", "")
    if not cookie:
        return None
    if _session_users.get(cookie):
        return _session_users[cookie]
    sb_token = _valid_tokens.get(cookie, "")
    if not sb_token or not SUPABASE_URL:
        return None
    try:
        req = urllib.request.Request(f"{SUPABASE_URL}/auth/v1/user", headers={
            "Authorization": f"Bearer {sb_token}", "apikey": SUPABASE_ANON_KEY})
        with urllib.request.urlopen(req, timeout=10) as r:
            uid = json.load(r).get("id")
        if uid:
            _session_users[cookie] = uid
        return uid
    except Exception:
        return None


def _sb_rpc(function: str, args: dict):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    body = json.dumps(args).encode("utf-8")
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/rpc/{function}", data=body, headers={
        "Content-Type": "application/json",
        "apikey": SUPABASE_SERVICE_KEY,
        
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception:
        return None


def get_credits(uid: str):
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print(f"[credits] MISSING CONFIG: uid={bool(uid)} url={bool(SUPABASE_URL)} key={bool(SUPABASE_SERVICE_KEY)}")
        return None
        
    # ADD THESE TWO LINES:
    print(f"[credits] URL being used: {SUPABASE_URL}")
    print(f"[credits] Key length: {len(SUPABASE_SERVICE_KEY)} characters")
        
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select=credits", headers={
            "apikey": SUPABASE_SERVICE_KEY,
            
        })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        print(f"[credits] SUCCESS for {uid}: {rows}")
        return rows[0]["credits"] if rows else None
    except Exception as e:
        print(f"[credits] ERROR for {uid}: {e}")
        return None


def deduct_credits(uid, amount):
    return _sb_rpc("deduct_credits", {"uid": uid, "amount": int(amount)})

def _fulfill_order(uid: str, session_id: str, credits: int):
    """Idempotently credit a paid checkout session. Safe to call many times."""
    if not uid or not session_id or not credits or not SUPABASE_SERVICE_KEY:
        return None
    try:
        chk = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/credit_orders?session_id=eq.{session_id}&select=session_id",
            headers={"apikey": SUPABASE_SERVICE_KEY}
        )
        with urllib.request.urlopen(chk, timeout=10) as r:
            if json.load(r):
                return "already-fulfilled"
                
        body = json.dumps({"session_id": session_id, "uid": uid, "credits": credits}).encode("utf-8")
        ins = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/credit_orders", 
            data=body, 
            headers={
                "Content-Type": "application/json",
                "apikey": SUPABASE_SERVICE_KEY,
            }
        )
        with urllib.request.urlopen(ins, timeout=10) as r:
            r.read()
            
        return _sb_rpc("add_credits", {"uid": uid, "amount": credits})
    except Exception as e:
        print("[stripe] fulfill error:", e)
        return None

def _watch_and_deduct(job_id, uid, kind):
    """Waits for the job to finish, then charges real credits."""

    def _run():
        while True:
            if kind == "transcribe":
                j = jobs_progress.get(job_id)
                if j is None:
                    return  # job abandoned/removed
                st = j.get("status")
                result = {}
            else:
                g = jobs_progress.get("generate")
                if g is None:
                    return
                st = g.get("status")
                result = g.get("result") or {}

            if st in ("done", "error"):
                break

            _time.sleep(2)

        if st != "done" or not uid:
            return

        if job_id in _abandoned_jobs:
            return  # user switched videos — never charge for abandoned work

        if kind == "transcribe":
            amount = 3
        else:
            chars = int(result.get("eleven_credits_used", 0) or 0)
            b = usage_bucket(job_id)

            gemini_usd = (
                (int(b.get("gemini_in", 0)) + int(b.get("audio_sec", 0) * 258))
                / 1e6
                * 0.30
                + int(b.get("gemini_out", 0)) / 1e6 * 2.50
            )

            amount = math.ceil(chars / 60) + max(
                1, math.ceil(gemini_usd / 0.01)
            )

        new_balance = deduct_credits(uid, amount)

        _job_charges[job_id] = {
            "credits_charged": amount,
            "balance_after": new_balance,
        }

    threading.Thread(target=_run, daemon=True).start()


# ---------- Stripe ----------
@app.get("/api/billing/packs")
def billing_packs():
    return {"packs": CREDIT_PACKS}


@app.post("/api/billing/checkout")
def billing_checkout(payload: dict, request: Request):
    if not stripe or not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "Payments are not configured yet."}, status_code=503)
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Login required to buy credits."}, status_code=401)
    pack_key = payload.get("pack", "")
    pack = CREDIT_PACKS.get(pack_key)
    if not pack:
        return JSONResponse({"error": "Unknown pack."}, status_code=400)
    stripe.api_key = STRIPE_SECRET_KEY
    origin = str(request.base_url).rstrip("/")
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            client_reference_id=uid,
            metadata={"pack": pack_key, "credits": str(pack["credits"]), "uid": uid},
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": f"Lisan AI {pack_key.capitalize()} Pack - {pack['credits']} credits"},
                    "unit_amount": int(round(pack["amount_usd"] * 100)),
                },
                "quantity": 1,
            }],
            success_url=origin + "/app#credits-purchased",
            cancel_url=origin + "/app",
        )
    except Exception as e:
        return JSONResponse({"error": f"Stripe error: {e}"}, status_code=502)
    return {"url": session.url}


@app.get("/api/billing/sync")
def billing_sync(request: Request):
    if not stripe or not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "not configured"}, status_code=503)
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "login required"}, status_code=401)
    stripe.api_key = STRIPE_SECRET_KEY
    added = 0
    try:
        sessions = stripe.checkout.Session.list(limit=100, client_reference_id=uid)
        for s in sessions.data:
            if s.get("payment_status") == "paid":
                credits = int((s.get("metadata") or {}).get("credits", 0))
                res = _fulfill_order(uid, s.get("id", ""), credits)
                if isinstance(res, int):
                    added += credits
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return {"added_sessions_credits": added}

@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    try:
        print("[stripe-webhook] received request")
        if not stripe or not STRIPE_WEBHOOK_SECRET:
            print("[stripe-webhook] NOT CONFIGURED: stripe=", bool(stripe), "secret=", bool(STRIPE_WEBHOOK_SECRET))
            return JSONResponse({"error": "not configured"}, status_code=503)
        raw = await request.body()
        sig = request.headers.get("stripe-signature", "")
        if not sig:
            print("[stripe-webhook] missing stripe-signature header")
            return JSONResponse({"error": "missing signature"}, status_code=400)
        try:
            event = stripe.Webhook.construct_event(raw, sig, STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            print("[stripe-webhook] SIGNATURE FAILED:", str(e))
            return JSONResponse({"error": "bad signature"}, status_code=400)
        etype = event.get("type")
        print("[stripe-webhook] event type:", etype)
        if etype == "checkout.session.completed":
            session = event["data"]["object"]
            uid = session.get("client_reference_id") or (session.get("metadata") or {}).get("uid")
            credits = int((session.get("metadata") or {}).get("credits", 0))
            print("[stripe-webhook] uid=", uid, "credits=", credits)
            if uid and credits:
                print("[stripe-webhook] SUPABASE_SERVICE_KEY present:", bool(SUPABASE_SERVICE_KEY))
                res = _fulfill_order(uid, session.get("id", ""), credits)
                print("[stripe] fulfill result:", res)
        return {"ok": True}
    except Exception as e:
        print("[stripe-webhook] UNEXPECTED ERROR:", str(e))
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)

# ---------- Auto-cleanup of old job files ----------
CLEANUP_RETENTION_HOURS = 6
CLEANUP_INTERVAL_MIN = 15


def _cleanup_worker():
    while True:
        _time.sleep(CLEANUP_INTERVAL_MIN * 60)
        try:
            cutoff = _time.time() - CLEANUP_RETENTION_HOURS * 3600
            removed = 0
            for d in (UPLOAD_DIR, OUTPUT_DIR):
                for p in d.glob("*"):
                    try:
                        if p.is_file() and p.stat().st_mtime < cutoff:
                            p.unlink()
                            removed += 1
                    except Exception:
                        pass
            for jid in list(_job_started.keys()):
                if _job_started[jid] < cutoff:
                    jobs_progress.pop(jid, None)
                    _job_started.pop(jid, None)
            if removed:
                print(f"[cleanup] removed {removed} old file(s)")
        except Exception as e:
            print("[cleanup] error:", e)


threading.Thread(target=_cleanup_worker, daemon=True).start()

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

@app.get("/logo.png")
def logo():
    return FileResponse(BASE_DIR / "logo.png", media_type="image/png")

@app.get("/help")
def help_page():
    return FileResponse(BASE_DIR / "help.html", media_type="text/html")

@app.get("/api/progress/enhance/{job_id}")
def enhance_progress(job_id: str):
    return audio_enhance.get_progress(job_id)

# ==================== API ROUTES ====================

@app.post("/api/transcribe")
async def transcribe(request: Request, file: UploadFile = File(...), speaker_count: int = Form(2), hf_token: str = Form("")):
    uid = _current_uid(request)
    bal = get_credits(uid) if uid else None
    if bal is not None and bal < 5:
        return JSONResponse({"error": f"Insufficient credits ({bal} left). Transcription costs 3 credits. Use ➕ Buy to get a pack."}, status_code=402)
    job_id = str(uuid.uuid4())
    _job_started[job_id] = _time.time()
    ext = Path(file.filename or "audio.mp4").suffix.lower() or ".mp4"
    dest = UPLOAD_DIR / f"{job_id}{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    jobs_progress[job_id] = {"status": "processing", "percent": 0,
                             "status_text": "Upload done, starting transcription...",
                             "is_video": ext in VIDEO_EXTS}
    threading.Thread(target=whisper_service.transcribe_worker,
                     args=(job_id, str(dest), HF_TOKEN, speaker_count), daemon=True).start()
    _watch_and_deduct(job_id, uid, "transcribe")
    return {"job_id": job_id}

@app.post("/api/abandon/{job_id}")
def abandon_job(job_id: str):
    _abandoned_jobs.add(job_id)
    jobs_progress.pop(job_id, None)
    try:
        for d in (UPLOAD_DIR, OUTPUT_DIR):
            for p in d.glob(f"{job_id}*"):
                if p.is_file():
                    p.unlink()
    except Exception:
        pass
    return {"ok": True}

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
def generate(req: GenerateRequest, request: Request):
    uid = _current_uid(request)
    bal = get_credits(uid) if uid else None
    if bal is not None and bal < 20:
        return JSONResponse({"error": f"Insufficient credits ({bal} left). Generation costs 1 credit per ~60 characters. Use ➕ Buy."}, status_code=402)
    req.elevenlabs_api_key = ELEVENLABS_API_KEY
    req.gemini_api_key = GEMINI_API_KEY
    jobs_progress["generate"] = {"status": "processing", "percent": 0, "result": None, "error": None}
    threading.Thread(target=eleven_service.generate_worker, args=(req,), daemon=True).start()
    _watch_and_deduct(req.job_id, uid, "generate")
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
def merge_video(req: MergeRequest, request: Request):
    uid = _current_uid(request)
    bal = get_credits(uid) if uid else None
    if bal is not None and bal < 1:
        return JSONResponse({"error": "Insufficient credits (merge costs 1 credit). Use ➕ Buy."}, status_code=402)
    if uid:
        deduct_credits(uid, 1)
    
    video = find_job_video(req.job_id)
    dub = OUTPUT_DIR / "final_dubbed.mp3"
    if video is None or not dub.exists():
        return {"error": "Missing video or dubbed audio. Run Generate first."}
    
    bg = job_background_audio(req.job_id)
    final = OUTPUT_DIR / "final_dubbed_video.mp4"
    
    if bg is not None:
        # Optionally enhance the separated background
        bg_to_use = bg
        if req.enhance_background:
            enhanced_bg = OUTPUT_DIR / f"{req.job_id}_bg_enhanced.wav"
            audio_enhance.enhance_background(req.job_id, str(bg), str(enhanced_bg))
            if enhanced_bg.exists() and enhanced_bg.stat().st_size > 0:
                bg_to_use = enhanced_bg
        mixed = OUTPUT_DIR / f"merge_mixed_{req.job_id}.wav"
        ffmpeg_utils.mix_two_audio(dub, bg_to_use, mixed)
        ffmpeg_utils.mux_audio_into_video(video, mixed, final)
        try:
            mixed.unlink()
        except Exception:
            pass
    else:
        ffmpeg_utils.mux_audio_into_video(video, dub, final)
    
    return {"status": "success", "has_background": bg is not None, "enhanced": req.enhance_background}

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
    out = {"eleven_credits": int(b.get("eleven_chars", 0)),
           "gemini_in_tokens": in_tok, "gemini_out_billable": out_tok,
           "gemini_cost_usd": round(cost, 6)}
    out.update(_job_charges.get(job_id, {}))
    return out

@app.get("/api/download/{filename}")
def download(filename: str):
    p = OUTPUT_DIR / filename
    if not p.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p, filename=filename)
@app.get("/api/segment_audio/{job_id}/{segment_id}")
def segment_audio(job_id: str, segment_id: str):
    if "/" in segment_id or ".." in segment_id: return JSONResponse({"error": "bad id"}, status_code=400)
    for ext, mt2 in ((".wav", "audio/wav"), (".mp3", "audio/mpeg")):
        p = OUTPUT_DIR / f"{segment_id}_stretched{ext}"
        if p.exists():
            fr = FileResponse(p, media_type=mt2); fr.headers["Cache-Control"] = "no-store"; return fr
    return JSONResponse({"error": "not found"}, status_code=404)

@app.post("/api/cleanup_voices")
def cleanup_voices(payload: dict = {}):
    return eleven_service.cleanup_cloned_voices(ELEVENLABS_API_KEY, payload.get("keep", []))

@app.post("/api/upload_custom_voice")
async def upload_custom_voice(request: Request, file: UploadFile = File(...), speaker: str = Form("Speaker 1"), job_id: str = Form("")):
    uid = _current_uid(request)
    if not uid: return JSONResponse({"error": "login required"}, status_code=401)
    nm = (file.filename or "").lower()
    if not nm.endswith((".mp3", ".wav")): return {"error": "Only MP3 or WAV files are allowed."}
    data = await file.read()
    if len(data) > 10 * 1024 * 1024: return {"error": "File too large (max 10 MB / 20 seconds)."}
    import uuid as _u
    tmp = OUTPUT_DIR / f"custom_upload_{_u.uuid4().hex}.bin"
    tmp.write_bytes(data)
    try: res = eleven_service.add_custom_voice(job_id or "custom", speaker, tmp, ELEVENLABS_API_KEY)
    except Exception as e: res = f"ERROR: {e}"
    finally:
        try: tmp.unlink()
        except Exception: pass
    if isinstance(res, str) and res.startswith("ERROR"): return {"error": res}
    return {"status": "success", "voice_id": res}

def account_summary(request: Request):
    uid = _current_uid(request)
    if not uid: return JSONResponse({"error": "login required"}, status_code=401)
    return {"credits": get_credits(uid) or 100, "purchases": [], "spends": []}

@app.get("/account")
def account_page():
    return FileResponse(BASE_DIR / "account.html")


# ===== USAGE RECORDING: log every credit deduction to credit_spends =====
def _record_spend(uid, action, credits, job_id=None):
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/credit_spends",
            data=json.dumps({"uid": uid, "action": action, "job_id": job_id, "credits": credits}).encode("utf-8"),
            headers={"apikey": SUPABASE_SERVICE_KEY, "Content-Type": "application/json", "Prefer": "return=minimal"},
            method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
try:
    _od = deduct_credits
    def deduct_credits(*a, **k):
        r = _od(*a, **k)
        try:
            uid = a[0] if len(a) > 0 else k.get("uid")
            amt = a[1] if len(a) > 1 else k.get("amount", k.get("credits"))
            act = a[2] if len(a) > 2 else k.get("action", "deduction")
            jid = a[3] if len(a) > 3 else k.get("job_id")
            _record_spend(uid, act or "deduction", amt, jid)
        except Exception:
            pass
        return r
except NameError:
    pass



@app.get("/api/account/summary")
def account_summary(request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "login required"}, status_code=401)
        
    import urllib.request as _ur
    
    def _fetch_and_filter(table):
        url = f"{SUPABASE_URL}/rest/v1/{table}?select=*&order=created_at.desc&limit=500"
        hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        try:
            req = _ur.Request(url, headers=hdrs)
            with _ur.urlopen(req, timeout=10) as r:
                all_rows = json.load(r)
            if isinstance(all_rows, list):
                return [x for x in all_rows if str(x.get("uid")) == str(uid)]
        except Exception as e:
            print(f"[account_summary] Error fetching {table}: {e}")
        return []

    spends = _fetch_and_filter("credit_spends")
    orders = _fetch_and_filter("credit_orders")
        
    return {
        "credits": get_credits(uid) or 0,
        "purchases": orders,
        "spends": spends
    }

def account_summary_diag(request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "login required"}, status_code=401)
        
    import urllib.request as _ur
    url = f"{SUPABASE_URL}/rest/v1/credit_spends?select=*&order=created_at.desc&limit=5"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    
    raw_data = []
    err = None
    status_code = None
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            status_code = r.status
            raw_data = json.load(r)
    except Exception as e:
        err = str(e)
        
    return {
        "uid_used": str(uid),
        "supabase_status": status_code,
        "raw_count": len(raw_data) if isinstance(raw_data, list) else "not a list",
        "error": err,
        "first_row": raw_data[0] if raw_data else None,
        "credits": get_credits(uid) or 0
    }
