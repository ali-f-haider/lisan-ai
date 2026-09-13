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
    voice_type: str = "community"
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
    "/", "/login", "/auth/callback", "/help", "/privacy", "/privacy.html", "/terms", "/terms.html", "/debug-keys", "/api/login",
    "/api/auth/session", "/api/auth/check", "/api/stripe/webhook",
    "/api/billing/packs", "/api/billing/checkout"
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

def get_packs_from_db():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/pricing_config?select=packs&limit=1",
            headers={"apikey": SUPABASE_SERVICE_KEY})
        with urllib.request.urlopen(req, timeout=5) as r:
            rows = json.load(r)
        if not rows:
            return None
        packs = rows[0].get("packs")
        items = []
        if isinstance(packs, dict):
            items = list(packs.items())
        elif isinstance(packs, list):
            for v in packs:
                if isinstance(v, dict):
                    k = v.get("key") or v.get("id") or v.get("name") or v.get("pack")
                    if k:
                        items.append((k, v))
        out = {}
        for k, v in items:
            if not isinstance(v, dict):
                continue
            amt = None
            for n in ("amount_usd", "amountUsd", "price_usd", "price", "usd"):
                if v.get(n) not in (None, ""):
                    amt = v.get(n); break
            if amt in (None, "") and v.get("amount_cents") not in (None, ""):
                amt = float(v["amount_cents"]) / 100.0
            cr = None
            for n in ("credits", "credit", "credit_count"):
                if v.get(n) not in (None, ""):
                    cr = v.get(n); break
            try:
                amt = float(amt); cr = int(cr)
            except (TypeError, ValueError):
                continue
            if amt > 0 and cr > 0:
                out[str(k).lower()] = {"amount_usd": round(amt, 2), "credits": cr}
        print(f"[pricing] packs read from DB: {out}")
        return out or None
    except Exception as e:
        print(f"[pricing] DB fetch failed: {e}")
        return None


CREDIT_PACKS = {
    "starter":  {"amount_usd": 4.99,  "credits": 500},
    "standard": {"amount_usd": 14.99, "credits": 2000},
    "pro":      {"amount_usd": 39.99, "credits": 6000},
    "business": {"amount_usd": 99.99, "credits": 20000},
}

# ---- DB-backed packs (admin-editable via pricing_config.packs), fallback + 60s cache ----
_PACKS_CACHE = {"ts": 0.0, "data": None}

def _norm_pack(v):
    if not isinstance(v, dict):
        return None
    amt = float(v.get("amount_usd") or v.get("amountUsd") or v.get("price") or v.get("usd") or 0)
    cr = int(v.get("credits") or v.get("credit") or 0)
    if amt > 0 and cr > 0:
        return {"amount_usd": round(amt, 2), "credits": cr}
    return None

def _load_packs_from_db():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/pricing_config?select=packs&limit=1",
            headers={"apikey": SUPABASE_SERVICE_KEY,
                     "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            rows = json.load(r)
        if not rows:
            return None
        packs = rows[0].get("packs")
        out = {}
        if isinstance(packs, dict):
            for k, v in packs.items():
                np = _norm_pack(v)
                if np:
                    out[k] = np
        elif isinstance(packs, list):
            for v in packs:
                np = _norm_pack(v)
                if np:
                    k = v.get("key") or v.get("id") or v.get("name")
                    if k:
                        out[str(k).lower()] = np
        return out or None
    except Exception as e:
        print("[pricing] packs DB read failed, using built-in defaults:", e)
        return None

def get_packs():
    """Returns credit packs keyed by pack_key (starter, standard, pro, business).
    Reads from pricing_config table — admin panel is the single source of truth."""
    cfg = _get_pricing_config()
    packs_array = cfg.get("packs", [])
    # Fallback defaults if DB is empty
    if not packs_array:
        packs_array = [
            {"name": "Starter", "credits": 1500, "price_usd": 15.0, "bonus_pct": 0, "stripe_link": ""},
            {"name": "Standard", "credits": 4000, "price_usd": 35.0, "bonus_pct": 14, "stripe_link": ""},
            {"name": "Pro", "credits": 10000, "price_usd": 75.0, "bonus_pct": 33, "stripe_link": ""},
            {"name": "Studio", "credits": 25000, "price_usd": 150.0, "bonus_pct": 66, "stripe_link": ""}
        ]
    keyed = {}
    for p in packs_array:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        key = name.lower().split()[0]
        # Keep backward-compat with old keys (business vs studio)
        if key in ("studio", "business"):
            key = "business"
        keyed[key] = {
            "credits": int(p.get("credits", 0)),
            "amount_usd": float(p.get("price_usd", 0)),
            "bonus_pct": int(p.get("bonus_pct", 0)),
            "stripe_link": p.get("stripe_link", "")
        }
    return keyed


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

def billing_packs():
    return {"packs": get_packs_from_db() or CREDIT_PACKS}

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
        voice_type=req.voice_type or "community",
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
        deduct_credits(uid, 1, "merge", req.job_id)

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
        "pricePerMin": 150,
        "freeCredits": 150,
        "minReserve": 150,
        "maxVideoMin": 60,
        "markup": 4.0,
        "packs": [
            {"name": "Starter", "credits": 1500, "price_usd": 15, "bonus_pct": 0, "stripe_link": ""},
            {"name": "Standard", "credits": 4000, "price_usd": 35, "bonus_pct": 14, "stripe_link": ""},
            {"name": "Pro", "credits": 10000, "price_usd": 75, "bonus_pct": 33, "stripe_link": ""},
            {"name": "Studio", "credits": 25000, "price_usd": 150, "bonus_pct": 66, "stripe_link": ""}
        ]
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
                "pricePerMin": row.get("price_per_min", defaults["pricePerMin"]),
                "freeCredits": row.get("free_credits", defaults["freeCredits"]),
                "minReserve": row.get("min_reserve", defaults["minReserve"]),
                "maxVideoMin": row.get("max_video_min", defaults["maxVideoMin"]),
                "markup": row.get("markup", defaults["markup"]),
                "packs": row.get("packs", defaults["packs"])
            }
    except Exception as ex:
        print(f"[admin] pricing_config load error: {ex}")
    return defaults

