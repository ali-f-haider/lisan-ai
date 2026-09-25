from fastapi.staticfiles import StaticFiles
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

from config import (BASE_DIR, DATA_DIR, UPLOAD_DIR, OUTPUT_DIR,
                    GEMINI_API_KEY, ELEVENLABS_API_KEY, HF_TOKEN, APP_PASSWORD, ADMIN_PASSWORD,
                    RESEND_API_KEY, CONTACT_TO_EMAIL, APP_VERSION, SENTRY_DSN, FAL_API_KEY,
                    LIPSYNC_ENABLED)
import app_state
from app_state import jobs_progress, usage_bucket
from models import Segment
import whisper_service
import gemini_service
import eleven_service
import ffmpeg_utils
import lipsync_service
import r2_backup
import railway_monitor
from media_paths import resolve_job_audio, find_job_video, job_background_audio

# Sentry: reports unhandled exceptions from the live server automatically.
# Wrapped in try/except so a missing package or bad DSN never takes the app
# down -- monitoring is a nice-to-have, not something that should be able to
# break dubbing jobs.
#
# disabled_integrations turns off sentry-sdk's auto-enabled Hugging Face Hub
# integration. This app only uses huggingface_hub for HF_TOKEN-gated model
# downloads (pyannote diarization) -- never InferenceClient.chat_completion,
# which is what that integration patches. The pinned huggingface-hub==0.21.4
# in requirements.txt predates that method existing at all, so with the
# integration left on, sentry_sdk.init() raised
# "AttributeError: type object 'InferenceClient' has no attribute
# 'chat_completion'" on every single startup and Sentry never initialized --
# confirmed by reproducing it locally against the exact pinned versions.
try:
    if SENTRY_DSN:
        import sentry_sdk
        from sentry_sdk.integrations.huggingface_hub import HuggingfaceHubIntegration
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            traces_sample_rate=0.1,
            send_default_pii=False,
            disabled_integrations=[HuggingfaceHubIntegration()],
        )
except Exception as _sentry_ex:
    print(f"[sentry] init skipped: {_sentry_ex}")

app = FastAPI()

@app.get("/help")
def public_help():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "help.html"))

@app.get("/help.html")
def public_help_html():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "help.html"))


import os
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/privacy")
@app.get("/privacy.html")
def read_privacy():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "privacy.html"))

@app.get("/terms")
@app.get("/terms.html")
def read_terms():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "terms.html"))

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

class VoiceLibrarySearchRequest(BaseModel):
    language: List[str] = []
    accent: str = ""
    gender: str = ""
    age: str = ""
    category: str = ""
    high_quality: bool = False
    search: str = ""
    voice_type: str = ""
    page_size: int = 6
    page_token: str = ""

class VoiceLibraryAddRequest(BaseModel):
    public_owner_id: str
    voice_id: str
    new_name: str = "Voice"

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
    dead_space_allowed: dict = {}
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
    dead_space_allowed: dict = {}
    total_duration: float = 0.0

class RemixRequest(BaseModel):
    job_id: str = ""
    segments: List[Segment] = []
    offsets: Dict[str, float] = {}
    gains: Dict[str, float] = {}
    total_duration: float = 0.0
    duration_mode: str = "exact"
    overlap_allowed: dict = {}
    dead_space_allowed: dict = {}

class MergeRequest(BaseModel):
    job_id: str
    enhance_background: bool = True

class LipSyncRequest(BaseModel):
    job_id: str
    provider: str = "veed"
    model: str = "lipsync-2"
    sync_key: str = ""

class TashkeelItem(BaseModel):
    segment_id: str
    arabic_text: str

class TashkeelRequest(BaseModel):
    items: List[TashkeelItem]

class ContactRequest(BaseModel):
    name: str = ""
    email: str
    message: str
    hp: str = ""  # honeypot -- real visitors never see or fill this field

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
    "/", "/login", "/auth/callback", "/help", "/privacy", "/privacy.html", "/terms", "/terms.html", "/debug-keys", "/api/login",
    "/api/auth/session", "/api/auth/check", "/api/stripe/webhook",
    "/api/billing/packs", "/api/billing/checkout", "/api/contact"
, "/help.html", "/admin"])

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/api/auth/") or path.startswith("/api/admin/"):
            return await call_next(request)
        if path.endswith((".css", ".js", ".svg", ".woff2", ".png", ".mp4", ".webm")):            return await call_next(request)
        if not _is_logged_in(request):
            if path.startswith("/api/"):
                return JSONResponse({"error": "Not logged in"}, status_code=401)
            return HTMLResponse('<script>window.location.href="/login";</script>', status_code=200)
        return await call_next(request)

app.add_middleware(AuthMiddleware)

# --- Simple in-memory brute-force guard for password-based login endpoints.
# Resets on process restart and is per-instance (fine for a single Railway
# worker); it isn't meant to be a full WAF, just to stop unthrottled password
# guessing against /api/login and /api/admin/login. ---
_login_fails: Dict[str, List[float]] = {}
LOGIN_MAX_ATTEMPTS = 8
LOGIN_WINDOW_SEC = 300  # 5 minutes

# --- Same idea, for the public /api/contact form: stop it being used to
# mass-spam CONTACT_TO_EMAIL. ---
_contact_attempts: Dict[str, List[float]] = {}
CONTACT_MAX_ATTEMPTS = 5
CONTACT_WINDOW_SEC = 3600  # 1 hour

def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def _login_rate_limited(request: Request) -> bool:
    """True if this client has too many recent failed attempts."""
    ip = _client_ip(request)
    now = time.time()
    attempts = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_WINDOW_SEC]
    _login_fails[ip] = attempts
    return len(attempts) >= LOGIN_MAX_ATTEMPTS

def _record_login_fail(request: Request):
    ip = _client_ip(request)
    _login_fails.setdefault(ip, []).append(time.time())

# --- Same sliding-window idea, generalized for the endpoints that cost
# either heavy server compute (transcribe/generate/merge -- already
# credit-gated, but nothing stopped a script from calling them faster than
# any real user ever would) or a real Gemini/ElevenLabs API charge per call
# with no credit check at all (translate/tashkeel/detect_emotions/clone/
# lipsync/regenerate_line). Keyed per logged-in user (falling back to IP
# for a session with no Supabase uid, e.g. the shared APP_PASSWORD login)
# rather than per IP, since every one of these endpoints already requires
# being logged in and uid is a far more reliable identity than an IP that
# a shared office/VPN can collide on. Limits are deliberately generous --
# this is meant to stop scripted abuse, not to get in the way of someone
# actively iterating on a real dubbing job. Resets on process restart and
# is per-instance, same tradeoff as the login/contact guards above.
_rate_buckets: Dict[str, Dict[str, List[float]]] = {}
HEAVY_RATE_MAX = 30          # /api/transcribe, /api/generate, /api/merge_video
HEAVY_RATE_WINDOW_SEC = 600  # 10 minutes
LIGHT_RATE_MAX = 60          # /api/translate, /api/tashkeel, /api/detect_emotions,
LIGHT_RATE_WINDOW_SEC = 600  # /api/clone, /api/lipsync, /api/regenerate_line
_RATE_LIMIT_MSG = "Too many requests. Please slow down and try again in a few minutes."

def _rate_key(request: Request) -> str:
    uid = _current_uid(request)
    return f"uid:{uid}" if uid else f"ip:{_client_ip(request)}"

def _rate_limited(request: Request, bucket: str, max_attempts: int, window_sec: int) -> bool:
    """True if this caller is already over the limit for `bucket` (caller
    should return a 429 in that case). Otherwise records this call and
    returns False."""
    key = _rate_key(request)
    now = time.time()
    store = _rate_buckets.setdefault(bucket, {})
    attempts = [t for t in store.get(key, []) if now - t < window_sec]
    if len(attempts) >= max_attempts:
        store[key] = attempts
        return True
    attempts.append(now)
    store[key] = attempts
    return False

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
        
        # Read credits securely via service key (bypasses RLS issues). A
        # None here means no profile row exists yet for this user -- this
        # fallback shows the admin-configured starting balance (freeCredits)
        # instead of a hardcoded number, though the real balance a new user
        # ends up with is whatever Supabase grants when it creates their
        # profile row (outside this codebase) -- keep the two in sync by
        # eye if you change freeCredits in the admin panel.
        credits = get_credits(user_id)
        if credits is None:
            credits = int(_get_pricing_config().get("freeCredits", 100))
            
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
def debug_keys(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
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
def login_legacy(req: LoginRequest, response: Response, request: Request):
    if _login_rate_limited(request):
        return JSONResponse({"error": "Too many attempts. Try again in a few minutes."}, status_code=429)
    if APP_PASSWORD and hmac.compare_digest(str(req.password), str(APP_PASSWORD)):
        tok = secrets.token_hex(32)
        _sessions.add(tok)
        response.set_cookie("session", tok, httponly=True, max_age=86400 * 7, samesite="lax")
        return {"ok": True}
    _record_login_fail(request)
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

# ---- Credit packs: ONE default list + ONE keying helper, shared by every ----
# endpoint that returns packs (get_packs() for checkout, billing_packs_dynamic()
# for the landing page / buy modal, and _get_pricing_config()'s defaults).
# Each pack carries its own explicit "key" (e.g. "starter") set by admin.html —
# that key is what the public site and Stripe checkout use to identify the
# pack. Older packs saved before this field existed have no "key" yet; for
# those only, _pack_dict_key() derives one from the name the same way the
# old code always did, so nothing on the live site changes until you re-save
# that pack in admin (at which point its real key gets stored permanently).
DEFAULT_PACKS = [
    {"key": "starter",  "name": "Starter",  "credits": 1500,  "price_usd": 15.0,  "bonus_pct": 0,  "stripe_link": ""},
    {"key": "standard", "name": "Standard", "credits": 4000,  "price_usd": 35.0,  "bonus_pct": 14, "stripe_link": ""},
    {"key": "pro",      "name": "Pro",      "credits": 10000, "price_usd": 75.0,  "bonus_pct": 33, "stripe_link": ""},
    {"key": "business", "name": "Studio",   "credits": 25000, "price_usd": 150.0, "bonus_pct": 66, "stripe_link": ""},
]

def _pack_dict_key(p):
    """The dict key a pack shows up under publicly. Prefers the pack's own
    explicit 'key' field; only derives one from the name (old behavior) when
    a pack was saved before 'key' existed."""
    explicit = str(p.get("key") or "").strip().lower()
    if explicit:
        return explicit
    name = (p.get("name") or "").strip()
    if not name:
        return ""
    key = name.lower().split()[0]
    if key in ("studio", "business"):
        key = "business"
    return key

def _keyed_packs(packs_array):
    """Maps the admin's pack array into the {key: {...}} structure the buy
    modal and landing page expect."""
    keyed = {}
    for p in packs_array:
        if not isinstance(p, dict):
            continue
        key = _pack_dict_key(p)
        if not key:
            continue
        keyed[key] = {
            "name": p.get("name", "") or key.capitalize(),
            "credits": int(p.get("credits", 0) or 0),
            "amount_usd": float(p.get("price_usd", 0) or 0),
            "bonus_pct": int(p.get("bonus_pct", 0) or 0),
            "stripe_link": p.get("stripe_link", "") or ""
        }
    return keyed

def get_packs():
    """Returns credit packs keyed by each pack's own key (see _keyed_packs).
    Reads from pricing_config table — admin panel is the single source of truth."""
    cfg = _get_pricing_config()
    packs_array = cfg.get("packs") or DEFAULT_PACKS
    return _keyed_packs(packs_array)


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


def set_credits(uid, new_amount):
    """Updates user's credits in profiles table."""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    import urllib.request as _ur
    body = json.dumps({"credits": int(new_amount)}).encode("utf-8")
    url = f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}"
    hdrs = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    try:
        req = _ur.Request(url, data=body, headers=hdrs, method="PATCH")
        with _ur.urlopen(req, timeout=10) as r:
            return r.status in (200, 204)
    except Exception as ex:
        print(f"[credits] set_credits error: {ex}")
        return False

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
                g = jobs_progress.get(f"generate_{job_id}")
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

        cfg = _get_pricing_config()
        if kind == "transcribe":
            amount = int(cfg.get("transcribeCredits", 3))
        else:
            chars = int(result.get("eleven_credits_used", 0) or 0)
            b = usage_bucket(job_id)

            gemini_usd = (
                (int(b.get("gemini_in", 0)) + int(b.get("audio_sec", 0) * 258))
                / 1e6
                * 0.30
                + int(b.get("gemini_out", 0)) / 1e6 * 2.50
            )

            chars_per_credit = int(cfg.get("charsPerCredit", 60))
            if chars_per_credit <= 0:
                chars_per_credit = 60

            amount = math.ceil(chars / chars_per_credit) + max(
                1, math.ceil(gemini_usd / 0.01)
            )

        # Duration (seconds) of the actual dubbed audio produced by this job —
        # only meaningful for "generate" (merge/transcribe don't produce new
        # generated audio). Recorded to credit_spends for the admin dashboard's
        # generated-minutes tracking.
        gen_seconds = result.get("final_duration") if kind == "generate" else None
        new_balance = deduct_credits(uid, amount, kind, job_id, gen_seconds)

        _job_charges[job_id] = {
            "credits_charged": amount,
            "balance_after": new_balance,
        }

    threading.Thread(target=_run, daemon=True).start()


# ---------- Stripe ----------

@app.post("/api/billing/checkout")
def billing_checkout(payload: dict, request: Request):
    if not stripe or not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "Payments are not configured yet."}, status_code=503)
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Login required to buy credits."}, status_code=401)
    pack_key = payload.get("pack", "")
    pack = get_packs().get(pack_key)
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
                    "product_data": {"name": f"Lisan AI {pack.get('name') or pack_key.capitalize()} Pack - {pack['credits']} credits"},
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
        sessions = stripe.checkout.Session.list(limit=100)
        for s in sessions.data:
            if s.get("client_reference_id") != uid:
                continue
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
# Two retention tiers now, not one:
#  - Everything in UPLOAD_DIR (the original upload, Demucs stems, etc.) and
#    every intermediate file in OUTPUT_DIR (per-line TTS clips, mix drafts,
#    lipsync working files...) is still purged after CLEANUP_RETENTION_HOURS,
#    same as always.
#  - The finished result of a job -- the files a user would actually want to
#    come back and download later, matched by the job-scoped filenames below
#    -- is kept for CLEANUP_FINAL_OUTPUT_DAYS instead, so it survives long
#    enough to show up on the Account page (see /api/my_jobs).
# Update privacy.html and terms.html if either number changes -- both make a
# stated retention-time commitment to users.
CLEANUP_RETENTION_HOURS = 6
CLEANUP_FINAL_OUTPUT_DAYS = 30
CLEANUP_INTERVAL_MIN = 15
# How long with no new transcription job before the cleanup loop drops the
# OS's cached copy of the Whisper/pyannote model weight files (see
# whisper_service.flush_model_file_cache). This is the actual multi-GB
# memory plateau fix -- per-job file cleanup above only ever covered the
# much smaller uploaded/intermediate audio files, not the model files
# themselves. Long enough that normal back-to-back usage during an active
# session never pays the cold-reload cost; short enough that a genuinely
# idle server (nobody using the site) isn't billed for gigabytes of RAM it
# isn't using. Raise this if jobs sometimes arrive minutes apart and a slow
# first-job-after-a-gap turns out to matter more than the memory cost.
MODEL_CACHE_IDLE_MINUTES = 20
# How many days before the 30-day auto-delete to email the owner a
# heads-up. Requires the `expiry_notices` table (see the SQL note above
# _check_expiring_outputs below) and RESEND_API_KEY to actually be set --
# both fail silently (no email sent, nothing crashes) if missing.
CLEANUP_WARNING_DAYS = 3
# Only re-scan for jobs entering the warning window this often -- a 3-day
# warning doesn't need 15-minute precision, and this keeps the extra
# Supabase/Resend calls rare.
EXPIRY_CHECK_INTERVAL_HOURS = 6

# Suffixes that mark a file in OUTPUT_DIR as a job's *finished* output rather
# than an intermediate working file. Keep this in sync with the filenames
# written in main.py (merge_video), eleven_service.py, tts_service.py and
# lipsync_service.py. Also what r2_backup.py treats as "back this up".
_FINAL_OUTPUT_SUFFIXES = ("_final_dubbed.mp3", "_final_dubbed_video.mp4", "_final_lipsync.mp4")


def _is_final_output(path):
    return path.parent == OUTPUT_DIR and path.name.endswith(_FINAL_OUTPUT_SUFFIXES)


# ---------- "Your file expires soon" email ----------
# Needs one new Supabase table (run once in the SQL editor):
#   CREATE TABLE IF NOT EXISTS expiry_notices (
#     job_id text PRIMARY KEY,
#     uid uuid,
#     notified_at timestamptz DEFAULT now()
#   );
# This is what makes the "only email once per job" de-dup survive a
# Railway restart -- an in-memory set would re-send every warning after
# every redeploy.

def _job_id_from_output_path(path):
    for suffix in _FINAL_OUTPUT_SUFFIXES:
        if path.name.endswith(suffix):
            return path.name[: -len(suffix)]
    return None


def _uid_for_job(job_id):
    """Same source of truth as account ownership (_job_belongs_to_uid) --
    credit_spends already records uid+job_id for every generate/merge."""
    if not job_id or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    import urllib.request as _ur
    import urllib.parse as _up
    url = f"{SUPABASE_URL}/rest/v1/credit_spends?job_id=eq.{_up.quote(job_id)}&select=uid&limit=1"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        return rows[0].get("uid") if rows else None
    except Exception:
        return None


def _email_for_uid(uid):
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    import urllib.request as _ur
    url = f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select=email"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        return (rows[0].get("email") or "").strip() or None if rows else None
    except Exception:
        return None


def _already_notified(job_id):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return True  # can't check -- fail toward not spamming, not over-sending
    import urllib.request as _ur
    import urllib.parse as _up
    url = f"{SUPABASE_URL}/rest/v1/expiry_notices?job_id=eq.{_up.quote(job_id)}&select=job_id&limit=1"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            return bool(json.load(r))
    except Exception:
        return True


def _mark_notified(job_id, uid):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    import urllib.request as _ur
    body = json.dumps({"job_id": job_id, "uid": uid}).encode("utf-8")
    req = _ur.Request(
        f"{SUPABASE_URL}/rest/v1/expiry_notices",
        data=body,
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Content-Type": "application/json",
            "Prefer": "return=minimal,resolution=merge-duplicates",
        },
        method="POST")
    try:
        _ur.urlopen(req, timeout=10)
    except Exception as ex:
        print(f"[expiry-notice] could not record notice for {job_id}: {ex}")


# ---------- Voice-cloning consent audit trail ----------
# Needs one new Supabase table (run once in the SQL editor):
#   CREATE TABLE IF NOT EXISTS consent_records (
#     id bigserial PRIMARY KEY,
#     job_id text,
#     uid uuid,
#     ip text,
#     consent_text text,
#     created_at timestamptz DEFAULT now()
#   );
# Enforcement itself already happened by the time this runs (/api/transcribe
# rejects the upload outright if voice_consent wasn't sent) -- this is only
# the paper trail of who certified what, for if it's ever needed later. Runs
# in a background thread so a slow/down Supabase never delays the user's
# upload, and silently does nothing if Supabase isn't configured.
_VOICE_CONSENT_TEXT = (
    "I hereby certify that I have all necessary rights or consents to "
    "upload and translate this audio/video, which could result in the "
    "cloning of the associated voices."
)


def _record_voice_consent(job_id, uid, request):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    client_ip = request.client.host if request and request.client else None

    def _run():
        import urllib.request as _ur
        body = json.dumps({
            "job_id": job_id,
            "uid": uid,
            "ip": client_ip,
            "consent_text": _VOICE_CONSENT_TEXT,
        }).encode("utf-8")
        req = _ur.Request(
            f"{SUPABASE_URL}/rest/v1/consent_records",
            data=body,
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            method="POST")
        try:
            _ur.urlopen(req, timeout=10)
        except Exception as ex:
            print(f"[voice-consent] could not record consent for {job_id}: {ex}")

    threading.Thread(target=_run, daemon=True).start()