def _save_pricing_config(config):
    """Saves pricing config to DB (upsert)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False  # not in DB, but UI already shows current values
    try:
        import urllib.request as _ur
        body = json.dumps({
            "id": "singleton",
            "price_per_min": config.get("pricePerMin", 150),
            "free_credits": config.get("freeCredits", 150),
            "min_reserve": config.get("minReserve", 150),
            "max_video_min": config.get("maxVideoMin", 60),
            "markup": config.get("markup", 4.0),
            "packs": config.get("packs", []),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }).encode("utf-8")
        url = f"{SUPABASE_URL}/rest/v1/pricing_config?id=eq.singleton"
        hdrs = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json"
        }
        req = _ur.Request(url, data=body, headers=hdrs, method="PATCH")
        with _ur.urlopen(req, timeout=10) as r:
            pass
        return True
    except Exception as ex:
        print(f"[admin] pricing_config save error: {ex}")
        return False

class AdminLoginRequest(BaseModel):
    code: str = ""
    password: str = ""

@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest):
    code = req.code or req.password
    if not code or not APP_PASSWORD:
        return JSONResponse({"error": "admin access disabled"}, status_code=403)
    if not hmac.compare_digest(str(code), str(APP_PASSWORD)):
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
        print(f"[admin] log_spend error: {ex}")
        return str(ex)

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
        print(f"[admin] log_audit error: {ex}")
        return str(ex)

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
    return HTMLResponse(p.read_text(encoding="utf-8"))


# ============================================================
# PUBLIC PRICING — readable by anyone (no auth needed)
# ============================================================
@app.get("/api/pricing")
def public_pricing():
    """Returns the user-facing pricing config (packs + per-minute rate).
    No auth required — used by the buy-credits modal."""
    cfg = _get_pricing_config()
    return {
        "pricePerMin": cfg.get("pricePerMin", 150),
        "packs": cfg.get("packs", [])
    }

@app.get("/api/billing/packs")
def billing_packs_dynamic():
    """Returns credit packs from pricing_config (managed by admin panel).
    Maps the admin array structure into the keyed structure the buy modal expects."""
    cfg = _get_pricing_config()
    packs_array = cfg.get("packs", [])
    # Default fallback if DB is empty
    if not packs_array:
        packs_array = [
            {"name": "Starter", "credits": 1500, "price_usd": 15.0, "bonus_pct": 0, "stripe_link": ""},
            {"name": "Standard", "credits": 4000, "price_usd": 35.0, "bonus_pct": 14, "stripe_link": ""},
            {"name": "Pro", "credits": 10000, "price_usd": 75.0, "bonus_pct": 33, "stripe_link": ""},
            {"name": "Studio", "credits": 25000, "price_usd": 150.0, "bonus_pct": 66, "stripe_link": ""}
        ]
    # Map array → keyed dict (lowercase name as key)
    # The buy modal iterates ["starter","standard","pro","business"]
    # so we map by lowercased first word of the pack name
    keyed = {}
    for p in packs_array:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        key = name.lower().split()[0]
        # Keep backward-compat with old keys (business vs studio)
        if key in ("studio", "business"):
            key = "business"
        keyed[key] = {
            "credits": int(p.get("credits", 0)),
            "amount_usd": float(p.get("price_usd", 0)),
            "bonus_pct": int(p.get("bonus_pct", 0)),
            "stripe_link": p.get("stripe_link", "")
        }
    return {"packs": keyed, "price_per_min": cfg.get("pricePerMin", 150)}

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