def _send_expiry_email(to_email, days_left):
    """Same Resend HTTP API call as /api/contact -- same 'from' domain,
    same Cloudflare-safe User-Agent. Silently does nothing if RESEND_API_KEY
    isn't set (matches /api/contact's own fallback behavior)."""
    if not RESEND_API_KEY or not to_email:
        return False
    plural = "s" if days_left != 1 else ""
    subject = f"Your Lisan AI file will be deleted in {days_left} day{plural}"
    body_text = (
        "Hi,\n\n"
        f"Your dubbed audio/video on Lisan AI is scheduled to be automatically "
        f"deleted in about {days_left} day{plural}, as part of our 30-day storage policy.\n\n"
        "If you'd like to keep it, download it now from your account page:\n"
        "https://lisanai.org/account\n\n"
        "After that, this file cannot be recovered.\n\n"
        "-- Lisan AI"
    )
    payload = json.dumps({
        "from": "Lisan AI <noreply@lisanai.org>",
        "to": [to_email],
        "subject": subject,
        "text": body_text,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
                "User-Agent": "LisanAI-Backend/1.0 (+https://lisanai.org)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 201)
    except Exception as ex:
        print(f"[expiry-notice] Resend send failed: {ex}")
        return False


def _check_expiring_outputs():
    """Emails a job's owner once when their finished output enters the
    CLEANUP_WARNING_DAYS window before CLEANUP_FINAL_OUTPUT_DAYS auto-delete.
    De-duped via the expiry_notices table (see the SQL note above), so this
    is safe to call repeatedly -- a job already recorded there is skipped."""
    try:
        now = _time.time()
        warn_after_days = CLEANUP_FINAL_OUTPUT_DAYS - CLEANUP_WARNING_DAYS
        for p in OUTPUT_DIR.glob("*"):
            if not p.is_file() or not _is_final_output(p):
                continue
            age_days = (now - p.stat().st_mtime) / 86400
            if age_days < warn_after_days:
                continue
            job_id = _job_id_from_output_path(p)
            if not job_id or _already_notified(job_id):
                continue
            uid = _uid_for_job(job_id)
            email = _email_for_uid(uid) if uid else None
            if not email:
                continue
            days_left = max(1, round(CLEANUP_FINAL_OUTPUT_DAYS - age_days))
            if _send_expiry_email(email, days_left):
                _mark_notified(job_id, uid)
    except Exception as e:
        print("[expiry-notice] error:", e)


_last_expiry_check = 0.0


def _cleanup_worker():
    global _last_expiry_check
    while True:
        _time.sleep(CLEANUP_INTERVAL_MIN * 60)
        try:
            r2_backup.backup_final_outputs(OUTPUT_DIR, _is_final_output)
        except Exception as e:
            print("[r2-backup] sweep error:", e)
        try:
            now = _time.time()
            short_cutoff = now - CLEANUP_RETENTION_HOURS * 3600
            final_cutoff = now - CLEANUP_FINAL_OUTPUT_DAYS * 86400
            removed = 0
            for d in (UPLOAD_DIR, OUTPUT_DIR):
                for p in d.glob("*"):
                    try:
                        if not p.is_file():
                            continue
                        cutoff = final_cutoff if _is_final_output(p) else short_cutoff
                        if p.stat().st_mtime < cutoff:
                            p.unlink()
                            removed += 1
                    except Exception:
                        pass
            for jid in list(_job_started.keys()):
                if _job_started[jid] < short_cutoff:
                    jobs_progress.pop(jid, None)
                    _job_started.pop(jid, None)
            if removed:
                print(f"[cleanup] removed {removed} old file(s)")
        except Exception as e:
            print("[cleanup] error:", e)

        try:
            idle_minutes = (_time.time() - app_state.last_job_activity) / 60
            if idle_minutes >= MODEL_CACHE_IDLE_MINUTES:
                whisper_service.flush_model_file_cache()
        except Exception as e:
            print("[cleanup] model-cache flush error:", e)

        if _time.time() - _last_expiry_check > EXPIRY_CHECK_INTERVAL_HOURS * 3600:
            _last_expiry_check = _time.time()
            _check_expiring_outputs()


threading.Thread(target=_cleanup_worker, daemon=True).start()
railway_monitor.start()

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
    raise Exception(last or "Translation AI call failed")

# ==================== STATIC FILES ====================

@app.get("/")
def landing():
    # If a GA4 Measurement ID is set in the admin panel, inject Google's
    # own standard gtag.js snippet directly into <head> server-side, on
    # the raw HTML -- not via a client-side fetch-then-inject like the
    # first version of this did. Google's own automated "tag not detected"
    # checker (and most bot/crawler-based checks) reads the page source
    # without waiting for an extra async round-trip, so a tag that only
    # appears after a follow-up JS fetch can look "not installed" even
    # though it works for real visitors. Injecting it straight into the
    # HTML response matches exactly what Google's install instructions
    # ask for ("put this code after <head> in every page") and is
    # reliably detectable.
    html = (BASE_DIR / "landing.html").read_text(encoding="utf-8")
    import re as _re
    ga_id = (_get_pricing_config().get("gaMeasurementId") or "").strip()
    # Only accept a well-formed GA4 ID (e.g. "G-NBWB2VYYE4") -- this value
    # gets embedded directly into raw HTML/JS below with no escaping, so
    # validating the shape here (rather than trusting whatever is in the
    # DB) is what keeps that safe.
    if _re.fullmatch(r"G-[A-Za-z0-9]{4,20}", ga_id):
        snippet = (
            "<!-- Google tag (gtag.js) -->\n"
            f'<script async src="https://www.googletagmanager.com/gtag/js?id={ga_id}"></script>\n'
            "<script>\n"
            "  window.dataLayer = window.dataLayer || [];\n"
            "  function gtag(){dataLayer.push(arguments);}\n"
            "  gtag('js', new Date());\n"
            f"  gtag('config', '{ga_id}');\n"
            "</script>\n"
        )
        html = html.replace("<head>", "<head>\n" + snippet, 1)
    return HTMLResponse(html)


# "no-cache" (despite the name) still lets the browser cache these -- it
# just makes it revalidate with the server first every time (a cheap
# conditional GET against the Last-Modified/ETag FileResponse already sets,
# 304 if unchanged) instead of silently reusing whatever copy it has for
# some heuristic length of time. Without this, a deploy that changes
# app.js/index.html/styles.css can go unnoticed by an already-open browser
# tab or a "hard refresh"-less reload, which cost real debugging time more
# than once (the Step 7 visibility investigation in particular).
_NO_CACHE_HEADERS = {"Cache-Control": "no-cache"}

@app.get("/app")
def home():
    return FileResponse(BASE_DIR / "index.html", headers=_NO_CACHE_HEADERS)

@app.get("/app.js")
def app_js():
    return FileResponse(BASE_DIR / "app.js", media_type="application/javascript", headers=_NO_CACHE_HEADERS)

@app.get("/styles.css")
def styles():
    return FileResponse(BASE_DIR / "styles.css", media_type="text/css", headers=_NO_CACHE_HEADERS)

@app.get("/logo.png")
def logo():
    return FileResponse(BASE_DIR / "logo.png", media_type="image/png")

def privacy_page():
    return FileResponse(BASE_DIR / "privacy.html")


def terms_page():
    return FileResponse(BASE_DIR / "terms.html")


def privacy_page():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "privacy.html"))

def terms_page():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "terms.html"))

def help_page():
    return FileResponse(BASE_DIR / "help.html", media_type="text/html")

@app.get("/api/progress/enhance/{job_id}")
def enhance_progress(job_id: str):
    return audio_enhance.get_progress(job_id)

# ==================== API ROUTES ====================

# Upload duration gates for Step 1. Two ranges, chosen by the "generate
# lip-sync too" checkbox in the upload form: lip-sync goes straight to Wan
# 3.0's own native single-call limits (no chunking/merging -- Ali decided
# against that approach since a chunk boundary isn't guaranteed to land on
# a silence gap), so a job that wants lip-sync must already fit inside that
# window at upload time. A job that doesn't want lip-sync uses the wider
# range instead -- unchanged from what the app already enforced (the
# frontend has capped uploads at 60s for a while; see MAX_DURATION_SEC in
# app.js). Both share the same 4-second floor: below that there usually
# isn't enough clean audio for voice cloning to have a chance, or for Wan
# 3.0 to accept the clip at all.
LIPSYNC_MIN_SEC = 4
LIPSYNC_MAX_SEC = 15
NO_LIPSYNC_MIN_SEC = 4
NO_LIPSYNC_MAX_SEC = 60
# Below this, cloning is still allowed (see the 4s floor above) but the
# result may not sound convincing -- ElevenLabs' own guidance is that ~30s
# of clean audio is where they've seen consistently good clones. This only
# drives a non-blocking warning shown to the user, not a rejection.
CLONE_QUALITY_WARN_SEC = 30


@app.post("/api/transcribe")
async def transcribe(request: Request, file: UploadFile = File(...), speaker_count: int = Form(0), hf_token: str = Form(""), voice_consent: str = Form(""), lipsync: str = Form("false")):
    if _rate_limited(request, "transcribe", HEAVY_RATE_MAX, HEAVY_RATE_WINDOW_SEC):
        return JSONResponse({"error": _RATE_LIMIT_MSG}, status_code=429)
    # The client already blocks the button until the voice-rights checkbox is
    # checked (Step 1), but that's JS and can't be trusted -- anyone posting
    # directly to this endpoint bypasses it, so this is the enforcement that
    # actually matters. Same reasoning as the duration cap a few lines below.
    if voice_consent.strip().lower() not in ("true", "1", "yes", "on"):
        return JSONResponse({"error": "You must certify you have the necessary rights or consents for the voices in this file before uploading."}, status_code=400)
    uid = _current_uid(request)
    bal = get_credits(uid) if uid else None
    transcribe_cost = int(_get_pricing_config().get("transcribeCredits", 3))
    if bal is not None and bal < transcribe_cost:
        return JSONResponse({"error": f"Insufficient credits ({bal} left). Transcription costs {transcribe_cost} credits. Use ➕ Buy to get a pack."}, status_code=402)
    job_id = str(uuid.uuid4())
    _job_started[job_id] = _time.time()
    ext = Path(file.filename or "audio.mp4").suffix.lower() or ".mp4"
    dest = UPLOAD_DIR / f"{job_id}{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    # Server-side duration cap. The client already blocks out-of-range
    # clips in its own UI, but that check runs in JavaScript and can't be
    # trusted -- anyone posting directly to this endpoint bypasses it
    # entirely, so this is the enforcement that actually matters. Which
    # range applies depends on whether this upload wants lip-sync (see the
    # LIPSYNC_MIN_SEC block above) -- a real duration probe decides, not a
    # client-supplied flag alone, since a wrong duration here means either
    # blocking a valid upload or letting through one that will only fail
    # later, after the user has already waited through transcription.
    lipsync_wanted = lipsync.strip().lower() in ("true", "1", "yes", "on")
    min_sec = LIPSYNC_MIN_SEC if lipsync_wanted else NO_LIPSYNC_MIN_SEC
    max_sec = LIPSYNC_MAX_SEC if lipsync_wanted else NO_LIPSYNC_MAX_SEC
    dur = None
    try:
        dur = ffmpeg_utils.get_media_duration(dest)
    except Exception as _dur_ex:
        print(f"[transcribe] duration probe failed, allowing upload through: {_dur_ex}")
    if dur is not None:
        if dur < min_sec:
            try: dest.unlink()
            except Exception: pass
            _job_started.pop(job_id, None)
            return JSONResponse({"error": f"This clip is only {round(dur, 1)} seconds long. The minimum is {min_sec} seconds."}, status_code=413)
        if dur > max_sec:
            try: dest.unlink()
            except Exception: pass
            _job_started.pop(job_id, None)
            limit_desc = "For a lip-synced clip, the" if lipsync_wanted else "The"
            return JSONResponse({"error": f"This clip is {round(dur)} seconds long. {limit_desc} limit is {max_sec} seconds — please trim it first."}, status_code=413)
    jobs_progress[job_id] = {"status": "processing", "percent": 0,
                             "status_text": "Upload done, starting transcription...",
                             "is_video": ext in VIDEO_EXTS}
    threading.Thread(target=whisper_service.transcribe_worker,
                     args=(job_id, str(dest), HF_TOKEN, speaker_count, lipsync_wanted), daemon=True).start()
    _watch_and_deduct(job_id, uid, "transcribe")
    _record_voice_consent(job_id, uid, request)
    return {"job_id": job_id}

@app.post("/api/attach_media")
async def attach_media(request: Request, file: UploadFile = File(...)):
    """Upload media file for an existing project without transcribing."""
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "login required"}, status_code=401)
    
    job_id = str(uuid.uuid4())
    _job_started[job_id] = _time.time()
    ext = Path(file.filename or "audio.mp4").suffix.lower() or ".mp4"
    dest = UPLOAD_DIR / f"{job_id}{ext}"

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    is_video = ext in VIDEO_EXTS
    if is_video:
        # Same vocal/background separation the normal upload path (transcribe_worker)
        # does for video, so job_background_audio() can find a background track for
        # this job_id later (e.g. when the user merges). Without this, media attached
        # here would merge with vocals only, no background music.
        try:
            extracted_audio = UPLOAD_DIR / f"{job_id}_audio.wav"
            ffmpeg_utils.extract_audio_from_video(str(dest), str(extracted_audio))
            ffmpeg_utils.separate_vocals(str(extracted_audio), str(UPLOAD_DIR / f"{job_id}_separated"))
        except Exception:
            pass  # no background track will be found later; merge falls back to vocals-only

    jobs_progress[job_id] = {
        "status": "ready",
        "percent": 100,
        "status_text": "Media attached",
        "is_video": is_video
    }

    return {
        "job_id": job_id,
        "is_video": is_video,
        "duration": 0
    }


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

# Must be registered BEFORE the generic "/api/progress/{job_id}" route
# just below -- FastAPI/Starlette matches routes in registration order,
# so without this ordering a request to "/api/progress/generate" would
# match {job_id}="generate" on the generic route first and always look up
# the wrong (nonexistent) "generate" key, returning not_found forever.
# (This was the actual cause of the frozen Generate progress bar.)
@app.get("/api/progress/generate")
def generate_progress(job_id: str = ""):
    return jobs_progress.get(f"generate_{job_id}", {"status": "not_found"})

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

@app.post("/api/voice_library/search")
def voice_library_search(req: VoiceLibrarySearchRequest):
    """Search ElevenLabs' full Voice Library (not just your own account) —
    filterable by language, accent, gender, age, and studio/professional
    quality. Pure search: never adds anything to your account, never touches
    your voice add/edit quota."""
    return eleven_service.search_voice_library(
        ELEVENLABS_API_KEY,
        language=req.language or None,
        accent=req.accent or None,
        gender=req.gender or None,
        age=req.age or None,
        category=req.category or None,
        high_quality=req.high_quality or None,
        search=req.search or None,
        voice_type=req.voice_type or None,
        page_size=req.page_size or 6,
        next_page_token=req.page_token or None,
    )

@app.post("/api/voice_library/add")
def voice_library_add(req: VoiceLibraryAddRequest):
    """One-time import of a Voice Library voice into your account. Whether this
    counts against your monthly voice add/edit quota is not documented by
    ElevenLabs — check your subscription page's counter after your first use."""
    return eleven_service.add_shared_voice(ELEVENLABS_API_KEY, req.public_owner_id, req.voice_id, req.new_name)

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
def clone(req: CloneRequest, request: Request):
    if _rate_limited(request, "clone", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": _RATE_LIMIT_MSG}, status_code=429)
    uid = _current_uid(request)
    bal = get_credits(uid) if uid else None
    clone_cost = int(_get_pricing_config().get("cloneCredits", 5))
    if clone_cost <= 0:
        clone_cost = 5
    if bal is not None and bal < clone_cost:
        plural = "s" if clone_cost != 1 else ""
        return JSONResponse({"error": f"Insufficient credits (cloning costs {clone_cost} credit{plural}). Use ➕ Buy."}, status_code=402)
    if uid:
        deduct_credits(uid, clone_cost, "clone", req.job_id)
    return eleven_service.clone_voices(req.job_id, req.segments, ELEVENLABS_API_KEY, req.speakers_to_clone)

@app.post("/api/translate")
def translate(req: TranslateRequest, request: Request):
    if _rate_limited(request, "translate", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": _RATE_LIMIT_MSG}, status_code=429)
    return gemini_service.translate_segments(req.job_id, req.segments, GEMINI_API_KEY)

@app.post("/api/detect_emotions")
def detect_emotions(req: EmotionRequest, request: Request):
    if _rate_limited(request, "detect_emotions", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": _RATE_LIMIT_MSG}, status_code=429)
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
def tashkeel(req: TashkeelRequest, request: Request):
    if _rate_limited(request, "tashkeel", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": _RATE_LIMIT_MSG}, status_code=429)
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
    if _rate_limited(request, "generate", HEAVY_RATE_MAX, HEAVY_RATE_WINDOW_SEC):
        return JSONResponse({"error": _RATE_LIMIT_MSG}, status_code=429)
    uid = _current_uid(request)
    bal = get_credits(uid) if uid else None
    # minReserve (admin-configurable, "💰 Pricing Configuration") is a rough
    # "don't even start" safety floor, not the actual price -- the real
    # per-job cost depends on how much text gets generated and is only known
    # once the job finishes (see _watch_and_deduct below, which reads the
    # real configured rate).
    min_reserve = int(_get_pricing_config().get("minReserve", 20))
    if bal is not None and bal < min_reserve:
        chars_per_credit = int(_get_pricing_config().get("charsPerCredit", 60))
        return JSONResponse({"error": f"Insufficient credits ({bal} left). Generation costs 1 credit per ~{chars_per_credit} characters. Use ➕ Buy."}, status_code=402)
    req.elevenlabs_api_key = ELEVENLABS_API_KEY
    req.gemini_api_key = GEMINI_API_KEY
    # Keyed by job_id (not a single shared "generate" slot) so two jobs
    # running at the same time — two users, or two tabs — never overwrite
    # each other's progress/result, and credits never get charged against
    # the wrong job's character count.
    jobs_progress[f"generate_{req.job_id}"] = {"status": "processing", "percent": 0, "result": None, "error": None}
    threading.Thread(target=eleven_service.generate_worker, args=(req,), daemon=True).start()
    _watch_and_deduct(req.job_id, uid, "generate")
    return {"status": "started"}


@app.post("/api/regenerate_line")
def regenerate_line(req: RegenerateLineRequest, request: Request):
    if _rate_limited(request, "regenerate_line", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": _RATE_LIMIT_MSG}, status_code=429)
    req.elevenlabs_api_key = ELEVENLABS_API_KEY
    return eleven_service.regenerate_line(req)

@app.post("/api/restretch_line")
def restretch_line(req: RegenerateLineRequest):
    # Pure editing action for the Step 5.5 Time Stretch dropdown: re-warps the
    # line's already-generated audio to its current setting, no TTS call and
    # no ElevenLabs key needed.
    return eleven_service.restretch_line(req)

@app.post("/api/remix_audio")
def remix_audio(req: RemixRequest):
    return eleven_service.remix_with_offsets(req)

@app.post("/api/merge_video")
def merge_video(req: MergeRequest, request: Request):
    if _rate_limited(request, "merge_video", HEAVY_RATE_MAX, HEAVY_RATE_WINDOW_SEC):
        return JSONResponse({"error": _RATE_LIMIT_MSG}, status_code=429)
    uid = _current_uid(request)
    bal = get_credits(uid) if uid else None
    merge_cost = int(_get_pricing_config().get("mergeCredits", 1))
    if merge_cost <= 0:
        merge_cost = 1
    if bal is not None and bal < merge_cost:
        plural = "s" if merge_cost != 1 else ""
        return JSONResponse({"error": f"Insufficient credits (merge costs {merge_cost} credit{plural}). Use ➕ Buy."}, status_code=402)
    if uid:
        deduct_credits(uid, merge_cost, "merge", req.job_id)

    video = find_job_video(req.job_id)
    # Job-scoped filename -- see the comment on CLEANUP_RETENTION_HOURS below
    # for why this used to be a single shared filename for every job on the
    # server (a real bug: two jobs finishing near each other would silently
    # overwrite each other's file) and why it now includes the job_id.
    dub = OUTPUT_DIR / f"{req.job_id}_final_dubbed.mp3"
    if video is None or not dub.exists():
        return {"error": "Missing video or dubbed audio. Run Generate first."}

    bg = job_background_audio(req.job_id)
    final = OUTPUT_DIR / f"{req.job_id}_final_dubbed_video.mp4"
    
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
def lipsync(req: LipSyncRequest, request: Request):
    if not LIPSYNC_ENABLED:
        # Backend gate, independent of the frontend hiding Step 7 -- so a
        # stale/cached page, or someone calling this endpoint directly,
        # still can't start (or get charged for) a lip-sync job while it's
        # disabled. See the LIPSYNC_ENABLED comment in config.py.
        return JSONResponse({"error": "Lip-sync is temporarily unavailable while we evaluate providers with better quality."}, status_code=503)
    if _rate_limited(request, "lipsync", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": _RATE_LIMIT_MSG}, status_code=429)
    uid = _current_uid(request)

    video_path = find_job_video(req.job_id)
    if video_path is None:
        return JSONResponse({"error": "Original video not found."}, status_code=404)

    # Lip-sync (VEED Lip Sync 2.0 via fal.ai) is billed per second of video,
    # not a flat fee, so the charge has to be computed from the real video
    # duration -- unlike the maxVideoMin check in /api/transcribe, a failed
    # probe here fails CLOSED (blocks the request) rather than falling back
    # to some default duration, since guessing wrong here means charging
    # the wrong amount for a real, meaningful cost.
    try:
        dur = ffmpeg_utils.get_media_duration(video_path)
    except Exception as _dur_ex:
        print(f"[lipsync] duration probe failed: {_dur_ex}")
        dur = None
    if not dur or dur <= 0:
        return JSONResponse({"error": "Could not determine this video's length. Please try again."}, status_code=500)
    # Re-check against Wan 3.0's real limits here too, not just at upload
    # time (LIPSYNC_MIN_SEC/LIPSYNC_MAX_SEC, defined above /api/transcribe)
    # -- duration is ground truth, and checking it again right before the
    # paid call is what actually prevents a charge for a job that can't
    # succeed, regardless of what was chosen back at Step 1.
    if dur < LIPSYNC_MIN_SEC or dur > LIPSYNC_MAX_SEC:
        return JSONResponse({"error": f"Lip-sync only works on clips between {LIPSYNC_MIN_SEC} and {LIPSYNC_MAX_SEC} seconds. This video is {round(dur, 1)} seconds."}, status_code=413)

    per_sec = float(_get_pricing_config().get("lipsyncCreditsPerSec", 10))
    lipsync_cost = max(1, round(dur * per_sec))

    bal = get_credits(uid) if uid else None
    if bal is not None and bal < lipsync_cost:
        return JSONResponse({"error": f"Insufficient credits ({bal} left). Lip-sync for this {round(dur)}s video costs {lipsync_cost} credits. Use ➕ Buy."}, status_code=402)
    if uid:
        deduct_credits(uid, lipsync_cost, "lipsync", req.job_id)

    jobs_progress[f"lipsync_{req.job_id}"] = {"status": "processing", "percent": 5,
                                              "message": "Preparing...", "error": None,
                                              "result": None, "generation_id": None}
    threading.Thread(target=lipsync_service.lipsync_worker,
                     args=(req.job_id, req.provider, req.model, ELEVENLABS_API_KEY, req.sync_key, FAL_API_KEY),
                     daemon=True).start()
    return {"status": "started", "credits_charged": lipsync_cost if uid else 0}

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
    # Basic path-traversal guard. This was harmless before (files only ever
    # lived a few hours and had one shared name), but final outputs now
    # persist for up to CLEANUP_FINAL_OUTPUT_DAYS days, so it's worth closing
    # off "../" style filenames before they reach the filesystem.
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "not found"}, status_code=404)
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
    result = eleven_service.cleanup_cloned_voices(ELEVENLABS_API_KEY, payload.get("keep", []))
    # Also remove this job's downloadable voice sample file(s), if any — same
    # "session end" moment as the ElevenLabs-side voice cleanup above.
    job_id = payload.get("job_id") or ""
    if job_id:
        try:
            safe_job = "".join(c for c in job_id if c.isalnum() or c in "_-")
            for p in OUTPUT_DIR.glob(f"voice_sample_{safe_job}_*.wav"):
                try:
                    p.unlink()
                except Exception:
                    pass
        except Exception:
            pass
    return result

@app.get("/api/download_voice_sample/{job_id}/{speaker}")
def download_voice_sample(job_id: str, speaker: str):
    """Serves the isolated voice sample used to create a speaker's clone —
    NOT the ElevenLabs voice model itself (ElevenLabs does not allow exporting
    cloned voices at all). This is the reference recording assembled from the
    user's own video before upload, kept only until this job's cleanup_voices
    call (new project / reset / explicit cleanup)."""
    safe_speaker = "".join(c for c in speaker if c.isalnum()).strip() or "speaker"
    safe_job = "".join(c for c in job_id if c.isalnum() or c in "_-")
    p = OUTPUT_DIR / f"voice_sample_{safe_job}_{safe_speaker}.wav"
    if not p.exists():
        return JSONResponse({"error": "This voice sample is no longer available."}, status_code=404)
    return FileResponse(p, media_type="audio/wav", filename=f"{speaker}_voice_sample.wav")

@app.post("/api/upload_custom_voice")
async def upload_custom_voice(request: Request, file: UploadFile = File(...), speaker: str = Form("Speaker 1"), job_id: str = Form("")):
    uid = _current_uid(request)
    if not uid: return JSONResponse({"error": "login required"}, status_code=401)
    nm = (file.filename or "").lower()
    if not nm.endswith((".mp3", ".wav")): return {"error": "Only MP3 or WAV files are allowed."}
    data = await file.read()
    if len(data) > 10 * 1024 * 1024: return {"error": "File too large (max 10 MB / 20 seconds)."}
    # Uploading a custom voice hits the same ElevenLabs POST /v1/voices/add
    # endpoint -- and the same paid voice-creation quota -- as "Clone Selected
    # Voices" in /api/clone above, so charge it the same admin-configurable
    # price (cloneCredits). Checked up front so a low balance is rejected
    # before spending the ElevenLabs quota; actually deducted only after the
    # voice is created, so a rejected/too-long clip never gets charged.
    clone_cost = int(_get_pricing_config().get("cloneCredits", 5))
    if clone_cost <= 0:
        clone_cost = 5
    bal = get_credits(uid)
    if bal is not None and bal < clone_cost:
        plural = "s" if clone_cost != 1 else ""
        return JSONResponse({"error": f"Insufficient credits (creating a custom voice costs {clone_cost} credit{plural}). Use ➕ Buy."}, status_code=402)
    import uuid as _u
    tmp = OUTPUT_DIR / f"custom_upload_{_u.uuid4().hex}.bin"
    tmp.write_bytes(data)
    try: res = eleven_service.add_custom_voice(job_id or "custom", speaker, tmp, ELEVENLABS_API_KEY)
    except Exception as e: res = f"ERROR: {e}"
    finally:
        try: tmp.unlink()
        except Exception: pass
    if isinstance(res, str) and res.startswith("ERROR"): return {"error": res}
    deduct_credits(uid, clone_cost, "custom_voice", job_id or "")
    return {"status": "success", "voice_id": res}

def account_summary(request: Request):
    uid = _current_uid(request)
    if not uid: return JSONResponse({"error": "login required"}, status_code=401)
    credits = get_credits(uid)
    if credits is None:
        credits = int(_get_pricing_config().get("freeCredits", 100))
    return {"credits": credits, "purchases": [], "spends": []}

@app.get("/account")
def account_page():
    return FileResponse(BASE_DIR / "account.html")


# ===== USAGE RECORDING: log every credit deduction to credit_spends =====
def _record_spend(uid, action, credits, job_id=None, generated_seconds=None):
    try:
        body = {"uid": uid, "action": action, "job_id": job_id, "credits": credits}
        # generated_seconds: duration (seconds) of the final dubbed audio this
        # spend represents — only set for "generate" jobs. Requires the
        # credit_spends table to have a generated_seconds numeric column
        # (see the ALTER TABLE note this was introduced with).
        if generated_seconds is not None:
            body["generated_seconds"] = round(float(generated_seconds), 2)
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/credit_spends",
            data=json.dumps(body).encode("utf-8"),
            headers={"apikey": SUPABASE_SERVICE_KEY, "Content-Type": "application/json", "Prefer": "return=minimal"},
            method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
try:
    _od = deduct_credits
    def deduct_credits(*a, **k):
        uid = a[0] if len(a) > 0 else k.get("uid")
        amt = a[1] if len(a) > 1 else k.get("amount", k.get("credits"))
        act = a[2] if len(a) > 2 else k.get("action", "deduction")
        jid = a[3] if len(a) > 3 else k.get("job_id")
        gsec = a[4] if len(a) > 4 else k.get("generated_seconds")
        # _od (the original deduct_credits) only ever took (uid, amount) — call it
        # with exactly that, never with the extra action/job_id tracking args,
        # or it raises "takes 2 positional arguments but 4 were given" and the
        # real credit deduction never happens.
        r = _od(uid, amt)
        try:
            _record_spend(uid, act or "deduction", amt, jid, gsec)
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


# ===== "Your Finished Files" on the Account page =====
# Ownership of a job_id is decided by whether it shows up in this uid's own
# credit_spends rows (already written for every generate/merge/clone call --
# see _record_spend above), not a separate jobs table.
_MY_JOB_FILE_SUFFIXES = {
    "audio": "_final_dubbed.mp3",
    "video": "_final_dubbed_video.mp4",
    "lipsync": "_final_lipsync.mp4",
}


def _job_belongs_to_uid(uid: str, job_id: str) -> bool:
    if not uid or not job_id or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    import urllib.request as _ur
    url = f"{SUPABASE_URL}/rest/v1/credit_spends?uid=eq.{uid}&job_id=eq.{job_id}&select=job_id&limit=1"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            return bool(json.load(r))
    except Exception:
        return False


@app.get("/api/my_jobs")
def my_jobs(request: Request):
    """Every job of this user's that still has a finished output on disk --
    backs the Account page's file list. Reuses credit_spends for ownership
    instead of a new table; a job with no final file left (never produced
    one, or past the 30-day window) is simply left out."""
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "login required"}, status_code=401)
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"jobs": []}
    import urllib.request as _ur
    url = f"{SUPABASE_URL}/rest/v1/credit_spends?uid=eq.{uid}&select=job_id,created_at&order=created_at.desc&limit=1000"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            rows = json.load(r)
    except Exception as e:
        print("[my_jobs] fetch error:", e)
        return {"jobs": []}

    latest_seen = {}
    for row in rows:
        jid = row.get("job_id")
        if not jid or "/" in jid or "\\" in jid or ".." in jid:
            continue
        ts = row.get("created_at") or ""
        if jid not in latest_seen or ts > latest_seen[jid]:
            latest_seen[jid] = ts

    jobs = []
    for jid, created_at in latest_seen.items():
        audio = OUTPUT_DIR / f"{jid}_final_dubbed.mp3"
        video = OUTPUT_DIR / f"{jid}_final_dubbed_video.mp4"
        lip = OUTPUT_DIR / f"{jid}_final_lipsync.mp4"
        existing = [p for p in (audio, video, lip) if p.exists()]
        if not existing:
            continue
        newest_mtime = max(p.stat().st_mtime for p in existing)
        jobs.append({
            "job_id": jid,
            "created_at": created_at,
            "has_audio": audio.exists(),
            "has_video": video.exists(),
            "has_lipsync": lip.exists(),
            "expires_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(newest_mtime + CLEANUP_FINAL_OUTPUT_DAYS * 86400)),
        })
    jobs.sort(key=lambda j: j["created_at"] or "", reverse=True)
    return {"jobs": jobs}


@app.get("/api/my_jobs/{job_id}/{kind}")
def my_job_file(job_id: str, kind: str, request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "login required"}, status_code=401)
    suffix = _MY_JOB_FILE_SUFFIXES.get(kind)
    if not suffix or "/" in job_id or "\\" in job_id or ".." in job_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _job_belongs_to_uid(uid, job_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    p = OUTPUT_DIR / f"{job_id}{suffix}"
    if not p.exists():
        return JSONResponse({"error": "This file has expired or was already deleted."}, status_code=404)
    media_type = "audio/mpeg" if kind == "audio" else "video/mp4"
    return FileResponse(p, media_type=media_type, filename=p.name)


@app.delete("/api/my_jobs/{job_id}")
def delete_my_job(job_id: str, request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "login required"}, status_code=401)
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _job_belongs_to_uid(uid, job_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    removed = 0
    for suffix in _MY_JOB_FILE_SUFFIXES.values():
        p = OUTPUT_DIR / f"{job_id}{suffix}"
        try:
            if p.exists():
                p.unlink()
                removed += 1
        except Exception:
            pass
    return {"status": "success", "removed": removed}


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


# --- PUBLIC PAGES ROUTING (Auto-injected fix) ---
@app.get("/help")
@app.get("/help.html")
def public_help_page():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "help.html"))

@app.get("/privacy")
@app.get("/privacy.html")
def public_privacy_page():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "privacy.html"))

@app.get("/terms")
@app.get("/terms.html")
def public_terms_page():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "terms.html"))
    
    # ============================================================
# ADMIN ROUTES — protected by APP_PASSWORD env var
# ============================================================
import hmac
import time
import secrets

# In-memory admin session tokens (valid for 4 hours)
_ADMIN_TOKENS = {}
ADMIN_TOKEN_TTL = 4 * 3600  # 4 hours

def _admin_check(request):
    """Returns True if request has a valid admin token."""
    tok = request.headers.get("X-Admin-Token", "")
    if not tok or tok not in _ADMIN_TOKENS:
        return False
    # Check expiry
    if time.time() - _ADMIN_TOKENS[tok] > ADMIN_TOKEN_TTL:
        del _ADMIN_TOKENS[tok]
        return False
    return True

def _get_pricing_config():
    """Returns pricing config from DB, with defaults if not set."""
    # Uses Supabase service key to read from a `pricing_config` table
    # If table doesn't exist or is empty, returns defaults
    defaults = {
        "freeCredits": 150,
        "minReserve": 150,
        "maxVideoMin": 60,
        # Real per-step charges -- these are the ones actually read by
        # _watch_and_deduct() and /api/merge_video below.
        "transcribeCredits": 3,
        "mergeCredits": 1,
        "charsPerCredit": 60,
        "cloneCredits": 5,
        # Lip-sync (Step 7, VEED Lip Sync 2.0 via fal.ai) is billed per
        # second of the source video, not a flat fee -- fal.ai charges this
        # app $0.07/sec, so the default of 10 credits/sec (= $0.10/sec at
        # 100 credits = $1) leaves a real margin instead of losing money on
        # every lip-sync request. See /api/lipsync for how it's charged.
        "lipsyncCreditsPerSec": 10,
        # Google Analytics 4 Measurement ID (e.g. "G-XXXXXXXXXX"), set from
        # the admin panel. Empty string = analytics off. The landing() route
        # below injects Google's gtag.js snippet server-side into the page
        # only when this is set, so nothing is tracked until admin turns it on.
        "gaMeasurementId": "",
        "packs": DEFAULT_PACKS
    }
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return defaults
    try:
        import urllib.request as _ur
        url = f"{SUPABASE_URL}/rest/v1/pricing_config?id=eq.singleton&select=*"
        hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=5) as r:
            rows = json.load(r)
        if rows and isinstance(rows, list) and len(rows) > 0:
            row = rows[0]
            return {
                "freeCredits": row.get("free_credits", defaults["freeCredits"]),
                "minReserve": row.get("min_reserve", defaults["minReserve"]),
                "maxVideoMin": row.get("max_video_min", defaults["maxVideoMin"]),
                "transcribeCredits": row.get("transcribe_credits", defaults["transcribeCredits"]),
                "mergeCredits": row.get("merge_credits", defaults["mergeCredits"]),
                "charsPerCredit": row.get("chars_per_credit", defaults["charsPerCredit"]),
                "cloneCredits": row.get("clone_credits", defaults["cloneCredits"]),
                "lipsyncCreditsPerSec": row.get("lipsync_credits_per_sec", defaults["lipsyncCreditsPerSec"]),
                "gaMeasurementId": row.get("ga_measurement_id", defaults["gaMeasurementId"]),
                "packs": row.get("packs", defaults["packs"])
            }
    except Exception as ex:
        print(f"[admin] pricing_config load error: {ex}")
    return defaults

def _save_pricing_config(config):
    """Saves pricing config to DB (upsert — creates the singleton row if it
    doesn't exist yet, updates it if it does). Previously this used PATCH,
    which only updates an EXISTING row matching id=eq.singleton — if that
    row had never been created, PATCH silently matched zero rows and
    returned success without writing anything, so admin edits looked saved
    but never actually persisted (and public pages kept showing defaults)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False  # not in DB, but UI already shows current values
    try:
        import re as _re
        import urllib.request as _ur
        # Accept either the bare Measurement ID ("G-NBWB2VYYE4") or the
        # whole <script> snippet Google's setup page tells you to paste --
        # pull just the "G-..." ID out of whatever was typed in, so pasting
        # Google's full install snippet into this one field still works.
        _raw_ga = (config.get("gaMeasurementId") or "").strip()
        _ga_match = _re.search(r"G-[A-Za-z0-9]{4,20}", _raw_ga)
        _clean_ga = _ga_match.group(0) if _ga_match else _raw_ga
        body = json.dumps({
            "id": "singleton",
            "free_credits": config.get("freeCredits", 150),
            "min_reserve": config.get("minReserve", 150),
            "max_video_min": config.get("maxVideoMin", 60),
            "transcribe_credits": config.get("transcribeCredits", 3),
            "merge_credits": config.get("mergeCredits", 1),
            "chars_per_credit": config.get("charsPerCredit", 60),
            "clone_credits": config.get("cloneCredits", 5),
            "lipsync_credits_per_sec": config.get("lipsyncCreditsPerSec", 10),
            "ga_measurement_id": _clean_ga,
            "packs": config.get("packs", []),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }).encode("utf-8")
        url = f"{SUPABASE_URL}/rest/v1/pricing_config"
        hdrs = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal"
        }
        req = _ur.Request(url, data=body, headers=hdrs, method="POST")
        with _ur.urlopen(req, timeout=10) as r:
            pass
        return True
    except Exception as ex:
        print(f"[admin] pricing_config save error: {_http_error_detail(ex)}")
        return False

class AdminLoginRequest(BaseModel):
    code: str = ""
    password: str = ""

@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest, request: Request):
    if _login_rate_limited(request):
        return JSONResponse({"error": "Too many attempts. Try again in a few minutes."}, status_code=429)
    code = req.code or req.password
    if not code or not ADMIN_PASSWORD:
        return JSONResponse({"error": "admin access disabled"}, status_code=403)
    if not hmac.compare_digest(str(code), str(ADMIN_PASSWORD)):
        _record_login_fail(request)
        return JSONResponse({"error": "invalid code"}, status_code=401)
    token = secrets.token_urlsafe(32)
    _ADMIN_TOKENS[token] = _time.time()
    return {"token": token}
def _get_generated_minutes():
    """Aggregate generated-audio duration from credit_spends (action='generate'
    rows carry a generated_seconds field — see _record_spend). Returns
    (per_user_seconds: {uid: total_seconds_all_time}, this_month_seconds: float).
    Requires credit_spends to have a generated_seconds numeric column."""
    import urllib.request as _ur
    per_user = {}
    month_total = 0.0
    now = time.gmtime()
    month_start = f"{now.tm_year:04d}-{now.tm_mon:02d}-01T00:00:00"
    url = f"{SUPABASE_URL}/rest/v1/credit_spends?action=eq.generate&select=uid,generated_seconds,created_at&limit=5000&order=created_at.desc"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            rows = json.load(r) or []
    except Exception:
        rows = []
    for row in rows:
        secs = float(row.get("generated_seconds") or 0)
        uid = row.get("uid") or ""
        if uid:
            per_user[uid] = per_user.get(uid, 0.0) + secs
        if (row.get("created_at") or "") >= month_start:
            month_total += secs
    return per_user, month_total

@app.get("/api/admin/overview")
def admin_overview(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    # Aggregate metrics
    import urllib.request as _ur
    def _fetch_count(table):
        url = f"{SUPABASE_URL}/rest/v1/{table}?select=*"
        hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Prefer": "count=exact"}
        try:
            req = _ur.Request(url, headers=hdrs, method="HEAD")
            with _ur.urlopen(req, timeout=5) as r:
                return int(r.headers.get("Content-Range", "*/0").split("/")[-1])
        except Exception:
            return 0
    def _fetch_rows(table, limit=20):
        url = f"{SUPABASE_URL}/rest/v1/{table}?order=created_at.desc&limit={limit}&select=*"
        hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        try:
            req = _ur.Request(url, headers=hdrs)
            with _ur.urlopen(req, timeout=5) as r:
                return json.load(r) or []
        except Exception:
            return []
    profiles = _fetch_rows("profiles", 1000)
    total_users = len(profiles)
    credits_outstanding = sum(p.get("credits", 0) for p in profiles)
    paying_users = sum(1 for p in profiles if not p.get("is_guest", True) and p.get("credits", 0) > 150)
    orders = _fetch_rows("credit_orders", 1000)
    revenue = sum(o.get("credits", 0) for o in orders) / 100.0  # 100 cr = $1
    recent_jobs = _fetch_rows("credit_spends", 20)
    _per_user_secs, _month_secs = _get_generated_minutes()
    return {
        "total_users": total_users,
        "paying_users": paying_users,
        "credits_outstanding": credits_outstanding,
        "revenue_usd": revenue,
        "minutes_generated_this_month": round(_month_secs / 60.0, 1),
        "recent_jobs": [{"created_at": j.get("created_at",""), "uid": j.get("uid",""), "user_name": "",
                         "action": j.get("action",""), "credits": j.get("credits",0), "status": "done",
                         "generated_seconds": j.get("generated_seconds")} for j in recent_jobs]
    }

@app.get("/api/admin/users")
def admin_users(request: Request, q: str = ""):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import urllib.request as _ur
    url = f"{SUPABASE_URL}/rest/v1/profiles?order=created_at.desc&limit=500&select=*"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            users = json.load(r) or []
    except Exception as ex:
        return JSONResponse({"error": str(ex)}, status_code=500)
    # Filter by search query
    if q:
        ql = q.lower()
        users = [u for u in users if ql in (str(u.get("display_name","")) + str(u.get("email","")) + str(u.get("id",""))).lower()]
    per_user_secs, _ = _get_generated_minutes()
    for u in users:
        u["minutes_generated"] = round(per_user_secs.get(u.get("id", ""), 0.0) / 60.0, 1)
    return {"users": users}

@app.post("/api/admin/adjust_credits")
async def admin_adjust_credits(request: Request):
    """Manually grant or deduct credits. Logged to audit."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    uid = body.get("uid", "")
    delta = int(body.get("delta", 0))
    reason = body.get("reason", "")
    if not uid or delta == 0:
        return JSONResponse({"error": "uid and non-zero delta required"}, status_code=400)
    if abs(delta) > 10000:
        return JSONResponse({"error": "delta too large (max 10000)"}, status_code=400)
    # Update credits
    current = get_credits(uid) or 0
    new_credits = max(0, current + delta)
    if not set_credits(uid, new_credits):
        return JSONResponse({"error": "failed to update credits"}, status_code=500)
    # Log to credit_spends
    spend_err = _log_spend(uid, "admin_adjustment", delta, job_id=None, reason=reason)
    # Log to audit table
    audit_err = _log_audit(uid, delta, reason)
    
    resp = {"ok": True, "new_credits": new_credits}
    if spend_err is not True: resp["spend_warning"] = str(spend_err)
    if audit_err is not True: resp["audit_warning"] = str(audit_err)
    return resp

def _http_error_detail(ex):
    """str(HTTPError) only ever gives 'HTTP Error 400: Bad Request' -- it drops
    the actual response body, which is where Supabase/PostgREST puts the real
    reason (e.g. which column or constraint). Read that body when present so
    failures are diagnosable from the toast instead of guessed at blindly."""
    detail = str(ex)
    try:
        if hasattr(ex, "read"):
            body = ex.read().decode("utf-8", errors="ignore")
            if body:
                detail = f"{detail} — {body}"
    except Exception:
        pass
    return detail

def _log_spend(uid, action, credits, job_id=None, reason=None):
    """Helper: insert into credit_spends. Returns True on success, or the
    error string on failure (previously this always returned None either
    way, which made the admin UI's 'Spend log error: None' warning show up
    on every adjustment regardless of whether it actually failed).

    Note: credit_spends has no 'reason' column (that lives in credit_audit,
    which is where manual admin adjustments are meant to record it) -- the
    'reason' param here is accepted but intentionally NOT written to
    credit_spends, since sending an unknown column made Supabase reject the
    whole insert with 400 Bad Request. job_id is only included when set, so
    a NULL-vs-omitted mismatch can't trip up the column's constraints."""
    import urllib.request as _ur
    row = {
        "uid": uid, "action": action, "credits": credits,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    if job_id:
        row["job_id"] = job_id
    body = json.dumps(row).encode("utf-8")
    url = f"{SUPABASE_URL}/rest/v1/credit_spends"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Content-Type": "application/json", "Prefer": "return=minimal"}
    try:
        req = _ur.Request(url, data=body, headers=hdrs, method="POST")
        with _ur.urlopen(req, timeout=5) as r: pass
        return True
    except Exception as ex:
        detail = _http_error_detail(ex)
        print(f"[admin] log_spend error: {detail}")
        return detail

def _log_audit(target_uid, delta, reason):
    """Helper: insert into credit_audit. Returns True on success, or the
    error string on failure (same missing-return bug as _log_spend above —
    this previously always returned None, masking real insert failures such
    as the credit_audit table not existing in Supabase)."""
    import urllib.request as _ur
    body = json.dumps({
        "admin": "admin", "target_uid": target_uid, "delta": delta,
        "reason": reason,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }).encode("utf-8")
    url = f"{SUPABASE_URL}/rest/v1/credit_audit"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Content-Type": "application/json", "Prefer": "return=minimal"}
    try:
        req = _ur.Request(url, data=body, headers=hdrs, method="POST")
        with _ur.urlopen(req, timeout=5) as r: pass
        return True
    except Exception as ex:
        detail = _http_error_detail(ex)
        print(f"[admin] log_audit error: {detail}")
        return detail

@app.get("/api/admin/pricing")
def admin_get_pricing(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return _get_pricing_config()

@app.post("/api/admin/pricing")
async def admin_save_pricing(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ok = _save_pricing_config(body)
    return {"ok": ok}

@app.get("/api/admin/audit")
def admin_audit(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import urllib.request as _ur
    url = f"{SUPABASE_URL}/rest/v1/credit_audit?order=created_at.desc&limit=100&select=*"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            rows = json.load(r) or []
    except Exception:
        rows = []
    return {"audit": rows}

@app.get("/api/admin/health")
def admin_health(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import urllib.request as _ur
    def _check(url, hdrs):
        try:
            req = _ur.Request(url, headers=hdrs)
            with _ur.urlopen(req, timeout=5) as r:
                return "ok" if r.status == 200 else f"http_{r.status}"
        except Exception:
            return "fail"
    supabase = _check(f"{SUPABASE_URL}/rest/v1/profiles?limit=1",
                       {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
    eleven = "ok" if ELEVENLABS_API_KEY else "fail"
    gemini = "ok" if GEMINI_API_KEY else "fail"
    return {"supabase": supabase, "elevenlabs": eleven, "gemini": gemini}

@app.get("/api/admin/storage")
def admin_storage(request: Request):
    """Real disk usage of the Railway volume mounted at DATA_DIR (/data) --
    not an API call to Railway, just shutil.disk_usage on the mount the
    container already sees. Also reports how much of that is this app's own
    30-day-retention final outputs, so it's clear how much of any growth is
    this feature specifically vs. everything else on the volume."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import shutil
    try:
        total, used, free = shutil.disk_usage(str(DATA_DIR))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    final_count = 0
    final_bytes = 0
    try:
        for p in OUTPUT_DIR.glob("*"):
            if p.is_file() and p.name.endswith(_FINAL_OUTPUT_SUFFIXES):
                final_count += 1
                final_bytes += p.stat().st_size
    except Exception:
        pass
    gb = 1024 ** 3
    return {
        "total_gb": round(total / gb, 2),
        "used_gb": round(used / gb, 2),
        "free_gb": round(free / gb, 2),
        "percent_used": round(used / total * 100, 1) if total else 0,
        "final_output_count": final_count,
        "final_output_gb": round(final_bytes / gb, 2),
    }

@app.get("/api/admin/railway_memory")
def admin_railway_memory(request: Request):
    """Last-polled Railway memory reading for this service (see
    railway_monitor.py) -- cached in-process, not a live Railway call, so
    this is instant and never burns Railway's API rate limit. Returns
    {"ok": false, "error": "RAILWAY_API_TOKEN not set"} until Ali adds
    that env var on Railway; the admin page shows that message plainly."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    data = railway_monitor.get_cached()
    data["alert_percent"] = railway_monitor.ALERT_PERCENT
    return data

def _read_cgroup_memory():
    """Reads THIS CONTAINER's own memory accounting from the cgroup
    filesystem. Unlike /proc/meminfo -- which was tried first in an earlier
    version of this endpoint and turned out to report the whole physical
    HOST's memory (Railway's shared underlying machine, not just this
    service's container), since /proc/meminfo isn't namespaced by Docker --
    the cgroup files under /sys/fs/cgroup ARE scoped correctly to just this
    container by the kernel itself, and are almost certainly the exact
    source Railway's own graph reads from (Railway runs on standard
    container cgroups same as any other Docker host). Tries cgroup v2 (the
    modern unified hierarchy) first, falls back to v1."""
    result = {"version": None, "used_mb": None, "limit_mb": None,
              "cache_mb": None, "anon_mb": None, "error": None}

    def _mb(byte_val):
        return round(byte_val / (1024 * 1024), 1)

    try:  # cgroup v2
        with open("/sys/fs/cgroup/memory.current") as f:
            used = int(f.read().strip())
        result["version"] = "v2"
        result["used_mb"] = _mb(used)
        try:
            with open("/sys/fs/cgroup/memory.max") as f:
                raw = f.read().strip()
            result["limit_mb"] = None if raw == "max" else _mb(int(raw))
        except Exception:
            pass
        try:
            with open("/sys/fs/cgroup/memory.stat") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) != 2:
                        continue
                    key, val = parts[0], int(parts[1])
                    if key == "file":       # page cache charged to this container
                        result["cache_mb"] = _mb(val)
                    elif key == "anon":     # real heap/stack memory this container holds
                        result["anon_mb"] = _mb(val)
        except Exception:
            pass
        return result
    except Exception:
        pass

    try:  # cgroup v1 fallback
        with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as f:
            used = int(f.read().strip())
        result["version"] = "v1"
        result["used_mb"] = _mb(used)
        try:
            with open("/sys/fs/cgroup/memory/memory.limit_in_bytes") as f:
                raw = int(f.read().strip())
            # v1 reports a near-int64-max sentinel when there's no real limit
            result["limit_mb"] = None if raw > 10 ** 15 else _mb(raw)
        except Exception:
            pass
        try:
            with open("/sys/fs/cgroup/memory/memory.stat") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) != 2:
                        continue
                    key, val = parts[0], int(parts[1])
                    if key == "cache":
                        result["cache_mb"] = _mb(val)
                    elif key == "rss":
                        result["anon_mb"] = _mb(val)
        except Exception:
            pass
        return result
    except Exception as e:
        result["error"] = f"cgroup memory files not readable: {e}"
        return result


@app.get("/api/admin/mem_diag")
def admin_mem_diag(request: Request):
    """TEMPORARY diagnostic (Sept 2026 memory-plateau investigation, same
    status as _log_mem in whisper_service.py -- remove once the plateau is
    understood): a one-shot, read-only breakdown of the CONTAINER's actual
    memory, not just this one process's. Answers the specific question of
    whether a sustained multi-GB reading on Railway's own memory graph is
    real application memory or reclaimable Linux page cache -- Railway
    doesn't document what its metric includes, and a Railway support reply
    claiming it's "just reporting what your app uses" hasn't been verified
    against this app's own container.

    The authoritative numbers come from _read_cgroup_memory() (see above),
    which is correctly scoped to just this container. /proc/meminfo is also
    included below, but only as host-wide background context -- it reports
    Railway's whole shared physical machine, not this container, so don't
    read its numbers as "this app's memory". Also walks /proc/<pid>/status
    for every process visible in this container (PID namespaces ARE
    isolated per-container, unlike /proc/meminfo) as a substitute for
    `ps aux`, since the slim python:3.12-slim base image doesn't ship a
    `ps` binary. No side effects, nothing here can make the plateau worse."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    def _kb_to_mb(kb):
        return round(kb / 1024, 1)

    cgroup = _read_cgroup_memory()

    host_meminfo = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                if key in ("MemTotal", "MemAvailable"):
                    value_kb = int(parts[1].strip().split()[0])
                    host_meminfo[key + "_mb"] = _kb_to_mb(value_kb)
    except Exception:
        pass

    processes = []
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/status") as f:
                    name = None
                    rss_kb = None
                    for line in f:
                        if line.startswith("Name:"):
                            name = line.split(":", 1)[1].strip()
                        elif line.startswith("VmRSS:"):
                            rss_kb = int(line.split()[1])
                    if name and rss_kb:
                        processes.append({"pid": int(entry), "name": name, "rss_mb": _kb_to_mb(rss_kb)})
            except Exception:
                continue
    except Exception:
        pass
    processes.sort(key=lambda p: p["rss_mb"], reverse=True)

    # On-disk size of the Whisper/pyannote model-cache directories -- not the
    # same thing as how much of them is currently resident in RAM (that would
    # need a mincore() walk per file, which isn't worth the risk of a raw
    # ctypes/mmap bug on a live server for a number this diagnostic), but a
    # close proxy: these files get read start-to-finish on every model load,
    # so their on-disk size is close to the ceiling of what could be cached.
    # If this total is in the same ballpark as cgroup's "cache_mb" above
    # (accounting for job files too), that confirms the model files -- not a
    # leak -- are the bulk of the plateau.
    model_cache_dirs = []
    try:
        for cache_dir in whisper_service._model_cache_dirs():
            total_bytes = 0
            file_count = 0
            for root, _dirs, files in os.walk(cache_dir):
                for name in files:
                    try:
                        total_bytes += os.path.getsize(os.path.join(root, name))
                        file_count += 1
                    except Exception:
                        pass
            model_cache_dirs.append({
                "path": str(cache_dir),
                "size_mb": _kb_to_mb(total_bytes / 1024),
                "file_count": file_count,
            })
    except Exception:
        pass

    idle_minutes = round((_time.time() - app_state.last_job_activity) / 60, 1)

    return {
        "cgroup": cgroup, "host_meminfo": host_meminfo, "processes": processes,
        "model_cache_dirs": model_cache_dirs,
        "idle_minutes": idle_minutes,
        "model_cache_idle_threshold_minutes": MODEL_CACHE_IDLE_MINUTES,
    }

@app.post("/api/admin/purge_old_jobs")
def admin_purge_jobs(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import os, shutil, time
    purge_before = time.time() - 30 * 86400  # 30 days ago
    purged = 0
    try:
        for d in os.listdir(OUTPUT_DIR):
            full = os.path.join(OUTPUT_DIR, d)
            if os.path.isdir(full) and os.path.getmtime(full) < purge_before:
                shutil.rmtree(full, ignore_errors=True)
                purged += 1
    except Exception:
        pass
    return {"purged": purged}

@app.post("/api/admin/clear_sessions")
def admin_clear_sessions(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cleared = 0
    cutoff = time.time() - 7 * 86400
    for tok in list(_session_users.keys()):
        # _session_users values are UIDs, not timestamps — we'd need to track timestamps
        # For now, clear all sessions (admin's call)
        del _session_users[tok]
        cleared += 1
    return {"cleared": cleared}

# Serve the admin HTML page
@app.get("/admin")
def admin_page(request: Request):
    if not APP_PASSWORD:
        return JSONResponse({"error": "admin access disabled"}, status_code=403)
    from pathlib import Path
    p = Path(__file__).parent / "admin.html"
    if not p.exists():
        return JSONResponse({"error": "admin.html not found"}, status_code=404)
    # Stamp the current APP_VERSION (config.py) into the page each time it's
    # served, so the admin dashboard always shows what's actually deployed
    # without admin.html itself needing to change when the version bumps.
    # (Previously also showed Railway's raw deployment id as a self-updating
    # cross-check -- dropped per Ali: a hex id doesn't mean anything to a
    # human, so it didn't actually help confirm what shipped. Bump
    # APP_VERSION by hand instead: patch (third number) for a fix, minor
    # (middle number) when a feature is added.)
    html = p.read_text(encoding="utf-8").replace("{{APP_VERSION}}", APP_VERSION)
    return HTMLResponse(html)


# ============================================================
# PUBLIC PRICING — readable by anyone (no auth needed)
# ============================================================
@app.get("/api/pricing")
def public_pricing():
    """Returns the user-facing pricing config (packs), plus the real
    per-step charges (transcribeCredits/mergeCredits/charsPerCredit/
    cloneCredits/lipsyncCreditsPerSec) so the app's own credit badges can
    show what a button actually costs instead of a guessed or hardcoded
    number. No auth required — these are prices, not secrets."""
    cfg = _get_pricing_config()
    return {
        "packs": cfg.get("packs", []),
        "transcribeCredits": cfg.get("transcribeCredits", 3),
        "mergeCredits": cfg.get("mergeCredits", 1),
        "charsPerCredit": cfg.get("charsPerCredit", 60),
        "cloneCredits": cfg.get("cloneCredits", 5),
        "lipsyncCreditsPerSec": cfg.get("lipsyncCreditsPerSec", 10)
    }

@app.get("/api/billing/packs")
def billing_packs_dynamic():
    """Returns credit packs from pricing_config (managed by admin panel),
    keyed by each pack's own 'key' field — see _keyed_packs(). This is what
    the landing page and the in-app buy modal both fetch to render pack
    cards, and both now loop over whatever keys come back here instead of
    a fixed list, so any number of packs with any keys will show up."""
    cfg = _get_pricing_config()
    packs_array = cfg.get("packs") or DEFAULT_PACKS
    return {"packs": _keyed_packs(packs_array)}

@app.post("/api/contact")
def contact_form(req: ContactRequest, request: Request):
    """Public contact form (landing page + in-app) -- emails
    CONTACT_TO_EMAIL via Resend. No auth required; rate-limited per IP."""
    if req.hp:
        # Honeypot field a real visitor never sees or fills in --
        # pretend success and do nothing, so bots get no signal.
        return {"ok": True}

    ip = _client_ip(request)
    now = time.time()
    attempts = [t for t in _contact_attempts.get(ip, []) if now - t < CONTACT_WINDOW_SEC]
    if len(attempts) >= CONTACT_MAX_ATTEMPTS:
        return JSONResponse({"ok": False, "error": "Too many messages sent. Please try again later."}, status_code=429)

    name = (req.name or "").strip()[:100]
    email = (req.email or "").strip()[:200]
    message = (req.message or "").strip()[:5000]

    if not email or "@" not in email or "." not in email.split("@")[-1] or " " in email:
        return JSONResponse({"ok": False, "error": "Please enter a valid email address."}, status_code=400)
    if not message:
        return JSONResponse({"ok": False, "error": "Please enter a message."}, status_code=400)

    if not RESEND_API_KEY:
        print("[contact] RESEND_API_KEY not set -- cannot send contact email")
        return JSONResponse({"ok": False, "error": "Contact form is not set up yet. Please email us directly."}, status_code=503)

    attempts.append(now)
    _contact_attempts[ip] = attempts

    subject = f"Lisan AI contact form: {name or email}"
    body_text = f"From: {name or '(no name given)'} <{email}>\n\n{message}"
    payload = json.dumps({
        "from": "Lisan AI Contact <contact@lisanai.org>",
        "to": [CONTACT_TO_EMAIL],
        "reply_to": email,
        "subject": subject,
        "text": body_text,
    }).encode("utf-8")
    try:
        contact_req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
                # Resend sits behind Cloudflare, which blocks Python's default
                # "Python-urllib/3.x" User-Agent as a bot signature (Cloudflare
                # error 1010). A normal-looking User-Agent avoids that block.
                "User-Agent": "LisanAI-Backend/1.0 (+https://lisanai.org)",
            },
            method="POST",
        )
        with urllib.request.urlopen(contact_req, timeout=10) as r:
            if r.status not in (200, 201):
                raise RuntimeError(f"Resend returned status {r.status}")
        return {"ok": True}
    except urllib.error.HTTPError as ex:
        try:
            err_body = ex.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = "(could not read response body)"
        print(f"[contact] Resend send failed: HTTP {ex.code} -- {err_body}")
        return JSONResponse({"ok": False, "error": "Message could not be sent. Please email us directly."}, status_code=502)
    except Exception as ex:
        print(f"[contact] Resend send failed: {ex}")
        return JSONResponse({"ok": False, "error": "Message could not be sent. Please email us directly."}, status_code=502)



# TEMPORARY DIAGNOSTIC ROUTE — delete after we fix the bug
@app.get("/api/admin/test_save")
async def admin_test_save(request: Request):
    """Diagnoses why pricing_config saves are failing. Delete after fix."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    
    import urllib.request as _ur
    
    result = {
        "env": {
            "SUPABASE_URL_set": bool(SUPABASE_URL),
            "SUPABASE_SERVICE_KEY_set": bool(SUPABASE_SERVICE_KEY),
            "url_prefix": SUPABASE_URL[:30] if SUPABASE_URL else None,
            "key_length": len(SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else 0,
        }
    }
    
    # Test 1: Can we READ pricing_config?
    try:
        url = f"{SUPABASE_URL}/rest/v1/pricing_config?select=*"
        hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        result["read_test"] = {"ok": True, "row_count": len(rows) if isinstance(rows, list) else 0, "rows": rows}
    except Exception as e:
        err_msg = str(e)
        err_body = ""
        if hasattr(e, "read"):
            try: err_body = e.read().decode()[:500]
            except: pass
        result["read_test"] = {"ok": False, "error": err_msg, "body": err_body}
    
    # Test 2: Can we WRITE pricing_config (with corrected Prefer header)?
    try:
        body = json.dumps({
            "id": "singleton",
            "price_per_min": 150,
            "free_credits": 150,
            "min_reserve": 150,
            "max_video_min": 60,
            "markup": 4.0,
            "packs": [],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }).encode("utf-8")
        url = f"{SUPABASE_URL}/rest/v1/pricing_config"
        hdrs = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates"  # ← corrected header
        }
        req = _ur.Request(url, data=body, headers=hdrs, method="POST")
        with _ur.urlopen(req, timeout=10) as r:
            result["write_test"] = {"ok": True, "status": r.status}
    except Exception as e:
        err_msg = str(e)
        err_body = ""
        if hasattr(e, "read"):
            try: err_body = e.read().decode()[:500]
            except: pass
        result["write_test"] = {"ok": False, "error": err_msg, "body": err_body}
    
    return result


@app.get("/api/admin/diag_bg/{job_id}")
async def diag_bg(job_id: str, request: Request):
    """Diagnostic: list separated folder contents for a job."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    
    import os
    from media_paths import job_background_audio, UPLOAD_DIR
    
    result = {
        "job_id": job_id,
        "upload_dir": str(UPLOAD_DIR),
        "separated_folder_exists": False,
        "separated_files": [],
        "job_background_audio_result": None,
        "glob_results": {},
        "alternative_files": {},
    }
    
    # Check if the separated folder exists
    sep_folder = UPLOAD_DIR / f"{job_id}_separated"
    result["separated_folder_exists"] = sep_folder.exists()
    
    # List all files in the separated folder
    if sep_folder.exists():
        for root, dirs, files in os.walk(sep_folder):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, sep_folder)
                try:
                    size = os.path.getsize(full)
                except Exception:
                    size = -1
                result["separated_files"].append({"path": rel, "size": size})
    
    # Check what job_background_audio returns
    bg = job_background_audio(job_id)
    result["job_background_audio_result"] = str(bg) if bg else None
    
    # Check each glob pattern individually
    p1 = sorted(UPLOAD_DIR.glob(f"{job_id}_separated/**/no_vocals.wav"))
    result["glob_results"]["pattern_1_no_vocals"] = [str(p) for p in p1]
    
    # Look for alternative names Demucs might produce
    alt_names = [
        "no_vocals.wav", "accompaniment.wav", "other.wav",
        "background.wav", "bg.wav", "instrumental.wav",
        "vocals.wav", "drums.wav", "bass.wav"
    ]
    for name in alt_names:
        found = sorted(UPLOAD_DIR.glob(f"{job_id}_separated/**/{name}"))
        if found:
            result["alternative_files"][name] = [str(p) for p in found]
    
    return result



# TEMPORARY DIAGNOSTIC v2 — thorough background audio lookup
@app.get("/api/admin/diag_bg/{job_id}")
async def diag_bg(job_id: str, request: Request):
    """Diagnostic v2: lists ALL files matching job_id, ALL _separated folders."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    
    import os
    from media_paths import job_background_audio, UPLOAD_DIR
    
    result = {
        "job_id": job_id,
        "upload_dir": str(UPLOAD_DIR),
        "upload_dir_exists": UPLOAD_DIR.exists(),
        "files_matching_prefix": [],
        "all_separated_folders": [],
        "no_vocals_files_anywhere": [],
        "job_background_audio_result": None,
    }
    
    # 1. List ALL files matching the job_id prefix (not just _separated)
    if UPLOAD_DIR.exists():
        for p in UPLOAD_DIR.iterdir():
            if p.name.startswith(job_id):
                try:
                    size = p.stat().st_size
                except Exception:
                    size = -1
                result["files_matching_prefix"].append({
                    "name": p.name,
                    "is_dir": p.is_dir(),
                    "size": size
                })
    
    # 2. List ALL _separated folders in the uploads dir
    if UPLOAD_DIR.exists():
        for p in UPLOAD_DIR.iterdir():
            if p.is_dir() and "_separated" in p.name:
                # List contents of this separated folder
                files_inside = []
                for root, dirs, files in os.walk(p):
                    for f in files:
                        full = os.path.join(root, f)
                        rel = os.path.relpath(full, UPLOAD_DIR)
                        try:
                            size = os.path.getsize(full)
                        except Exception:
                            size = -1
                        files_inside.append({"path": rel, "size": size})
                result["all_separated_folders"].append({
                    "folder": p.name,
                    "file_count": len(files_inside),
                    "files": files_inside[:20]  # limit to first 20
                })
    
    # 3. Search ENTIRE uploads dir for any file named no_vocals.wav
    if UPLOAD_DIR.exists():
        for p in UPLOAD_DIR.rglob("no_vocals.wav"):
            result["no_vocals_files_anywhere"].append(str(p))
        # Also check for accompaniment.wav (Demucs default name)
        for p in UPLOAD_DIR.rglob("accompaniment.wav"):
            result["no_vocals_files_anywhere"].append(str(p))
    
    # 4. Check what job_background_audio returns
    bg = job_background_audio(job_id)
    result["job_background_audio_result"] = str(bg) if bg else None
    
    return result

