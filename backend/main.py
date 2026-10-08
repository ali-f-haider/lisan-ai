from fastapi.staticfiles import StaticFiles
import asyncio
import json
from user_errors import customer_message, customer_payload
import os
import secrets
import shutil
import threading
import urllib.request
import uuid
import audio_enhance
import voice_clean
import bg_duck
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, UploadFile, File, Form, Request, Response
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import ClientDisconnect

from config import (BASE_DIR, DATA_DIR, UPLOAD_DIR, OUTPUT_DIR,
                    GEMINI_API_KEY, ELEVENLABS_API_KEY, INWORLD_API_KEY, HF_TOKEN, APP_PASSWORD, ADMIN_PASSWORD,
                    RESEND_API_KEY, CONTACT_TO_EMAIL, APP_VERSION, SENTRY_DSN, FAL_API_KEY, LOCAL_DEV,
                    LIPSYNC_ENABLED, LIPSYNC_TEST_MODE, DASHSCOPE_API_KEY, DASHSCOPE_WORKSPACE_ID, DASHSCOPE_REGION,
                    SITE_GATE_PASSWORD, lipsync_res, lipsync_rate, lipsync_rates, LIPSYNC_REF_IMAGES_ENABLED, LONGDUB_ALL_TIERS)
import app_state
from app_state import jobs_progress, usage_bucket
from models import Segment
import whisper_service
import gemini_service
import eleven_service
import inworld_service
import ffmpeg_utils
import lipsync_service
import longdub_service
import subs_align
import subs_export
import watermark
import r2_backup
import business_metrics
import alibaba_cost
import disk_guard
import railway_monitor
import resource_meter
import assistant_service
import service_usage_monitor
import dub_review
import dub_audio
import dub_background
import fal_usage
import longdub_edits
import emotion_review
from media_paths import resolve_job_audio, find_job_video, job_background_audio
from shortdub_paths import line_audio_path, begin_operation, finish_operation, operation_active
from shortdub_billing import studio_quote, debit_confirmed

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
# SENTRY_INITIALIZED tracks whether sentry_sdk.init() actually SUCCEEDED,
# not just whether SENTRY_DSN is set -- the admin Health panel checks this
# flag rather than the DSN string, since this exact init call has silently
# failed before (see the huggingface_hub AttributeError above) while the
# DSN itself stayed configured the whole time, which a DSN-presence check
# would have missed entirely.
SENTRY_INITIALIZED = False
try:
    if SENTRY_DSN:
        import sentry_sdk
        from sentry_sdk.integrations.huggingface_hub import HuggingfaceHubIntegration
        # Sentry attaches the local variables of every frame to an error. Its built-in filter only hides names such
        # as "password"; the admin login keeps the typed code in a variable called "code", so a mistyped admin code
        # showed up in plain text in an error report. These names are now hidden too.
        try:
            from sentry_sdk.scrubber import EventScrubber, DEFAULT_DENYLIST
            _scrubber = EventScrubber(denylist=list(DEFAULT_DENYLIST) + ["code", "admin_code", "token", "pin", "otp", "secret", "api_key", "authorization", "cookie"], recursive=True)
        except Exception:
            _scrubber = None
        def _sentry_before_send(event, hint):
            # A customer closing the tab or losing signal in the middle of an upload is normal, not a bug.
            # Starlette reports it as ClientDisconnect (sometimes wrapped in an ExceptionGroup); drop those.
            try:
                exc = (hint or {}).get("exc_info", (None, None, None))[1]
                stack = [exc]
                while stack:
                    e = stack.pop()
                    if e is None:
                        continue
                    if type(e).__name__ == "ClientDisconnect":
                        return None
                    stack.extend(getattr(e, "exceptions", None) or [])
            except Exception:
                pass
            return event
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            before_send=_sentry_before_send,
            traces_sample_rate=0.1,
            send_default_pii=False,
            disabled_integrations=[HuggingfaceHubIntegration()],
            **({"event_scrubber": _scrubber} if _scrubber is not None else {}),
        )
        SENTRY_INITIALIZED = True
except Exception as _sentry_ex:
    print(f"[sentry] init skipped: {_sentry_ex}")

app = FastAPI()


@app.exception_handler(Exception)
async def _unhandled_error(request, exc):
    """Anything nobody caught: the detail goes to the server log, the customer gets a plain sentence
    (instead of a bare "Internal Server Error" page that the app cannot read)."""
    import traceback as _tb
    print(f"[error] unhandled in {request.method} {request.url.path}: {type(exc).__name__}: {exc}\n{_tb.format_exc()}")
    return JSONResponse({"error": "Something went wrong on our side. Please try again in a moment."}, status_code=500)


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

# Security review (2026-09-30): job ids come from the browser and end up inside
# file names and glob patterns ("{job_id}*"). A value like "*" or "a?" used to
# match OTHER users' files. Real ids are uuid4 strings, so only plain
# letters/digits/_/- are accepted anywhere an id is read from a request.
import re as _re_sec
_JOB_ID_RE = _re_sec.compile(r"[A-Za-z0-9_-]{20,100}")   # real ids are 36-char uuid4s; short values like "a" would prefix-match many files
_SAFE_TOKEN_RE = _re_sec.compile(r"[A-Za-z0-9_-]{8,200}")


def _bad_segment_id(v) -> bool:
    v = str(v or "")
    return (not v) or len(v) > 200 or any(c in v for c in ("/", "\\", "\x00")) or ".." in v


class _JobIdModel(BaseModel):
    @field_validator("job_id", check_fields=False)
    @classmethod
    def _job_id_ok(cls, v):
        if v and not _JOB_ID_RE.fullmatch(str(v)):
            raise ValueError("bad job id")
        return v

    @field_validator("segment_id", check_fields=False)
    @classmethod
    def _segment_id_ok(cls, v):
        if _bad_segment_id(v):
            raise ValueError("bad segment id")
        return v


class LoginRequest(BaseModel):
    password: str = ""

class AuthRequest(BaseModel):
    access_token: str = ""
    refresh_token: str = ""

class AnalyzeRequest(_JobIdModel):
    job_id: str
    segments: List[Segment]

class CloneRequest(_JobIdModel):
    job_id: str
    segments: List[Segment]
    speakers_to_clone: List[str] = []

class VoiceUpdateRequest(BaseModel):
    name: str = ""
    description: str = ""

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

class TranslateRequest(_JobIdModel):
    job_id: str = ""
    segments: List[Segment]

class EmotionRequest(_JobIdModel):
    job_id: str
    segments: List[Segment]

class GenerateRequest(_JobIdModel):
    job_id: str = ""
    accepted_credits: Optional[int] = None
    segments: List[Segment]
    elevenlabs_api_key: str = ""
    gemini_api_key: str = ""
    # Server-set (not trusted from the client) right before generate_worker
    # runs -- see /api/generate. Not the "elevenlabs vs gemini" job-wide
    # mode selector (tts_provider, unchanged); this is which engine created
    # each SPEAKER's already-cloned voice_id, since that never changes for
    # a voice once it's cloned even if the admin panel's Voice Engine
    # switch changes later. speaker_voice_engines is keyed exactly like
    # speaker_voices; default_voice_engine covers default_voice_id.
    inworld_api_key: str = ""
    speaker_voice_engines: Dict[str, str] = {}
    default_voice_engine: str = "elevenlabs"
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

class RegenerateLineRequest(_JobIdModel):
    job_id: str = ""
    operation_id: Optional[str] = None
    accepted_credits: Optional[int] = None
    segment: Segment
    segments: List[Segment] = []
    elevenlabs_api_key: str = ""
    # Server-set right before eleven_service.regenerate_line runs -- see
    # /api/regenerate_line. Which engine created THIS voice_id (see
    # GenerateRequest.speaker_voice_engines' comment for why this can't
    # just be "whatever the admin panel currently says").
    inworld_api_key: str = ""
    voice_engine: str = "elevenlabs"
    voice_id: str = ""
    tempo_mode: str = "excellent"
    duration_mode: str = "exact"
    overlap_allowed: dict = {}
    dead_space_allowed: dict = {}
    offsets: Dict[str, float] = {}   # timeline offsets the user confirmed; kept when only one line is rebuilt
    total_duration: float = 0.0

class RemixRequest(_JobIdModel):
    job_id: str = ""
    segments: List[Segment] = []
    offsets: Dict[str, float] = {}
    gains: Dict[str, float] = {}
    total_duration: float = 0.0
    duration_mode: str = "exact"
    overlap_allowed: dict = {}
    dead_space_allowed: dict = {}
    room: dict = {}      # Step 5.5 "Room sound": {"mode": "auto"|"off"|"manual", "rt60": s, "wet_db": dB}

class MergeRequest(_JobIdModel):
    job_id: str
    enhance_background: bool = True
    keep_music: bool = True
    accepted_credits: int = -1

class LipSyncRequest(_JobIdModel):
    job_id: str
    provider: str = "wan3"
    model: str = "lipsync-2"
    sync_key: str = ""
    resolution: str = ""        # "480P" / "720P" / "1080P"; empty = 720P
    operation_id: Optional[str] = None

class TashkeelItem(_JobIdModel):
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

# ---- Durable backing store for login sessions ----
# _sessions/_valid_tokens above are the fast path (checked on every request,
# no network call), but they're plain in-memory Python state -- a Railway
# restart (which happens on every deploy) wipes them, which used to log out
# every logged-in user immediately, even though their login was still good.
# Needs one new Supabase table (run once in the SQL editor):
#   CREATE TABLE IF NOT EXISTS app_sessions (
#     token text PRIMARY KEY,
#     sb_access_token text,
#     created_at timestamptz DEFAULT now()
#   );
# Written to (best-effort, background thread) whenever a session is created;
# read from only as a fallback when the in-memory set doesn't recognize a
# cookie -- normally that means "this process restarted since you logged
# in", not "this cookie is invalid". A hit repopulates the in-memory caches
# so the rest of that process's requests for this cookie stay fast again.
# Requires SUPABASE_SERVICE_KEY (same one already used for credits/consent);
# silently a no-op without it, same as _record_voice_consent below.
def _stripe_fail(ex, where=""):
    """Stripe failed: the real reason goes to the log, the customer gets a plain sentence (Stripe's own
    customer-safe text, e.g. "Your card was declined.", is kept when it has one)."""
    print(f"[stripe] {where} failed: {type(ex).__name__}: {ex}")
    msg = getattr(ex, "user_message", None)
    return JSONResponse({"error": msg or "We couldn't complete that with our payment provider. Please try again, or contact support if it keeps happening."}, status_code=502)


_PRIVATE_PROGRESS_KEYS = ("error_trace", "background_path", "generation_id", "provider_video")


def _public_progress(d):
    """A job's progress as the browser may see it: no server paths, tracebacks or provider task ids."""
    if not isinstance(d, dict):
        return d
    out = {k: v for k, v in d.items() if k not in _PRIVATE_PROGRESS_KEYS}
    r = out.get("result")
    if isinstance(r, dict) and ("output_folder" in r or "final_file" in r):
        out["result"] = {k: v for k, v in r.items() if k not in ("output_folder", "final_file")}
    return customer_payload(out)


def _http_err_detail(ex):
    """str(ex), plus the response body when it's an HTTPError -- Supabase's
    JSON body says WHY a 403/400 happened (e.g. "permission denied for table
    ...", code 42501), which the bare "HTTP Error 403: Forbidden" hides."""
    try:
        body = getattr(ex, "read", None)
        if callable(body):
            return f"{ex} | body: {body().decode('utf-8', 'replace')[:300]}"
    except Exception:
        pass
    return str(ex)


def _persist_session(token, sb_access_token):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not token:
        return

    def _run():
        import urllib.request as _ur
        body = json.dumps({
            "token": token,
            "sb_access_token": sb_access_token or None,
        }).encode("utf-8")
        req = _ur.Request(
            f"{SUPABASE_URL}/rest/v1/app_sessions",
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
            print(f"[session] could not persist session: {_http_err_detail(ex)}")

    threading.Thread(target=_run, daemon=True).start()


def _restore_session_from_db(token) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not token:
        return False
    if not _SAFE_TOKEN_RE.fullmatch(str(token)):
        return False
    try:
        url = f"{SUPABASE_URL}/rest/v1/app_sessions?token=eq.{token}&select=sb_access_token"
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.load(r)
    except Exception as ex:
        print(f"[session] could not restore session: {_http_err_detail(ex)}")
        return False
    if not rows:
        return False
    _sessions.add(token)
    sb_access_token = rows[0].get("sb_access_token")
    if sb_access_token:
        _valid_tokens[token] = sb_access_token
    return True


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
    if not cookie:
        return False
    if cookie not in _sessions and not _restore_session_from_db(cookie):
        return False
    sb_token = _valid_tokens.get(cookie, "")
    if sb_token:
        # A session that was created by a real (Supabase) login stands or falls with that login. It must NOT fall
        # back to the shared-password rule below when the login has expired: that turned an expired customer
        # session into a logged-in session with no account (shown as "Guest", credits -1) that was never charged.
        return _verify_supabase_token(sb_token)
    if APP_PASSWORD:
        return True       # a session made with the shared password (no Supabase account behind it)
    return False

# --- Site-wide "private testing" gate (Sept 2026) ---
# Separate from APP_PASSWORD (the shared *login*) and from ADMIN_PASSWORD
# (the admin panel's own login) -- this one gates being able to reach ANY
# page at all, including the public landing page and /login itself, so a
# stranger who finds the URL while the product is still being tested sees
# nothing until they enter this password. The real password only ever
# lives in Railway's SITE_GATE_PASSWORD env var (never in the DB, never
# sent to a browser) -- leave that unset and this whole gate is a no-op,
# restoring today's fully-public behavior with no code change needed
# either way. Whether the gate is actually ENFORCED right now is a
# separate on/off switch, saved in pricing_config and flippable from the
# admin panel (Ali's request, Sept 2026: a way to instantly shut it off
# without a trip to Railway, while keeping the password saved for next
# time). See _site_gate_active below.
SITE_GATE_COOKIE = "site_gate_ok"
GATE_EXEMPT_PATHS = frozenset(["/gate", "/api/site-gate", "/api/stripe/webhook"])

# Cache the admin toggle briefly instead of hitting Supabase on literally
# every request this server handles (this check runs in AuthMiddleware,
# ahead of every page, asset and API call) -- 20 seconds is fast enough
# that flipping the admin switch takes effect almost immediately, without
# adding a DB round-trip to the site's hot path.
_site_gate_cache = {"enabled": True, "checked_at": 0.0}
SITE_GATE_CACHE_TTL_SEC = 20

def _site_gate_active() -> bool:
    """Whether the gate should block requests right now. Requires BOTH the
    Railway env var (the actual password) to be set AND the admin panel's
    toggle to be on -- defaults to on (True) so setting the env var alone
    reproduces the original always-on behavior until someone visits the
    admin panel and changes it."""
    if not SITE_GATE_PASSWORD:
        return False
    now = time.time()
    if now - _site_gate_cache["checked_at"] > SITE_GATE_CACHE_TTL_SEC:
        try:
            _site_gate_cache["enabled"] = bool(_get_pricing_config().get("siteGateEnabled", True))
        except Exception:
            pass  # Supabase hiccup -- keep the last known value rather than fail open or crash
        _site_gate_cache["checked_at"] = now
    return _site_gate_cache["enabled"]

# --- Independent on/off switches for the automatic usage-alert emails
# (Sept 2026) ---
# service_usage_monitor.py (ElevenLabs quota) and railway_monitor.py
# (Railway memory) each email CONTACT_TO_EMAIL via Resend when usage runs
# high. Ali's request: stop these -- they were burning through his free
# Resend account's send quota on top of the real contact-form/expiry-notice
# emails -- and control each one separately, not as one combined switch.
# This only gates the EMAIL step in each module; the underlying polling
# and the admin dashboard's live numbers (Health tab) are completely
# unaffected either way, so turning either off doesn't lose any
# visibility, it just stops that one inbox notification.
#
# No local cache here (unlike _site_gate_active above) -- these are only
# ever checked right before actually sending an alert email, which happens
# at most once per ~20-minute poll cycle in each monitor, never on a
# request's hot path, so a fresh Supabase read every time is fine and
# means flipping either in admin takes effect on that monitor's very next
# poll, not after some cache delay.
def _inworld_slot_limit() -> int:
    """Admin-set custom-voice slot limit for the Inworld plan (used by
    service_usage_monitor). Falls back to 100 (Inworld On-Demand)."""
    try:
        return max(1, int(_get_pricing_config().get("inworldSlotLimit") or 100))
    except Exception:
        return 100


def _eleven_alerts_enabled() -> bool:
    try:
        return bool(_get_pricing_config().get("elevenAlertsEnabled", True))
    except Exception:
        return True  # fail OPEN, not closed -- a Supabase hiccup should never silently swallow a real "you're about to run out" warning

def _disk_alerts_enabled() -> bool:
    try:
        return bool(_get_pricing_config().get("diskAlertsEnabled", True))
    except Exception:
        return True  # fail OPEN -- same reasoning as the two above

def _railway_alerts_enabled() -> bool:
    try:
        return bool(_get_pricing_config().get("railwayAlertsEnabled", True))
    except Exception:
        return True  # fail OPEN, not closed -- same reasoning as above

def _site_gate_token() -> str:
    # Derived from the password rather than storing it verbatim in the
    # cookie. Also means changing SITE_GATE_PASSWORD in Railway harmlessly
    # signs out everyone already past the gate (their old cookie no longer
    # matches), with no extra code needed to "revoke" old gate cookies.
    import hashlib
    return hashlib.sha256(f"site-gate:{SITE_GATE_PASSWORD}".encode()).hexdigest()

def _site_gate_ok(request: Request) -> bool:
    if not _site_gate_active():
        return True  # gate disabled (env var unset, or admin switched it off)
    cookie = request.cookies.get(SITE_GATE_COOKIE, "")
    return bool(cookie) and _safe_eq(cookie, _site_gate_token())

PUBLIC_PATHS = frozenset([
    "/", "/pricing", "/login", "/robots.txt", "/sitemap.xml", "/auth/callback", "/help", "/privacy", "/privacy.html", "/terms", "/terms.html", "/debug-keys", "/api/login",
    "/api/auth/session", "/api/auth/check", "/api/me", "/api/stripe/webhook",
    "/api/maintenance", "/api/billing/packs", "/api/billing/checkout", "/api/billing/subscribe", "/api/billing/portal", "/api/billing/cancel", "/api/contact", "/api/assistant", "/api/assistant/credits",
    "/api/account/delete"
, "/help.html", "/admin"])

# Security review (2026-09-30): upload endpoints used to accept a body of ANY
# size -- FastAPI spools the whole multipart body to disk before the route
# runs, so the size check inside the route came after the damage (a 4 GB
# upload on a 5 GB Railway volume would fill it for everybody). The check
# here uses the Content-Length header, which every browser sends for a
# FormData upload, and refuses before a single byte is read.
_UPLOAD_PATH_CAPS_MB = {
    "/api/transcribe": 305,            # MAX_TRIM_UPLOAD_MB (300) + form fields
    "/api/attach_media": 305,
    "/api/upload_custom_voice": 12,    # route allows 10 MB
    "/api/lipsync/reference-images": 70,
}
_SMALL_BODY_CAP_MB = 20                # every other non-upload API call
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=31536000",
}


def _path_has_route(request) -> bool:
    """True when the request path matches a route (or mount) the app really
    serves, for any HTTP method. Used so logged-out visitors get the login
    redirect only on real pages and a plain 404 on made-up paths."""
    try:
        from starlette.routing import Match
        scope = request.scope
        for _r in request.app.router.routes:
            _m, _ = _r.matches(scope)
            if _m in (Match.FULL, Match.PARTIAL):
                return True
    except Exception:
        return True   # never block a real page because of a lookup error
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method in ("POST", "PUT", "PATCH") and path.startswith("/api/") and path not in GATE_EXEMPT_PATHS:
            cap_mb = _UPLOAD_PATH_CAPS_MB.get(path)
            if cap_mb is None and "/chunk" not in path:
                cap_mb = _SMALL_BODY_CAP_MB
            if cap_mb is not None:
                try:
                    clen = int(request.headers.get("content-length") or -1)
                except ValueError:
                    clen = -1
                if clen > cap_mb * 1024 * 1024:
                    return JSONResponse({"error": "That file is too large."}, status_code=413)
                if clen < 0 and path in _UPLOAD_PATH_CAPS_MB:
                    return JSONResponse({"error": "Upload not accepted (missing size)."}, status_code=411)
        resp = await self._dispatch_inner(request, call_next)
        try:
            for _k, _v in _SECURITY_HEADERS.items():
                if _k == "Strict-Transport-Security" and request.headers.get("x-forwarded-proto", request.url.scheme) != "https":
                    continue
                resp.headers.setdefault(_k, _v)
        except Exception:
            pass
        return resp

    async def _dispatch_inner(self, request: Request, call_next):
        path = request.url.path
        # /gate and /api/site-gate must ALWAYS be reachable no matter what,
        # bypassing every check below -- not just the gate check itself.
        # Bug fixed here (Ali reported the URL bouncing between /login and
        # /gate): /gate used to only skip the SITE GATE check, but then
        # fell through to the old "are you logged in" check further down,
        # which isn't satisfied either -- so it redirected to /login,
        # which then wasn't gate-exempt, which redirected back to /gate,
        # forever. Returning immediately here means these two paths never
        # touch the login check at all.
        if path in GATE_EXEMPT_PATHS:
            return await call_next(request)
        # Hidden files and folders (.git, .svn, .env, .DS_Store ...) never
        # exist on this site -- answer a plain 404 straight away, before the
        # gate or the login redirect, so scanners probing for them don't get
        # a 200 "redirect to login" page that looks like a hit. /.well-known/
        # is the one legitimate dot-path (certificates, security.txt).
        if any(_seg.startswith(".") for _seg in path.split("/")[1:]) and not path.startswith("/.well-known/"):
            return PlainTextResponse("Not found", status_code=404)
        # The site-wide testing gate runs next and applies to EVERY other
        # path, including ones PUBLIC_PATHS and the /api/admin/ bypass below
        # would otherwise let straight through -- that's the whole point
        # while the gate is active (see _site_gate_active above).
        if not _site_gate_ok(request):
            if path.startswith("/api/"):
                return JSONResponse({"error": "Lisan AI isn't open to the public yet. Please enter your access code."}, status_code=401)
            import urllib.parse
            next_q = urllib.parse.quote(path, safe="")
            return HTMLResponse(f'<script>window.location.href="/gate?next={next_q}";</script>', status_code=200)
        if path in PUBLIC_PATHS or path.startswith("/api/auth/") or path.startswith("/api/admin/"):
            return await call_next(request)
        if not path.startswith("/api/") and path.endswith((".css", ".js", ".svg", ".woff2", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".mp4", ".webm")):
            return await call_next(request)
        if not _is_logged_in(request):
            if path.startswith("/api/"):
                return JSONResponse({"error": "Not logged in"}, status_code=401)
            if not _path_has_route(request):
                # Not one of our pages at all (a scanner probing for files):
                # a real 404, not the "go to login" page with a 200.
                return PlainTextResponse("Not found", status_code=404)
            return HTMLResponse('<script>window.location.href="/login";</script>', status_code=200)
        if request.method == "POST" and path in ("/api/transcribe", "/api/attach_media"):
            # Disk guard: say "try again shortly" up front, before the user waits
            # through an upload that could not be saved (and before any charge).
            try:
                _clen = max(0, int(request.headers.get("content-length") or 0))
            except ValueError:
                _clen = 0
            _ok, _ = await asyncio.to_thread(disk_guard.check, _clen, disk_guard.WORK_SHORT_GB, _is_final_output)
            if not _ok:
                return JSONResponse({"error": disk_guard.REFUSAL_MESSAGE, "capacity": True}, status_code=503)
        return await call_next(request)

app.add_middleware(AuthMiddleware)

# Local test copy only (run_local.bat): an orange strip on every HTML page, so a
# local window can never be mistaken for the real site -- it talks to the LIVE database.
if LOCAL_DEV:
    class _LocalBannerMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            resp = await call_next(request)
            if request.url.path.startswith("/api/") or not resp.headers.get("content-type", "").startswith("text/html"):
                return resp
            body = b"".join([c async for c in resp.body_iterator])
            strip = (b'<div style="position:fixed;left:0;right:0;bottom:0;z-index:2147483647;background:#f59e0b;'
                     b'color:#111;font:700 12px system-ui,sans-serif;text-align:center;padding:4px;pointer-events:none">'
                     b'LOCAL TEST COPY - connected to the LIVE database</div>')
            i = body.rfind(b"</body>")
            if i != -1:
                body = body[:i] + strip + body[i:]
            new = Response(content=body, status_code=resp.status_code)
            new.raw_headers = [(k, v) for k, v in resp.raw_headers if k.lower() != b"content-length"] + [(b"content-length", str(len(body)).encode())]
            return new
    app.add_middleware(_LocalBannerMiddleware)

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

def _safe_eq(a, b) -> bool:
    """Constant-time string compare that never raises. hmac.compare_digest
    throws TypeError ("comparing strings with non-ASCII characters is not
    supported") when either side has a non-ASCII character -- e.g. an
    Arabic-keyboard slip typed into a password box -- which turned a wrong
    password into a 500 error. Comparing the UTF-8 bytes avoids that."""
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


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
_user_info_cache = {}
@app.get("/api/user/info")
def user_info(request: Request):
    import hashlib
    cookie = request.cookies.get("session", "")
    user_id = _current_uid(request)
    if not user_id:
        if cookie and (_valid_tokens.get(cookie) or _sessions.get(cookie)):
            return JSONResponse({"error": "Your account is temporarily unavailable. Please retry."}, status_code=503)
        return {"name": "Guest", "credits": None, "is_guest": True, "lipsync_enabled": LIPSYNC_ENABLED}
    try:
        cached = _user_info_cache.get(user_id, {})
        email = _email_for_uid(user_id) or ""
        display_name = cached.get("name") or (email.split("@")[0] if email else "User")
        
        # Read credits securely via service key (bypasses RLS issues). A
        # None here means no profile row exists yet for this user -- this
        # fallback shows the admin-configured starting balance (freeCredits)
        # instead of a hardcoded number, though the real balance a new user
        # ends up with is whatever Supabase grants when it creates their
        # profile row (outside this codebase) -- keep the two in sync by
        # eye if you change freeCredits in the admin panel.
        credits = get_credits(user_id)
        # None means unavailable; never invent a balance when the database cannot answer.
            
        subscription_status = "none"
        # The profile is read with the SERVER key, not the user's own token:
        # Supabase now refuses the user's token on public.profiles ("permission
        # denied for table profiles", code 42501 -- seen in the logs), which
        # made display names fall back to the e-mail prefix and every
        # subscriber look unsubscribed. user_id was already verified from the
        # token above, so reading this one user's row server-side is safe.
        try:
            if SUPABASE_SERVICE_KEY:
                dn_req = urllib.request.Request(
                    f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=display_name",
                    headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
                with urllib.request.urlopen(dn_req, timeout=10) as dn_r:
                    dn_rows = json.load(dn_r)
                if dn_rows and dn_rows[0].get("display_name"):
                    display_name = dn_rows[0]["display_name"]
        except Exception as ex:
            print("[user_info] display_name read failed:", _http_err_detail(ex))
        # The user-token read above can miss subscription_status (row-level or
        # column-level permissions for the user's own token), which made an
        # active subscriber look "not subscribed". The uid is already verified
        # from the token, so read just this one column with the service key.
        sub_prof = _read_subscription_profile(user_id) if SUPABASE_SERVICE_KEY else {}
        if sub_prof:
            subscription_status = sub_prof.get("subscription_status") or "none"

        result = {"name": display_name, "uid": user_id, "credits": credits, "is_guest": False, "lipsync_enabled": LIPSYNC_ENABLED,
                  "subscription_status": subscription_status, "watermark": bool(_wm_needed(user_id)),
                  # Identifies THIS login (a new login always gets a new session cookie) without revealing the cookie;
                  # the page uses it to start every new login with an empty notification list.
                  "login_id": hashlib.sha256(("lisan-login:" + cookie).encode("utf-8")).hexdigest()[:16] if cookie else ""}
        if subscription_status == "active":
            # Which plan, when it renews/ends, and whether it's been cancelled
            # (still active until the paid period ends) -- for the Account
            # page and the Buy box.
            plan_key = sub_prof.get("subscription_plan_key") or ""
            try:
                plan = _get_subscription_plan(plan_key)
                result["subscription_plan_key"] = plan.get("key") or plan_key
                result["subscription_plan_name"] = plan.get("name") or ""
                result["subscription_plan_credits"] = plan.get("credits") or 0
            except Exception:
                result["subscription_plan_key"] = plan_key
            result["subscription_current_period_end"] = sub_prof.get("subscription_current_period_end") or None
            result["subscription_cancel_at_period_end"] = bool(sub_prof.get("subscription_cancel_at_period_end"))
            pending_key = sub_prof.get("subscription_pending_plan_key") or ""
            if pending_key:
                result["subscription_pending_plan_key"] = pending_key
                try:
                    result["subscription_pending_plan_name"] = _get_subscription_plan(pending_key).get("name") or ""
                except Exception:
                    result["subscription_pending_plan_name"] = ""
        _user_info_cache[user_id] = {"name": display_name}
        return result
    except Exception:
        return JSONResponse({"error": "Your account is temporarily unavailable. Please retry."}, status_code=503)

def _cookie_secure(request: Request) -> bool:
    """Secure cookies only over https (Railway terminates TLS and tells us
    via X-Forwarded-Proto); local http development keeps working."""
    try:
        return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    except Exception:
        return False


def _forget_session(tok: str):
    """Really ends a login: drops it from memory AND from the app_sessions
    table, so a copied cookie stops working (before, logout only asked the
    browser to delete its copy, and the server kept honouring the old one
    for up to 7 days)."""
    if not tok:
        return
    _sessions.discard(tok)
    _valid_tokens.pop(tok, None)
    _session_users.pop(tok, None)
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY and _SAFE_TOKEN_RE.fullmatch(str(tok))):
        return

    def _run():
        try:
            req = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/app_sessions?token=eq.{tok}",
                headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                         "Prefer": "return=minimal"},
                method="DELETE")
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as ex:
            print(f"[session] could not delete session row: {ex}")

    threading.Thread(target=_run, daemon=True).start()


@app.post("/api/logout")
def logout(request: Request, response: Response):
    _forget_session(request.cookies.get("session", ""))
    response.delete_cookie("session")
    return {"ok": True}




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
def auth_session(req: AuthRequest, response: Response, request: Request):
    if _verify_supabase_token(req.access_token):
        tok = secrets.token_hex(32)
        _sessions.add(tok)
        _valid_tokens[tok] = req.access_token
        _persist_session(tok, req.access_token)
        response.set_cookie("session", tok, httponly=True, max_age=86400 * 7, samesite="lax", secure=_cookie_secure(request))
        return {"ok": True}
    return JSONResponse({"error": "Your session has expired. Please log in again."}, status_code=401)


@app.get("/api/auth/check")
def auth_check(request: Request):
    if _is_logged_in(request):
        return {"ok": True}
    return JSONResponse({"error": "Not logged in"}, status_code=401)


_me_cache = {}   # session cookie -> (display name, expires at); only to spare Supabase a call per landing-page visit

@app.get("/api/me")
def api_me(request: Request):
    """Public: who is signed in right now? The landing page uses it to show the person's name instead of the login links.
    Returns {"signed_in": false} for a visitor, {"signed_in": true, "name": "..."} otherwise (name may be empty for a
    shared-password session, which has no account behind it). Nothing else about the account is exposed."""
    hdr = {"Cache-Control": "no-store"}
    if not _is_logged_in(request):
        return JSONResponse({"signed_in": False}, headers=hdr)
    cookie = request.cookies.get("session", "")
    hit = _me_cache.get(cookie)
    if hit and hit[1] > time.time():
        return JSONResponse({"signed_in": True, "name": hit[0]}, headers=hdr)
    name = ""
    sb_token = _valid_tokens.get(cookie, "")
    if sb_token and SUPABASE_URL:
        try:
            req = urllib.request.Request(f"{SUPABASE_URL}/auth/v1/user", headers={
                "Authorization": f"Bearer {sb_token}", "apikey": SUPABASE_ANON_KEY})
            with urllib.request.urlopen(req, timeout=10) as r:
                u = json.load(r)
            uid = u.get("id", "")
            email = u.get("email") or ""
            name = email.split("@")[0] if email else ""
            if uid and SUPABASE_SERVICE_KEY:
                try:
                    dn_req = urllib.request.Request(
                        f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select=display_name",
                        headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
                    with urllib.request.urlopen(dn_req, timeout=10) as dn_r:
                        rows = json.load(dn_r)
                    if rows and rows[0].get("display_name"):
                        name = rows[0]["display_name"]
                except Exception:
                    pass
        except Exception:
            name = ""
    name = str(name or "").strip()[:60]
    if len(_me_cache) > 2000:
        _me_cache.clear()
    _me_cache[cookie] = (name, time.time() + 300)
    return JSONResponse({"signed_in": True, "name": name}, headers=hdr)


@app.post("/api/login")
def login_legacy(req: LoginRequest, response: Response, request: Request):
    if _login_rate_limited(request):
        return JSONResponse({"error": "Too many attempts. Try again in a few minutes."}, status_code=429)
    if APP_PASSWORD and _safe_eq(req.password, APP_PASSWORD):
        tok = secrets.token_hex(32)
        _sessions.add(tok)
        _persist_session(tok, None)
        response.set_cookie("session", tok, httponly=True, max_age=86400 * 7, samesite="lax", secure=_cookie_secure(request))
        return {"ok": True}
    _record_login_fail(request)
    return {"ok": False}


@app.post("/api/site-gate")
def site_gate_submit(req: LoginRequest, response: Response, request: Request):
    if _login_rate_limited(request):
        return JSONResponse({"error": "Too many attempts. Try again in a few minutes."}, status_code=429)
    if SITE_GATE_PASSWORD and _safe_eq(req.password, SITE_GATE_PASSWORD):
        response.set_cookie(SITE_GATE_COOKIE, _site_gate_token(), httponly=True, max_age=86400 * 30, samesite="lax", secure=_cookie_secure(request))
        return {"ok": True}
    _record_login_fail(request)
    return {"ok": False}


@app.get("/gate")
def site_gate_page(next: str = "/"):
    # Deliberately self-contained (no external CSS/JS/images) -- this page
    # itself must always be reachable even when everything else is gated,
    # so it can't depend on any other route (e.g. /logo.png) also being
    # exempted.
    safe_next = next if next.startswith("/") and not next.startswith("//") else "/"
    import json as _json
    # Leads with a plain "under construction" message for the vast majority
    # of visitors (random people who found the URL, not testers) -- Ali's
    # request (Sept 2026). The access-code field still works exactly the
    # same underneath, just styled as a small, secondary detail rather than
    # the headline, so it doesn't read as "this is a private beta with a
    # login" to someone who isn't a tester.
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lisan AI</title>
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:20px}}
.box{{background:#1e293b;border-radius:16px;padding:36px 32px;max-width:380px;width:100%;box-shadow:0 10px 40px rgba(0,0,0,.4);text-align:center}}
.icon{{font-size:34px;margin-bottom:6px}}
h1{{font-size:20px;margin:0 0 10px}}
p{{font-size:14px;color:#94a3b8;margin:0;line-height:1.5}}
.divider{{border:none;border-top:1px solid #334155;margin:26px 0 16px}}
.code-toggle{{font-size:12px;color:#64748b;background:none;border:none;cursor:pointer;padding:0;text-decoration:underline}}
.code-form{{display:none;margin-top:14px;text-align:left}}
.code-form.open{{display:block}}
input{{width:100%;padding:9px 11px;border-radius:8px;border:1.5px solid #334155;background:#0f172a;color:#fff;font-size:13px;box-sizing:border-box}}
button[type="submit"]{{width:100%;margin-top:10px;padding:9px;border:none;border-radius:8px;background:#4f46e5;color:#fff;font-weight:600;cursor:pointer;font-size:13px}}
#err{{color:#f87171;font-size:12px;margin-top:8px;min-height:14px}}
</style></head><body>
<div class="box">
<div class="icon">ðŸš§</div>
<h1>Under Construction</h1>
<p>Lisan AI is being tested right now and will be available soon. Thanks for checking back!</p>
<hr class="divider">
<button type="button" class="code-toggle" id="codeToggle">Have an access code?</button>
<form id="f" class="code-form">
<input type="password" id="pw" placeholder="Access code" autocomplete="off">
<button type="submit">Continue</button>
<div id="err"></div>
</form>
</div>
<script>
document.getElementById('codeToggle').addEventListener('click', function () {{
  var f = document.getElementById('f');
  f.classList.toggle('open');
  if (f.classList.contains('open')) document.getElementById('pw').focus();
}});
document.getElementById('f').addEventListener('submit', async function (e) {{
  e.preventDefault();
  var err = document.getElementById('err');
  err.textContent = '';
  try {{
    var res = await fetch('/api/site-gate', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{password: document.getElementById('pw').value}})
    }});
    var data = await res.json();
    if (data.ok) {{ window.location.href = {_json.dumps(safe_next)}; }}
    else {{ err.textContent = res.status === 429 ? (data.error || 'Too many attempts.') : 'Wrong code.'; }}
  }} catch (e2) {{ err.textContent = 'Something went wrong. Try again.'; }}
}});
</script>
</body></html>""")


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
# Each pack carries its own explicit "key" (e.g. "starter") set by admin.html â€”
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

# ---- Monthly subscription TIERS (2026-09, voice-cloning business model) ---
# Voice cloning is becoming subscription-only and slot-limited (Ali's
# request), so the single flat "Pro Monthly" plan (subscriptionName/
# subscriptionCredits/subscriptionPriceUsd below) is being generalized into
# several tiers, same array pattern as DEFAULT_PACKS above. "voice_slots" is
# a PERSISTENT cap -- how many named cloned voices a subscriber may have
# saved at once (see the new user_voices table), NOT a monthly-resetting
# counter. To get a new clone once full, the user deletes an existing saved
# voice to free a slot, or upgrades to a tier with more slots. This is
# separate from -- and in addition to -- the real ElevenLabs account-wide
# voice-cloning quota (service_usage_monitor's clone_ops_used/limit), which
# is still checked at clone time so this per-tier cap can never promise more
# than ElevenLabs' pool can actually deliver.
#
# These started as DRAFT numbers built off the old single plan ($29/mo, 4000
# credits) as the middle "Pro" tier, with a cheaper "Starter" and a pricier
# "Studio" bracketing it -- tune live in admin as needed.
# /api/billing/subscribe accepts an OPTIONAL plan_key naming one of these
# tiers (see _get_subscription_plan) -- both the public /pricing page
# (pricing.html's renderSubs()) and the in-app Buy modal (app.js's
# openBuyModal()) render one tile per tier here and pass its key on
# subscribe, so this array is what real subscribe clicks actually use as of
# 2026-09-27. The old flat subscriptionName/subscriptionCredits/
# subscriptionPriceUsd fields (admin's former "Monthly Subscription" card,
# removed from the UI but the fields themselves are kept, unmanaged, as
# hidden inputs) now ONLY matter as _get_subscription_plan's fallback for a
# profile with no subscription_plan_key at all -- i.e. someone who
# subscribed before tiers existed. Never touched by either page above.
DEFAULT_SUBSCRIPTION_PLANS = [
    {"key": "starter_monthly", "name": "Starter", "price_usd": 19.0, "credits_per_month": 2000,  "voice_slots": 1, "clones_per_month": 2,  "storage_gb": 0.2},
    {"key": "pro_monthly",     "name": "Pro",      "price_usd": 29.0, "credits_per_month": 4000,  "voice_slots": 3, "clones_per_month": 5,  "storage_gb": 0.3},
    {"key": "studio_monthly",  "name": "Studio",   "price_usd": 59.0, "credits_per_month": 10000, "voice_slots": 8, "clones_per_month": 15, "storage_gb": 0.6},
]

# ---- Storage per tier (2026-09) --------------------------------------------
# Finished dubbed files (mp3 / mp4 / lip-synced mp4) count toward each user's storage. Every monthly tier has a
# fixed number of GB ("storage_gb" on the tier, editable in admin); a pay-once buyer (no active subscription) gets
# PAYONCE_STORAGE_GB. When the storage is full the user must delete finished files (Account page) before anything new
# can be saved: see _storage_block() and the routes that call it.
PLAN_STORAGE_GB_DEFAULT = {"starter_monthly": 0.2, "pro_monthly": 0.3, "studio_monthly": 0.6}
STORAGE_FALLBACK_GB = 0.2          # a tier that has no storage number at all (an old custom tier)
try:
    PAYONCE_STORAGE_GB = float(os.environ.get("PAYONCE_STORAGE_GB", "0.1"))
except Exception:
    PAYONCE_STORAGE_GB = 0.1
_GB = 1024 ** 3


def _plan_storage_gb(plan):
    """GB of finished-file storage of one tier dict (admin value, else the built-in default for that key)."""
    try:
        v = (plan or {}).get("storage_gb")
        if v not in (None, ""):
            v = float(v)
            if v > 0:
                return v
    except (TypeError, ValueError):
        pass
    return PLAN_STORAGE_GB_DEFAULT.get(str((plan or {}).get("key") or "").strip().lower(), STORAGE_FALLBACK_GB)

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
    Reads from pricing_config table â€” admin panel is the single source of truth."""
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
    if cookie not in _valid_tokens and cookie not in _sessions:
        # Cold in-memory cache (server restarted since login) -- most
        # callers reach here only after AuthMiddleware's _is_logged_in
        # already restored this cookie, but a couple of endpoints (e.g.
        # /api/billing/checkout) are in PUBLIC_PATHS and call _current_uid
        # directly without going through that middleware first, so this
        # needs to be able to self-restore too.
        _restore_session_from_db(cookie)
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


def _billing_pause_response():
    from credit_billing import ready, UNAVAILABLE
    if not ready(_sb_rpc):
        return JSONResponse({"error": UNAVAILABLE}, status_code=503)
    return None


def _short_clone_settle(uid, debit_id, outcome, refund_due):
    from credit_billing import settle_clone
    return settle_clone(_sb_rpc, DATA_DIR, uid, debit_id, outcome, refund_due)


def _credit_recovery():
    from credit_billing import reconcile_clones
    while True:
        try:
            reconcile_clones(_sb_rpc, DATA_DIR)
        except Exception:
            print("[credits] settlement recovery will retry later")
        _time.sleep(60)


def _paid_uid(request: Request):
    """(uid, None) for a caller that has a real account, otherwise (None, ready 4xx/5xx response).
    Every step that costs credits (and real money at the AI providers) goes through this: a session without an
    account -- the shared-password login, or a login the server cannot verify -- is never let through to run it for free."""
    uid = _current_uid(request)
    if uid:
        paused = _billing_pause_response()
        return (None, paused) if paused is not None else (uid, None)
    cookie = request.cookies.get("session", "")
    if cookie and _valid_tokens.get(cookie):
        # there is an account login but the lookup failed just now (network, Supabase hiccup): nothing was charged
        return None, JSONResponse({"error": "We couldn't check your account just now. Please try again in a moment."}, status_code=503)
    return None, JSONResponse({"error": "Please log in with your account to continue."}, status_code=401)


# ---- job ownership for the short (Steps 1-7) flow -------------------------------
# Every short-flow job is a uuid4 that only its creator ever sees, but nothing
# used to CHECK that the caller is that creator. _job_guard is the one check
# every job-scoped route now runs: the id must look like a real id (no glob
# characters), and if we know who owns it (in memory from the upload, else from
# credit_spends) it must be the caller. A job nobody is recorded against yet
# (e.g. very first seconds after a server restart) is let through -- the id is
# a 122-bit random value, so it can't be guessed.
_job_owner: Dict[str, str] = {}
_job_owner_lookup: Dict[str, tuple] = {}


def _register_job_owner(job_id: str, uid):
    if job_id and uid:
        _job_owner[job_id] = str(uid)


def _job_guard(request: Request, job_id, allow_empty: bool = True):
    """None when the caller may use this job; otherwise a ready 4xx response."""
    if not job_id:
        if allow_empty:
            return None
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _JOB_ID_RE.fullmatch(str(job_id)):
        return JSONResponse({"error": "not found"}, status_code=404)
    uid = _current_uid(request)
    owner = _job_owner.get(job_id)
    if owner is None:
        now = time.time()
        cached = _job_owner_lookup.get(job_id)
        if cached and now - cached[1] < 120:
            owner = cached[0]
        else:
            owner = _uid_for_job(job_id)
            if len(_job_owner_lookup) > 4000:
                _job_owner_lookup.clear()
            _job_owner_lookup[job_id] = (owner, now)
    if owner and str(owner) != str(uid or ""):
        return JSONResponse({"error": "not found"}, status_code=404)
    return None


def _sb_rpc(function: str, args: dict, strict=False):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        if strict:
            raise RuntimeError('The credit service is unavailable.')
        return None
    body = json.dumps(args).encode("utf-8")
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/rpc/{function}", data=body, headers={
        "Content-Type": "application/json",
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception:
        if strict:
            raise
        return None


def set_credits(uid, new_amount):
    """Updates user's credits in profiles table."""
    from credit_billing import number
    try:
        new_amount = number(new_amount, "Credit balance", zero=True, integer=True)
    except ValueError:
        return False
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
    """Returns the user's total SPENDABLE balance: the permanent `credits`
    column (signup bonus, admin grants, one-time pack purchases -- never
    expires) PLUS `subscription_credits` (this billing cycle's subscription
    allowance -- forfeited and replaced at each renewal, Ali's 2026-09-27
    "use it or lose it" decision, see _grant_subscription_credits). Every
    existing balance check in this file (bal = get_credits(uid); if bal <
    cost: reject) keeps working unchanged, since it only ever needed the
    combined total. subscription_credits is treated as 0 -- never as
    "unknown" -- when the column doesn't exist yet (migration not run) or
    is null, so this is safe to call before that migration has been run.

    Callers that need to read/modify ONLY the permanent bucket (admin's
    manual grant/deduct/set -- see admin_adjust_credits) must use
    _get_permanent_credits below instead, never this function, or they'll
    silently double-count a user's subscription credits into the permanent
    bucket."""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select=credits,subscription_credits", headers={
            "apikey": SUPABASE_SERVICE_KEY,
        })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        if not rows:
            return None
        row = rows[0]
        return int(row.get("credits") or 0) + int(row.get("subscription_credits") or 0)
    except Exception as e:
        print(f"[credits] get_credits ERROR for {uid}: {e}")
        return None


def _get_permanent_credits(uid):
    """Reads ONLY the permanent, non-expiring `credits` column -- for
    callers that must read-modify-write that bucket specifically (admin's
    manual grant/deduct/set), never the combined spendable total
    get_credits returns. Returns None (not 0) on failure, same convention
    as get_credits."""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select=credits", headers={
            "apikey": SUPABASE_SERVICE_KEY,
        })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        return rows[0]["credits"] if rows else None
    except Exception as e:
        print(f"[credits] _get_permanent_credits ERROR for {uid}: {e}")
        return None


def deduct_credits(uid, amount, action="deduction", job_id=None, generated_seconds=None, operation_id=None):
    """Atomic full debit and history receipt. False=insufficient; None=unconfirmed.

    An explicit operation_id makes a replay safe across process restarts.
    Without one, a fresh user action gets an ID retained for transport retries.
    Missing migration pauses billing; the old split debit is never used.
    """
    from credit_billing import debit
    return debit(_sb_rpc, uid, amount, action, job_id, generated_seconds, operation_id)

def _fulfill_order(uid: str, session_id: str, credits: int):
    """Atomically record a paid pack and grant it once; failure remains retriable."""
    from credit_billing import fulfill
    try:
        return fulfill(_sb_rpc, uid, session_id, credits)
    except ValueError:
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
            return  # user switched videos â€” never charge for abandoned work

        cfg = _get_pricing_config()
        if kind == "transcribe":
            amount = int(cfg.get("transcribeCredits", 3))
        else:
            b = usage_bucket(job_id)
            eleven_chars = int(b.get("eleven_chars", 0) or 0)
            inworld_chars = int(b.get("inworld_chars", 0) or 0)
            # Fallback for anything that only ever wrote a combined total and
            # never split it into the two per-engine bucket keys (e.g.
            # tts_service.py's separate Gemini-inline generate path, which
            # still lumps everything into eleven_chars regardless of engine
            # -- unaffected by the Inworld swap, out of scope for that
            # change). If both per-engine counters are empty but the job's
            # own result reports a nonzero total, trust that total under
            # ElevenLabs' rate rather than silently charging 0.
            if eleven_chars == 0 and inworld_chars == 0:
                reported_total = int(result.get("eleven_credits_used", 0) or 0)
                if reported_total > 0:
                    eleven_chars = reported_total

            gemini_usd = (
                (int(b.get("gemini_in", 0)) + int(b.get("audio_sec", 0) * 258))
                / 1e6
                * 0.30
                + (int(b.get("gemini_out", 0)) + int(b.get("gemini_thoughts", 0))) / 1e6 * 2.50
            )

            eleven_rate = int(cfg.get("charsPerCredit", 60)) or 60
            inworld_rate = int(cfg.get("inworldCharsPerCredit", 60)) or 60

            # Each engine's characters are ceil'd against its OWN rate
            # separately (rather than combining both into one rate) since a
            # single job can mix ElevenLabs and Inworld speakers -- each
            # voice keeps using whichever engine actually created it (see
            # _voice_engines_for_ids), so this is the only way to charge
            # each engine's own real, admin-configured cost.
            amount = (
                (math.ceil(eleven_chars / eleven_rate) if eleven_chars else 0)
                + (math.ceil(inworld_chars / inworld_rate) if inworld_chars else 0)
                + max(1, math.ceil(gemini_usd * _gemini_cpc(cfg.get("geminiCreditsPerCent")) / 0.01))
            )

        # Duration (seconds) of the actual dubbed audio produced by this job â€”
        # only meaningful for "generate" (merge/transcribe don't produce new
        # generated audio). Recorded to credit_spends for the admin dashboard's
        # generated-minutes tracking.
        gen_seconds = result.get("final_duration") if kind == "generate" else None
        new_balance = deduct_credits(uid, amount, kind, job_id, gen_seconds)

        if not debit_confirmed(new_balance):
            _job_charges[job_id] = {"credits_charged": 0, "payment_pending": new_balance is None}
            return

        _job_charges[job_id] = {
            "credits_charged": amount,
            "balance_after": new_balance,
        }

    threading.Thread(target=_run, daemon=True).start()


# ---------- Stripe ----------

def _public_origin(request: Request) -> str:
    """Base URL used to build Stripe's success/cancel/return URLs.
    Behind Railway's proxy the app itself only sees plain http, so
    request.base_url came back as "http://lisanai.org" and customers were
    sent back from Stripe to an http:// address (seen in a real test
    checkout, 2026-09-28). Every real host is https, so force that; only a
    local development host (localhost / 127.0.0.1) keeps the scheme it was
    reached with. The host part is still taken from the request, so the
    lisanai.org domain and the Railway domain each get their own correct
    address."""
    base = str(request.base_url).rstrip("/")
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(base)
        host = (parts.hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost"):
            return base
        return "https://" + parts.netloc
    except Exception:
        return base


@app.post("/api/billing/checkout")
def billing_checkout(payload: dict, request: Request):
    if not stripe or not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "Payments are temporarily unavailable. Please try again later."}, status_code=503)
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Login required to buy credits."}, status_code=401)
    paused = _billing_pause_response()
    if paused is not None:
        return paused
    pack_key = payload.get("pack", "")
    pack = get_packs().get(pack_key)
    if not pack:
        return JSONResponse({"error": "Unknown pack."}, status_code=400)
    stripe.api_key = STRIPE_SECRET_KEY
    origin = _public_origin(request)
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
            # A real numbered invoice (PDF) for each credit pack, not just a
            # bare receipt. The invoice.paid webhook ignores it: it has no
            # subscription id, so no credits are granted from it twice.
            invoice_creation={"enabled": True},
            success_url=origin + "/app#credits-purchased",
            cancel_url=origin + "/app",
        )
    except Exception as e:
        return _stripe_fail(e, "billing")
    return {"url": session.url}


def _get_subscription_plan(plan_key):
    """Looks up one tier by key from pricing_config's subscriptionPlans
    array (admin's "Subscription Tiers" table). Falls back to the single
    legacy plan -- subscriptionName/subscriptionCredits/subscriptionPriceUsd
    -- whenever plan_key is empty, unrecognized, or the array itself is
    empty (nothing saved in admin yet), so every existing call site that
    doesn't pass a plan_key (an old cached frontend, a subscribe link with
    no query string) keeps working exactly as before this function existed.
    The fallback's key is "pro_monthly" -- same key as the draft tier of the
    same name/price already in DEFAULT_SUBSCRIPTION_PLANS -- so a profile
    stamped via this path is still identifiable as that tier later.

    clones_per_month (2026-09-27) is a SEPARATE cap from voice_slots: slots
    are how many named voices a subscriber can keep saved at once (frees up
    on delete), while clones_per_month is how many NEW clone operations
    that tier allows per billing cycle, regardless of deletions -- mirrors
    ElevenLabs' own account-wide clone_ops quota (see _authorize_new_clones)
    but scoped per user/tier instead of shared across everyone. Left blank
    or 0 in admin means "no separate cap" (None here) -- unlimited except
    for the slot cap and the real ElevenLabs quota, i.e. exactly today's
    behavior -- so this field is additive/inert until Ali actually sets a
    number for a tier, same as voice_slots was when tiers were first added."""
    cfg = _get_pricing_config()
    plans = cfg.get("subscriptionPlans") or []
    if plan_key:
        for p in plans:
            if str(p.get("key") or "").strip().lower() == str(plan_key).strip().lower():
                raw_clones = p.get("clones_per_month")
                clones_per_month = None
                if raw_clones not in (None, ""):
                    try:
                        clones_per_month = int(raw_clones) or None
                    except (TypeError, ValueError):
                        clones_per_month = None
                # Inworld has no clone-count limit (unlike ElevenLabs' shared
                # monthly voice add/edit quota), so while Inworld is the
                # active engine the per-tier monthly clone cap is switched
                # off everywhere -- the clone gate, the Account page's clone
                # counter -- without touching the numbers saved in admin.
                # Switching the admin Voice Engine back to ElevenLabs brings
                # them back automatically.
                if _active_voice_engine() == "inworld":
                    clones_per_month = None
                return {
                    "key": p.get("key"),
                    "name": p.get("name") or "Pro",
                    "credits": int(p.get("credits_per_month") or 0),
                    "price_usd": float(p.get("price_usd") or 0),
                    "voice_slots": int(p.get("voice_slots") or 0),
                    "clones_per_month": clones_per_month,
                    "storage_gb": _plan_storage_gb(p),
                }
    return {
        "key": "pro_monthly",
        "name": cfg.get("subscriptionName") or "Pro Monthly",
        "credits": int(cfg.get("subscriptionCredits") or 4000),
        "price_usd": float(cfg.get("subscriptionPriceUsd") or 29.0),
        "voice_slots": 0,
        "clones_per_month": None,
        "storage_gb": _plan_storage_gb({"key": "pro_monthly"}),
    }


@app.post("/api/billing/subscribe")
def billing_subscribe(request: Request, plan_key: str = ""):
    """Monthly subscription checkout (Ali's request, 2026-09-26; generalized
    to multiple tiers 2026-09-27). Uses inline price_data with recurring
    set, same as billing_checkout's inline price_data, so it never needs a
    Product/Price pre-created in the Stripe dashboard. plan_key is an
    OPTIONAL query param (?plan_key=starter_monthly) -- see
    _get_subscription_plan's docstring for why omitting it (as every
    existing caller still does today) is safe and falls back to the single
    legacy plan. subscription_data.metadata carries uid AND plan_key so
    customer.subscription.* / invoice.paid webhook events (which never see
    our uid directly, only Stripe's own ids) can still be tied back to a
    user and their tier immediately, in addition to the
    stripe_subscription_id / subscription_plan_key we store on profiles
    once checkout.session.completed fires below."""
    if not stripe or not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "Payments are temporarily unavailable. Please try again later."}, status_code=503)
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Login required to subscribe."}, status_code=401)
    paused = _billing_pause_response()
    if paused is not None:
        return paused
    # A second checkout would create a SECOND, parallel subscription (double
    # billing). Existing subscribers change tier from the Buy box, and
    # cancel/renew from the Account page.
    if _subscription_active(uid):
        return JSONResponse({"error": "You already have an active subscription. Open the Buy menu in the app to change your plan, or the Account page to cancel or renew it."}, status_code=409)
    plan = _get_subscription_plan(plan_key)
    plan_name = plan["name"]
    plan_credits = plan["credits"]
    plan_price = plan["price_usd"]
    _pause = _subscription_credit_pause()
    if _pause is not None:
        return _pause
    stripe.api_key = STRIPE_SECRET_KEY
    origin = _public_origin(request)
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            client_reference_id=uid,
            metadata={"uid": uid, "credits": str(plan_credits), "plan_key": plan["key"]},
            subscription_data={"metadata": {"uid": uid, "credits": str(plan_credits), "plan_key": plan["key"]}},
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "recurring": {"interval": "month"},
                    "product_data": {"name": f"Lisan AI {plan_name} - {plan_credits} credits/month"},
                    "unit_amount": int(round(plan_price * 100)),
                },
                "quantity": 1,
            }],
            success_url=origin + "/app#subscription-active",
            cancel_url=origin + "/app",
        )
    except Exception as e:
        return _stripe_fail(e, "billing")
    return {"url": session.url}


@app.post("/api/billing/portal")
def billing_portal(request: Request):
    """Opens Stripe's own hosted Billing Portal so a subscriber can update
    their card or cancel -- required so cancelling is genuinely easy (not
    just 'email us'), which matters both for user trust and, given Lisan
    AI's EU customers, EU distance-selling rules on recurring subscriptions.
    We never build our own cancel/payment-method UI; Stripe's portal handles
    that entirely off our servers."""
    if not stripe or not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "Payments are temporarily unavailable. Please try again later."}, status_code=503)
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Login required."}, status_code=401)
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return JSONResponse({"error": "This feature is temporarily unavailable. Please try again later."}, status_code=503)
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select=stripe_customer_id",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        customer_id = rows[0].get("stripe_customer_id") if rows else None
    except Exception:
        customer_id = None
    if not customer_id:
        return JSONResponse({"error": "No active subscription found for this account."}, status_code=404)
    stripe.api_key = STRIPE_SECRET_KEY
    origin = _public_origin(request)
    try:
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=origin + "/account",
        )
    except Exception as e:
        return _stripe_fail(e, "billing")
    return {"url": portal.url}


@app.post("/api/billing/cancel")
def billing_cancel(request: Request):
    """Same Stripe-hosted Billing Portal as /api/billing/portal above, but
    deep-linked straight to the portal's built-in "cancel this subscription"
    flow (Stripe's documented flow_data[type]=subscription_cancel) instead
    of the general account-management screen -- Ali asked for an explicit
    Cancel button on the account page, separate from "Manage Subscription".
    Still entirely Stripe's own hosted flow: we never implement cancellation
    logic ourselves, same reasoning as /api/billing/portal (EU
    distance-selling rules want an easy, unambiguous cancel path)."""
    if not stripe or not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "Payments are temporarily unavailable. Please try again later."}, status_code=503)
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Login required."}, status_code=401)
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return JSONResponse({"error": "This feature is temporarily unavailable. Please try again later."}, status_code=503)
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select=stripe_customer_id,stripe_subscription_id",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        customer_id = rows[0].get("stripe_customer_id") if rows else None
        subscription_id = rows[0].get("stripe_subscription_id") if rows else None
    except Exception:
        customer_id = None
        subscription_id = None
    if not customer_id or not subscription_id:
        return JSONResponse({"error": "No active subscription found for this account."}, status_code=404)
    stripe.api_key = STRIPE_SECRET_KEY
    origin = _public_origin(request)
    # Ask Stripe first: a subscription that is ALREADY cancelled (e.g. from
    # the Stripe dashboard, or an earlier click here) can't be cancelled
    # again -- Stripe's portal answers with an error ("already set to be
    # canceled at period end"). Sync our profile with the real state and
    # tell the page instead, so it can show when the plan ends.
    try:
        sub = stripe.Subscription.retrieve(subscription_id)
        synced = _sync_subscription_to_profile(uid, sub)
        if synced["status"] == "none":
            return JSONResponse({"error": "This subscription has already ended."}, status_code=404)
        if synced["cancel_at_period_end"]:
            return {"already_canceling": True, "ends_at": synced["period_end"] or sub.get("cancel_at")}
    except Exception as e:
        return _stripe_fail(e, "billing")
    try:
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=origin + "/account#subscription",
            flow_data={
                "type": "subscription_cancel",
                "subscription_cancel": {"subscription": subscription_id},
                "after_completion": {
                    "type": "redirect",
                    "redirect": {"return_url": origin + "/account#subscription"},
                },
            },
        )
    except Exception as e:
        return _stripe_fail(e, "billing")
    return {"url": portal.url}


def _profile_subscription_id(uid):
    """profiles.stripe_subscription_id for one user ("" when none/unreadable)."""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return ""
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select=stripe_subscription_id",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        return (rows[0].get("stripe_subscription_id") if rows else "") or ""
    except Exception:
        return ""


@app.post("/api/billing/resume")
def billing_resume(request: Request):
    """Undoes a pending cancellation ("Renew" on the Account page): the
    subscription was set to end at the end of the paid period, this sets it
    to keep renewing again. No charge happens now -- it just stops the
    scheduled end. Only works while the subscription is still active (before
    the period end); afterwards the user subscribes again from the Buy box."""
    if not stripe or not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "Payments are temporarily unavailable. Please try again later."}, status_code=503)
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Login required."}, status_code=401)
    subscription_id = _profile_subscription_id(uid)
    if not subscription_id:
        return JSONResponse({"error": "No active subscription found for this account."}, status_code=404)
    stripe.api_key = STRIPE_SECRET_KEY
    try:
        sub = stripe.Subscription.retrieve(subscription_id)
        if (sub.get("status") or "") in ("canceled", "incomplete_expired"):
            _sync_subscription_to_profile(uid, sub)
            return JSONResponse({"error": "This subscription has already ended. Please subscribe again."}, status_code=409)
        if sub.get("cancel_at_period_end"):
            sub = stripe.Subscription.modify(subscription_id, cancel_at_period_end=False)
        elif sub.get("cancel_at"):
            # Cancelled with a specific end date instead of "at period end"
            # -- not something this button can safely undo.
            return JSONResponse({"error": "Please use Manage Subscription to renew this subscription."}, status_code=409)
        synced = _sync_subscription_to_profile(uid, sub)
    except Exception as e:
        return _stripe_fail(e, "billing")
    return {"ok": True, "status": synced["status"], "cancel_at_period_end": synced["cancel_at_period_end"]}


@app.post("/api/billing/refresh_subscription")
def billing_refresh_subscription(request: Request):
    """Re-reads this user's subscription from Stripe and stores its current
    status / renewal date / cancelled flag on the profile. The Account page
    calls it on load, so it is right even if a webhook was late or missed
    (e.g. cancelling from the Stripe dashboard, or returning from the Stripe
    portal before its webhook arrived). No-op for users with no subscription."""
    if not stripe or not STRIPE_SECRET_KEY:
        return {"ok": False}
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Login required."}, status_code=401)
    subscription_id = _profile_subscription_id(uid)
    if not subscription_id:
        return {"ok": True, "subscription": False}
    stripe.api_key = STRIPE_SECRET_KEY
    try:
        sub = stripe.Subscription.retrieve(subscription_id)
        synced = _sync_subscription_to_profile(uid, sub)
    except Exception as e:
        print("[subscription] refresh failed:", e)
        return {"ok": False}
    return {"ok": True, "subscription": True, "status": synced["status"], "cancel_at_period_end": synced["cancel_at_period_end"]}


def _plan_by_key_strict(plan_key):
    """The configured tier whose key equals plan_key, or None. Unlike
    _get_subscription_plan this never falls back to another plan."""
    plan = _get_subscription_plan(plan_key)
    if plan_key and str(plan.get("key") or "").strip().lower() == str(plan_key).strip().lower():
        return plan
    return None


def _stripe_monthly_price_data(product_id, plan):
    return {
        "currency": "usd",
        "product": product_id,
        "recurring": {"interval": "month"},
        "unit_amount": int(round(float(plan["price_usd"]) * 100)),
    }


@app.post("/api/billing/change_plan")
def billing_change_plan(request: Request, plan_key: str = ""):
    """Switch an existing subscriber to another tier (Buy box).

    UPGRADE (target costs more than what they pay now): starts immediately.
    Stripe charges the prorated difference for the rest of the period right
    away; their subscription credits are set to the new tier's full monthly
    amount (same "set, not add" rule as every renewal). The profile's plan
    key is switched BEFORE the Stripe call so the invoice.paid webhook for
    that charge already grants the NEW tier's credits; it is switched back if
    Stripe refuses (e.g. card declined). The credit grant is also done here
    when the invoice is already paid -- idempotent per invoice id, so the
    webhook arriving too can never double-grant.

    DOWNGRADE (cheaper or equal price): scheduled for the next renewal. The
    Stripe price changes now with no proration (so the next invoice is the
    lower price), while the tier/credits on the profile stay as they are and
    switch when the renewal invoice is paid (see invoice.paid). Choosing the
    current tier again while a downgrade is pending cancels the schedule.

    Refused while the subscription is cancelled (renew it first) and while a
    downgrade is already scheduled (undo it first) -- one change at a time
    keeps the proration maths unambiguous."""
    if not stripe or not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "Payments are temporarily unavailable. Please try again later."}, status_code=503)
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Login required."}, status_code=401)
    paused = _billing_pause_response()
    if paused is not None:
        return paused
    target = _plan_by_key_strict(plan_key)
    if not target:
        return JSONResponse({"error": "That plan doesn't exist."}, status_code=400)
    prof = _read_subscription_profile(uid)
    if (prof.get("subscription_status") or "") != "active":
        return JSONResponse({"error": "You don't have an active subscription to change."}, status_code=409)
    if prof.get("subscription_cancel_at_period_end"):
        return JSONResponse({"error": "Your subscription is cancelled. Renew it from your Account page before changing plans."}, status_code=409)
    subscription_id = _profile_subscription_id(uid)
    if not subscription_id:
        return JSONResponse({"error": "No active subscription found for this account."}, status_code=404)
    current = _get_subscription_plan(prof.get("subscription_plan_key") or "")
    pending_key = (prof.get("subscription_pending_plan_key") or "").strip()
    target_is_current = str(target["key"]).lower() == str(current["key"]).lower()

    _pause = _subscription_credit_pause()
    if _pause is not None:
        return _pause
    stripe.api_key = STRIPE_SECRET_KEY
    try:
        sub = stripe.Subscription.retrieve(subscription_id)
        if (sub.get("status") or "") != "active":
            return JSONResponse({"error": "Your subscription isn't active right now."}, status_code=409)
        if sub.get("cancel_at_period_end") or sub.get("cancel_at"):
            _sync_subscription_to_profile(uid, sub)
            return JSONResponse({"error": "Your subscription is cancelled. Renew it from your Account page before changing plans."}, status_code=409)
        items = (sub.get("items") or {}).get("data") or []
        if len(items) != 1:
            return JSONResponse({"error": "This subscription can't be changed automatically. Please contact support."}, status_code=409)
        item = items[0]
        current_cents = int(((item.get("price") or {}).get("unit_amount")) or 0)
        target_cents = int(round(float(target["price_usd"]) * 100))

        # --- undo a scheduled downgrade ("keep my current plan")
        if pending_key:
            if not target_is_current:
                return JSONResponse({"error": "A plan change is already scheduled for your next renewal. Choose your current plan to cancel it first."}, status_code=409)
            product = stripe.Product.create(name=f"Lisan AI {current['name']} - {current['credits']} credits/month")
            stripe.Subscription.modify(
                subscription_id,
                items=[{"id": item["id"], "price_data": _stripe_monthly_price_data(product["id"], current)}],
                proration_behavior="none",
            )
            _set_subscription_pending_plan(uid, None)
            return {"ok": True, "mode": "reverted", "plan": current["name"]}

        if target_is_current:
            return JSONResponse({"error": "You're already on this plan."}, status_code=409)

        product = stripe.Product.create(name=f"Lisan AI {target['name']} - {target['credits']} credits/month")

        # --- upgrade: now, prorated difference charged immediately
        if target_cents > current_cents:
            _set_subscription_plan_key(uid, target["key"])
            try:
                updated = stripe.Subscription.modify(
                    subscription_id,
                    items=[{"id": item["id"], "price_data": _stripe_monthly_price_data(product["id"], target)}],
                    proration_behavior="always_invoice",
                    payment_behavior="error_if_incomplete",
                    metadata={"uid": uid, "credits": str(target["credits"]), "plan_key": target["key"]},
                )
            except Exception:
                _set_subscription_plan_key(uid, current["key"])
                raise
            granted = None
            try:
                inv_id = _as_id(updated.get("latest_invoice"))
                if inv_id:
                    inv = stripe.Invoice.retrieve(inv_id)
                    if (inv.get("status") or "") == "paid":
                        period_start, period_end = _invoice_billing_period(inv)
                        granted = _grant_subscription_credits(uid, inv_id, int(target["credits"]), period_start, period_end)
            except Exception as ex:
                print("[subscription] upgrade credit grant deferred to webhook:", ex)
            _sync_subscription_to_profile(uid, updated)
            print("[subscription] upgraded uid=", uid, "to", target["key"], "grant=", granted)
            _pending = _subscription_grant_response(granted)
            if _pending is not None:
                return _pending
            return {"ok": True, "mode": "upgraded", "plan": target["name"], "credits": int(target["credits"])}

        # --- downgrade (or same price): at the next renewal.
        # Record the schedule FIRST and refuse if it can't be stored (column
        # not migrated): otherwise Stripe would bill the lower price at
        # renewal while the profile kept granting the higher tier's credits.
        if not _set_subscription_pending_plan(uid, target["key"]):
            return JSONResponse({"error": "We can't schedule a plan change right now. Please try again later."}, status_code=503)
        try:
            stripe.Subscription.modify(
                subscription_id,
                items=[{"id": item["id"], "price_data": _stripe_monthly_price_data(product["id"], target)}],
                proration_behavior="none",
            )
        except Exception:
            _set_subscription_pending_plan(uid, None)
            raise
        print("[subscription] downgrade scheduled uid=", uid, "to", target["key"])
        return {"ok": True, "mode": "scheduled", "plan": target["name"],
                "effective": prof.get("subscription_current_period_end") or None}
    except Exception as e:
        return _stripe_fail(e, "billing")


@app.post("/api/account/delete")
def delete_account(request: Request, response: Response):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Login required."}, status_code=401)
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return JSONResponse({"error": "This feature is temporarily unavailable. Please try again later."}, status_code=503)
    with longdub_service.account_lock(uid):
        try:
            longdub_service.delete_account_jobs(uid)
        except ValueError as ex:
            return JSONResponse({"error": customer_message(str(ex))}, status_code=409)
        except Exception:
            return JSONResponse({"error": "Your project cleanup could not finish. Your account has not been deleted. Please try again later."}, status_code=503)
        return _delete_account_run(request, response, uid)


def _delete_account_run(request: Request, response: Response, uid):
    """Permanently deletes the caller's account -- irreversible, as warned
    on the account page's confirmation dialog before this is ever called.
    In order:
      1. Cancel any active Stripe subscription immediately (not at period
         end -- once the account is gone there's nothing left to bill for).
      2. Delete this user's finished job output files from disk (same
         suffixes /api/my_jobs already tracks).
      3. Explicitly delete the profiles row via PostgREST -- done even
         though Supabase's recommended profiles-table setup has an
         ON DELETE CASCADE FK to auth.users, so this doesn't silently
         no-op if that FK was never actually added.
      4. Delete the Supabase Auth user itself via the GoTrue Admin API
         (DELETE /auth/v1/admin/users/{id}).
      5. Clear the session cookie, same as /api/logout.
    credit_spends/credit_orders/subscription_invoices rows are intentionally
    left alone -- they're financial/audit records, not account data, and
    privacy.html already tells users that deleting their account removes
    "stored account data", not transaction history. Every step is
    best-effort logged and continues past a single failure except the auth
    delete itself, so a partial failure never leaves the account half-open
    with no way for the user to know."""
    sb_hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}

    # 1) Cancel any active Stripe subscription immediately.
    if stripe and STRIPE_SECRET_KEY:
        try:
            req = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select=stripe_subscription_id",
                headers=sb_hdrs)
            with urllib.request.urlopen(req, timeout=10) as r:
                rows = json.load(r)
            sub_id = rows[0].get("stripe_subscription_id") if rows else None
            if sub_id:
                stripe.api_key = STRIPE_SECRET_KEY
                stripe.Subscription.delete(sub_id)
        except Exception as e:
            print(f"[delete-account] subscription cancel failed for {uid}: {e}")

    # 2) Delete this user's finished job output files from disk.
    try:
        url = f"{SUPABASE_URL}/rest/v1/credit_spends?uid=eq.{uid}&select=job_id&limit=1000"
        req = urllib.request.Request(url, headers=sb_hdrs)
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.load(r) or []
        job_ids = {row.get("job_id") for row in rows if row.get("job_id")}
        for job_id in job_ids:
            if "/" in job_id or "\\" in job_id or ".." in job_id:
                continue
            for suffix in _MY_JOB_FILE_SUFFIXES.values():
                p = OUTPUT_DIR / f"{job_id}{suffix}"
                try:
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass
    except Exception as e:
        print(f"[delete-account] file cleanup failed for {uid}: {e}")

    # 3) Explicitly delete the profiles row.
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}",
            headers={**sb_hdrs, "Prefer": "return=minimal"},
            method="DELETE")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[delete-account] profile row delete failed for {uid}: {e}")

    # 4) Delete the Supabase Auth user itself. If this fails, the account
    # data above is already gone but the login isn't -- tell the user
    # plainly instead of reporting success on a half-finished deletion.
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/auth/v1/admin/users/{uid}",
            headers=sb_hdrs,
            method="DELETE")
        with urllib.request.urlopen(req, timeout=10) as r:
            pass
    except Exception as e:
        print(f"[delete-account] auth user delete failed for {uid}: {e}")
        return JSONResponse({"error": "Your data was cleared, but we couldn't fully remove your login. Please contact support to finish closing your account."}, status_code=502)

    # 5) Clear the session cookie, same as /api/logout.
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/billing/sync")
def billing_sync(request: Request):
    if not stripe or not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "Payments are temporarily unavailable. Please try again later."}, status_code=503)
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    stripe.api_key = STRIPE_SECRET_KEY
    added = 0
    try:
        sessions = stripe.checkout.Session.list(limit=100)
        for s in sessions.data:
            if s.get("client_reference_id") != uid:
                continue
            # Subscription-mode sessions are reconciled by the invoice.paid
            # webhook (_grant_subscription_credits), never here -- this
            # one-time _fulfill_order path would otherwise also match a
            # subscription checkout (its metadata carries "credits" too, for
            # the webhook's benefit) and hand out an extra one-time lump on
            # top of the correct recurring grant.
            if s.get("mode") == "subscription":
                continue
            if s.get("payment_status") == "paid":
                credits = int((s.get("metadata") or {}).get("credits", 0))
                res = _fulfill_order(uid, s.get("id", ""), credits)
                if res == "needs-review":
                    return JSONResponse({"error": "A previous credit purchase needs review. Please contact support."}, status_code=503)
                if res is None:
                    return JSONResponse({"error": "Your credit purchase is still pending. Please try again later."}, status_code=503)
                if type(res) is int:
                    added += credits
    except Exception as e:
        print(f"[billing-sync] failed: {e}")
        return JSONResponse({"error": "We couldn't check your payments right now. Please try again shortly."}, status_code=502)
    profile = _read_subscription_profile(uid)
    if profile is None:
        return JSONResponse({"error": "Your subscription could not be checked. Please try again later."}, status_code=503)
    sid = profile.get("stripe_subscription_id")
    if sid:
        try:
            invoices = stripe.Invoice.list(subscription=sid, status="paid", limit=1)
            for invoice in invoices.data:
                if _invoice_subscription_id(invoice) != sid:
                    return JSONResponse({"error": "This invoice needs review before delivering credits."}, status_code=503)
                key = profile.get("subscription_plan_key") or ""
                pending_key = profile.get("subscription_pending_plan_key") or ""
                if pending_key and invoice.get("billing_reason") == "subscription_cycle":
                    key = pending_key
                start, end = _invoice_billing_period(invoice)
                granted = _grant_subscription_credits(uid, invoice.get("id") or "", _get_subscription_plan(key)["credits"], start, end)
                paused = _subscription_grant_response(granted)
                if paused is not None:
                    return paused
                if key == pending_key and pending_key:
                    if not _set_subscription_plan_key(uid, key) or not _set_subscription_pending_plan(uid, None):
                        return JSONResponse({"error": "The subscription plan update is pending. Please retry later."}, status_code=503)
        except Exception:
            return JSONResponse({"error": "Subscription credit delivery is pending. Please try again later."}, status_code=503)
    return {"added_sessions_credits": added}

def _as_id(v):
    """Stripe fields are either an id string or an expanded object."""
    if isinstance(v, dict):
        return v.get("id") or ""
    return v or ""

def _invoice_subscription_id(invoice):
    """The subscription an invoice belongs to.

    Older Stripe API versions put it at invoice.subscription; API versions
    from 2025-03-31 (basil) removed that field and moved it to
    invoice.parent.subscription_details.subscription (and, per line item, to
    line.parent.subscription_item_details.subscription). The account's
    webhook endpoint decides which shape arrives, so accept all of them.
    """
    sid = _as_id(invoice.get("subscription"))
    if sid:
        return sid
    parent = invoice.get("parent") or {}
    sid = _as_id((parent.get("subscription_details") or {}).get("subscription"))
    if sid:
        return sid
    for line in ((invoice.get("lines") or {}).get("data") or []):
        lparent = line.get("parent") or {}
        sid = _as_id((lparent.get("subscription_item_details") or {}).get("subscription"))
        if sid:
            return sid
        sid = _as_id(line.get("subscription"))
        if sid:
            return sid
    return ""

def _invoice_billing_period(invoice):
    """Use service periods on subscription lines, never the invoice accrual dates."""
    from credit_actions import period_value
    sid = _invoice_subscription_id(invoice)
    found = set()
    lines = invoice.get("lines") or {}
    if lines.get("has_more"):
        raise ValueError("The full invoice must be checked before delivering credits.")
    for line in lines.get("data") or []:
        parent = line.get("parent") or {}
        details = parent.get("subscription_item_details") or {}
        line_sid = _as_id(line.get("subscription") or details.get("subscription"))
        if not (line.get("type") == "subscription" or details or (sid and line_sid == sid)):
            continue
        if line_sid and sid and line_sid != sid:
            continue
        amount = line.get("amount")
        if amount is not None and (isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0):
            continue
        period = line.get("period") or {}
        start, end = period_value(period.get("start")), period_value(period.get("end"))
        found.add((start, end))
    if len(found) != 1:
        raise ValueError("The invoice billing period needs review before delivering credits.")
    return next(iter(found))


def _subscription_credit_pause():
    from credit_actions import subscription_ready
    if not subscription_ready(_sb_rpc):
        return JSONResponse({"error": "Subscription credit delivery is temporarily paused. Please try again later."}, status_code=503)
    return None


def _subscription_grant_response(result):
    if not isinstance(result, dict) or result.get("status") != "done":
        status = (result or {}).get("status") if isinstance(result, dict) else ""
        message = ("This subscription invoice needs review. No allowance was replaced. Please contact support."
                   if status in ("mismatch", "stale", "legacy_review") else
                   "Subscription credit delivery is temporarily paused. Please retry later.")
        return JSONResponse({"error": message}, status_code=409 if status in ("mismatch", "stale") else 503)
    return None


def _subscription_period_end(sub):
    """Current period end (unix ts) of a Subscription object.

    Removed from the Subscription itself in API 2025-03-31 (basil); it now
    lives on each subscription item. Prefer the old field, else the latest
    item period end.
    """
    ts = sub.get("current_period_end")
    if ts:
        return ts
    try:
        best = 0
        for item in ((sub.get("items") or {}).get("data") or []):
            best = max(best, int(item.get("current_period_end") or 0))
        return best or None
    except Exception:
        return None

@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    try:
        print("[stripe-webhook] received request")
        if not stripe or not STRIPE_WEBHOOK_SECRET:
            print("[stripe-webhook] NOT CONFIGURED: stripe=", bool(stripe), "secret=", bool(STRIPE_WEBHOOK_SECRET))
            return JSONResponse({"error": "Payments are temporarily unavailable. Please try again later."}, status_code=503)
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
            if session.get("mode") == "subscription":
                # Just record the subscription + customer id here -- credits
                # are granted by invoice.paid below (fires for this first
                # invoice too, not just renewals), never here, so a
                # subscription checkout never double-grants.
                sub_id = session.get("subscription") or ""
                cust_id = session.get("customer") or ""
                plan_key = (session.get("metadata") or {}).get("plan_key") or ""
                print("[stripe-webhook] subscription checkout uid=", uid, "sub=", sub_id, "customer=", cust_id, "plan_key=", plan_key)
                if uid and sub_id:
                    _set_subscription_fields(
                        uid,
                        subscription_status="active",
                        stripe_subscription_id=sub_id,
                        stripe_customer_id=cust_id,
                    )
                    if plan_key:
                        _set_subscription_plan_key(uid, plan_key)
                    _set_subscription_pending_plan(uid, None)
            else:
                credits = int((session.get("metadata") or {}).get("credits", 0))
                print("[stripe-webhook] uid=", uid, "credits=", credits)
                if session.get("payment_status") == "unpaid":
                    print("[stripe-webhook] session not paid yet, not granting credits:", session.get("id"))
                    credits = 0
                if uid and credits:
                    print("[stripe-webhook] SUPABASE_SERVICE_KEY present:", bool(SUPABASE_SERVICE_KEY))
                    res = _fulfill_order(uid, session.get("id", ""), credits)
                    print("[stripe] fulfill result:", res)
                    if res is None or res == "needs-review":
                        return JSONResponse({"error": "Credit delivery is pending. Please retry later."}, status_code=503)
        elif etype == "invoice.paid":
            # Fires for the subscription's very first charge AND every
            # monthly renewal -- the one place credits actually get granted
            # for a subscription, idempotent per invoice id (see
            # _grant_subscription_credits), so a webhook retry never
            # double-grants a period's credits.
            invoice = event["data"]["object"]
            sub_id = _invoice_subscription_id(invoice)
            invoice_id = invoice.get("id") or ""
            uid = _uid_for_subscription(sub_id) if sub_id else None
            plan_key = ""
            recovered_sub = None
            if not uid and sub_id:
                # Stripe doesn't guarantee checkout.session.completed (which
                # is what normally stores stripe_subscription_id on the
                # profile) arrives before this invoice.paid for the very
                # first charge -- if the profile lookup above came up empty
                # because of that race, fall back to the uid we stamped
                # directly onto the Subscription's own metadata at creation
                # (see /api/billing/subscribe's subscription_data.metadata),
                # and self-heal the profile fields while we're at it so the
                # normal lookup path works for every event after this one.
                try:
                    stripe.api_key = STRIPE_SECRET_KEY
                    sub_obj = stripe.Subscription.retrieve(sub_id)
                    uid = (sub_obj.get("metadata") or {}).get("uid")
                    plan_key = (sub_obj.get("metadata") or {}).get("plan_key") or ""
                    if uid:
                        recovered_sub = sub_obj
                except Exception as e:
                    print("[stripe-webhook] subscription metadata fallback failed:", e)
            print("[stripe-webhook] invoice.paid sub=", sub_id, "uid=", uid, "plan_key=", plan_key)
            if uid:
                # Normal path (uid found via the profile lookup, not the
                # Stripe-metadata fallback above): plan_key is still "" here,
                # so read the tier we stamped on the profile at checkout --
                # this is what makes every RENEWAL grant the right tier's
                # credits, not just the first invoice. Falls back to the
                # legacy flat plan automatically if the column isn't there
                # yet or the profile has no plan_key (pre-existing
                # subscriber from before tiers existed).
                # A scheduled downgrade takes effect on the first RENEWAL
                # invoice after it was requested (Stripe already switched the
                # price for that invoice; the credits and the tier on the
                # profile switch here, at the same moment).
                pending_key = ""
                if invoice.get("billing_reason") == "subscription_cycle":
                    pending_key = (_read_subscription_profile(uid) or {}).get("subscription_pending_plan_key") or ""
                    if pending_key:
                        plan_key = pending_key
                if not plan_key:
                    plan_key = _get_profile_plan_key(uid)
                credits = _get_subscription_plan(plan_key)["credits"]
                try:
                    period_start, period_end = _invoice_billing_period(invoice)
                except ValueError:
                    return JSONResponse({"error": "The invoice billing period needs review before delivering credits."}, status_code=503)
                res = _grant_subscription_credits(uid, invoice_id, credits, period_start, period_end)
                pending = _subscription_grant_response(res)
                if pending is not None:
                    return pending
                if recovered_sub is not None:
                    if not _set_subscription_fields(uid, subscription_status="active", stripe_subscription_id=sub_id,
                                                    stripe_customer_id=recovered_sub.get("customer") or ""):
                        return JSONResponse({"error": "The subscription account update is pending. Please retry later."}, status_code=503)
                    if plan_key and not _set_subscription_plan_key(uid, plan_key):
                        return JSONResponse({"error": "The subscription plan update is pending. Please retry later."}, status_code=503)
                if pending_key:
                    if not _set_subscription_plan_key(uid, pending_key) or not _set_subscription_pending_plan(uid, None):
                        return JSONResponse({"error": "The subscription plan update is pending. Please retry later."}, status_code=503)
                print("[stripe-webhook] subscription credit grant confirmed")
            elif sub_id:
                return JSONResponse({"error": "This subscription account could not be checked. Credit delivery is pending."}, status_code=503)
        elif etype in ("customer.subscription.updated", "customer.subscription.created"):
            sub = event["data"]["object"]
            sub_id = sub.get("id") or ""
            uid = _uid_for_subscription(sub_id) if sub_id else None
            if not uid:
                # This event's payload IS the Subscription object, so the
                # metadata fallback (see invoice.paid above) needs no extra
                # API call here -- it's right there on `sub`.
                uid = (sub.get("metadata") or {}).get("uid")
            status = sub.get("status") or "active"
            period_end_ts = _subscription_period_end(sub)
            print("[stripe-webhook] subscription updated sub=", sub_id, "uid=", uid, "status=", status)
            if uid:
                # Also records "cancelled, ends on <date>" (cancel_at_period_end)
                # so the Account page can say so instead of "you're subscribed".
                _sync_subscription_to_profile(uid, sub)
        elif etype == "customer.subscription.deleted":
            sub = event["data"]["object"]
            sub_id = sub.get("id") or ""
            uid = _uid_for_subscription(sub_id) if sub_id else None
            print("[stripe-webhook] subscription canceled sub=", sub_id, "uid=", uid)
            if uid:
                _set_subscription_fields(uid, subscription_status="none")
                _set_subscription_cancel_flag(uid, False)
                _set_subscription_pending_plan(uid, None)
        return {"ok": True}
    except Exception as e:
        print("[stripe-webhook] UNEXPECTED ERROR:", str(e))
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": "error"}, status_code=500)

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
#  - CLEANUP_FINAL_OUTPUT_DAYS only applies to a job owned by a user whose
#    profiles.subscription_status is 'active' (see the monthly-subscription
#    billing further down). Everyone else's finished output gets only
#    PAYONCE_OUTPUT_HOURS -- Ali's call (2026-09-26): pay-once credit-pack
#    buyers shouldn't cost us a full 30 days of Cloudflare storage for a
#    video they made once and left. See _final_output_cutoff().
# Update privacy.html and terms.html if any of these numbers change -- all
# three make a stated retention-time commitment to users.
CLEANUP_RETENTION_HOURS = 6
CLEANUP_FINAL_OUTPUT_DAYS = 30
PAYONCE_OUTPUT_HOURS = 48
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
_FINAL_OUTPUT_SUFFIXES = ("_final_dubbed.mp3", "_final_dubbed_video.mp4", "_final_lipsync.mp4",
                          "_final_voices.m4a", "_final_effects.m4a", "_final_corrections.m4a")      # the last two: the optional separate tracks of a long dub


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


def _subscription_active(uid):
    """True only if this user currently has an active monthly subscription
    (see the Stripe subscription billing section below) -- controls which
    retention window their finished outputs get (CLEANUP_FINAL_OUTPUT_DAYS
    vs PAYONCE_OUTPUT_HOURS). Fails closed (False) on any error/missing
    config, same reasoning as _already_notified: better to under-retain a
    file than silently treat everyone as a paying subscriber."""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    import urllib.request as _ur
    url = f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select=subscription_status"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        return bool(rows) and rows[0].get("subscription_status") == "active"
    except Exception:
        return False


def _final_output_cutoff(path, now, sub_cache):
    """The mtime cutoff below which a *finished* output file (see
    _is_final_output) gets deleted: CLEANUP_FINAL_OUTPUT_DAYS ago for a job
    owned by an active subscriber, PAYONCE_OUTPUT_HOURS ago for everyone
    else (no owner found at all -- e.g. a guest/free job -- is treated as
    pay-once too, the cheaper/shorter side, not the free 30-day tier).
    sub_cache is a plain dict the caller reuses across one cleanup sweep so
    the same uid's subscription status is only looked up once even if they
    have several finished files sitting in OUTPUT_DIR."""
    payonce_cutoff = now - PAYONCE_OUTPUT_HOURS * 3600
    job_id = _job_id_from_output_path(path)
    if not job_id:
        return payonce_cutoff
    uid = _uid_for_job(job_id)
    if not uid:
        return payonce_cutoff
    if uid not in sub_cache:
        sub_cache[uid] = _subscription_active(uid)
    return (now - CLEANUP_FINAL_OUTPUT_DAYS * 86400) if sub_cache[uid] else payonce_cutoff


# ---------- watermark for the free tier ----------
# Somebody who has only used the free starting credits (no subscription, no credit pack and no subscription invoice
# ever) gets the semi-transparent Lisan AI logo on the videos they make -- see watermark.py. A video is marked once,
# when it is made (merge, lip-sync, long dub). Audio files and sound are never marked. Anything that is not clearly "free" (the lookup failed, a payment exists,
# a test account listed in WATERMARK_EXEMPT_UIDS) is never marked. Switch off for everybody: WATERMARK_FREE=0.
_wm_cache = {}                 # uid -> (is_free, valid_until)
_WM_FREE_TTL = 300             # seconds a "free" answer is kept (so a first purchase shows up quickly)
_WM_PAID_TTL = 3600


def _wm_rows(path):
    """Rows of a Supabase read, or None when the read failed (so "no rows" and "could not look" are never mixed up)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    import urllib.request as _ur
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(f"{SUPABASE_URL}/rest/v1/{path}", headers=hdrs)
        with _ur.urlopen(req, timeout=8) as r:
            rows = json.load(r)
        return rows if isinstance(rows, list) else None
    except Exception as ex:
        print(f"[watermark] could not read {path.split('?')[0]}: {ex}")
        return None


def _is_free_user(uid):
    """True only when it is certain that this account has never paid. False when unsure."""
    uid = str(uid or "").strip()
    if not _re_sec.fullmatch(r"[0-9A-Fa-f-]{8,64}", uid):
        return False
    now = _time.time()
    hit = _wm_cache.get(uid)
    if hit and hit[1] > now:
        return hit[0]
    prof = _wm_rows(f"profiles?id=eq.{uid}&select=subscription_status")
    if prof is None:
        return False
    free = True
    if prof and prof[0].get("subscription_status") == "active":
        free = False
    if free:
        for tbl in ("credit_orders", "subscription_invoices"):
            rows = _wm_rows(f"{tbl}?uid=eq.{uid}&select=*&limit=1")
            if rows is None:
                return False            # could not look: not cached, not marked
            if rows:
                free = False
                break
    _wm_cache[uid] = (free, now + (_WM_FREE_TTL if free else _WM_PAID_TTL))
    return free


def _wm_needed(uid):
    """Do this user's files carry the watermark?"""
    try:
        if not watermark.ENABLED or not uid or watermark.exempt(uid):
            return False
        return _is_free_user(uid)
    except Exception as ex:
        print(f"[watermark] check failed: {ex}")
        return False


def _uid_for_subscription(stripe_subscription_id):
    """Maps a Stripe subscription id back to our uid, via the
    stripe_subscription_id we store on profiles when the subscription is
    created (see /api/billing/subscribe's webhook handling below). Used by
    the invoice.paid / customer.subscription.* webhook events, which only
    ever give us Stripe's own ids, never our uid directly."""
    if not stripe_subscription_id or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    import urllib.request as _ur
    import urllib.parse as _up
    url = (f"{SUPABASE_URL}/rest/v1/profiles?stripe_subscription_id=eq."
           f"{_up.quote(stripe_subscription_id)}&select=id&limit=1")
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        return rows[0].get("id") if rows else None
    except Exception:
        return None


def _set_subscription_fields(uid, **fields):
    """PATCHes any subset of profiles.subscription_status /
    stripe_subscription_id / stripe_customer_id /
    subscription_current_period_end for this uid. Same PATCH-profiles
    pattern as set_credits() above."""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not fields:
        return False
    import urllib.request as _ur
    body = json.dumps(fields).encode("utf-8")
    url = f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}"
    hdrs = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        req = _ur.Request(url, data=body, headers=hdrs, method="PATCH")
        with _ur.urlopen(req, timeout=10) as r:
            return r.status in (200, 204)
    except Exception as ex:
        print(f"[subscription] _set_subscription_fields error: {ex}")
        return False


def _set_subscription_plan_key(uid, plan_key):
    """Best-effort PATCH of just profiles.subscription_plan_key -- kept
    deliberately SEPARATE from _set_subscription_fields() above, not merged
    into one call, because PostgREST rejects an entire PATCH if any key in
    its JSON body doesn't match a real column. Bundling this new field into
    the main call would mean a subscriber's subscription_status /
    stripe_subscription_id / stripe_customer_id silently fail to save too,
    on every checkout, until the `ALTER TABLE profiles ADD COLUMN
    subscription_plan_key text;` migration has actually been run -- keeping
    it isolated means the core activation always succeeds regardless of
    migration timing; only the tier tagging (and therefore which credit
    amount later renewals grant -- see _get_profile_plan_key) waits on it."""
    if not uid or not plan_key or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    import urllib.request as _ur
    body = json.dumps({"subscription_plan_key": plan_key}).encode("utf-8")
    url = f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}"
    hdrs = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        req = _ur.Request(url, data=body, headers=hdrs, method="PATCH")
        with _ur.urlopen(req, timeout=10) as r:
            return r.status in (200, 204)
    except Exception as ex:
        print(f"[subscription] _set_subscription_plan_key error (has the profiles.subscription_plan_key migration been run yet?): {ex}")
        return False


def _set_subscription_cancel_flag(uid, flag):
    """Best-effort PATCH of just profiles.subscription_cancel_at_period_end
    ("cancelled, but still active until the paid period ends"). Its own
    call, same reasoning as _set_subscription_plan_key: PostgREST rejects a
    whole PATCH if one key isn't a real column, so this new column must
    never be bundled into the core status update. Needs:
      alter table profiles add column if not exists
        subscription_cancel_at_period_end boolean not null default false;"""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    import urllib.request as _ur
    body = json.dumps({"subscription_cancel_at_period_end": bool(flag)}).encode("utf-8")
    url = f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}"
    hdrs = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        req = _ur.Request(url, data=body, headers=hdrs, method="PATCH")
        with _ur.urlopen(req, timeout=10) as r:
            return r.status in (200, 204)
    except Exception as ex:
        print(f"[subscription] _set_subscription_cancel_flag error (has the profiles.subscription_cancel_at_period_end migration been run yet?): {_http_err_detail(ex)}")
        return False


def _set_subscription_pending_plan(uid, plan_key):
    """Best-effort PATCH of profiles.subscription_pending_plan_key -- the
    tier a subscriber has scheduled to switch DOWN to at their next renewal
    (None clears it). Separate call for the same reason as the cancel flag
    above. Needs:
      alter table profiles add column if not exists subscription_pending_plan_key text;"""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    import urllib.request as _ur
    body = json.dumps({"subscription_pending_plan_key": plan_key or None}).encode("utf-8")
    url = f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}"
    hdrs = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        req = _ur.Request(url, data=body, headers=hdrs, method="PATCH")
        with _ur.urlopen(req, timeout=10) as r:
            return r.status in (200, 204)
    except Exception as ex:
        print(f"[subscription] _set_subscription_pending_plan error (has the profiles.subscription_pending_plan_key migration been run yet?): {_http_err_detail(ex)}")
        return False


def _sync_subscription_to_profile(uid, sub):
    """Copies a Stripe Subscription object's status, period end and
    "cancelled at period end" flag onto the user's profile. Used by the
    customer.subscription.* webhooks and by the Account page's refresh, so
    both always agree. Returns what it stored."""
    status = sub.get("status") or "active"
    if status in ("canceled", "incomplete_expired"):
        status = "none"
    period_end_ts = _subscription_period_end(sub)
    fields = {"subscription_status": status}
    if period_end_ts:
        fields["subscription_current_period_end"] = _time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", _time.gmtime(period_end_ts))
    _set_subscription_fields(uid, **fields)
    cancel = status != "none" and (bool(sub.get("cancel_at_period_end")) or bool(sub.get("cancel_at")))
    _set_subscription_cancel_flag(uid, cancel)
    return {"status": status, "cancel_at_period_end": cancel, "period_end": period_end_ts}


def _read_subscription_profile(uid):
    """Service-key read of this user's subscription columns for /api/user/info.
    Tries the full column list and falls back to fewer columns if a newer
    column hasn't been migrated yet (PostgREST answers 400 for an unknown
    column), so the core status is always readable."""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {}
    import urllib.request as _ur
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    for cols in (
        "subscription_status,subscription_plan_key,subscription_current_period_end,subscription_cancel_at_period_end,subscription_pending_plan_key",
        "subscription_status,subscription_plan_key,subscription_current_period_end,subscription_cancel_at_period_end",
        "subscription_status,subscription_plan_key,subscription_current_period_end",
        "subscription_status",
    ):
        try:
            req = _ur.Request(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select={cols}", headers=hdrs)
            with _ur.urlopen(req, timeout=10) as r:
                rows = json.load(r)
            return rows[0] if rows else {}
        except Exception as ex:
            print(f"[subscription] profile read ({cols}) failed: {_http_err_detail(ex)}")
    return {}


def _get_profile_plan_key(uid):
    """Reads profiles.subscription_plan_key for one user -- lets invoice.paid
    grant the correct tier's credits on every RENEWAL, not just the first
    checkout (where the plan_key is already in hand from Stripe metadata).
    Returns "" on any failure, including the column not existing yet, which
    makes _get_subscription_plan("") fall back to the single legacy plan --
    i.e. exactly today's behavior -- so this is safe to call before the
    migration has been run."""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return ""
    import urllib.request as _ur
    try:
        req = _ur.Request(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select=subscription_plan_key",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        with _ur.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        return (rows[0].get("subscription_plan_key") or "") if rows else ""
    except Exception:
        return ""


# ---- Per-tier monthly clone allowance (profiles.subscription_clones_used) ----
# Needs the `profiles.subscription_clones_used` migration (SQL given to Ali
# alongside subscription_plan_key). Separate counter from voice_slots -- see
# _get_subscription_plan's docstring for why. Reset once per billing period
# by _grant_subscription_credits, incremented after each successful clone by
# /api/clone and /api/upload_custom_voice, and checked by
# _authorize_new_clones below.

def _get_profile_clones_used(uid):
    """Reads profiles.subscription_clones_used -- how many NEW voice clones
    this user has created so far in their CURRENT billing period. Returns
    None -- NOT 0 -- on any failure, including the column not existing yet
    (migration not run), so _authorize_new_clones can fail closed on
    'couldn't verify' rather than silently treating an unreachable column as
    a fresh, empty allowance."""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    import urllib.request as _ur
    try:
        req = _ur.Request(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}&select=subscription_clones_used",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        with _ur.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        if not rows:
            return None
        val = rows[0].get("subscription_clones_used")
        return int(val) if val is not None else 0
    except Exception as ex:
        print(f"[voice-library] _get_profile_clones_used error (has the profiles.subscription_clones_used migration been run?): {ex}")
        return None


def _increment_clone_usage(uid, n):
    """Best-effort: adds n to profiles.subscription_clones_used right after
    n new voices actually finish cloning successfully. Read-then-write, not
    an atomic RPC like credits -- fine here since this is a soft monthly cap
    (not a payment-critical balance) and a single user's own clone requests
    are effectively serialized in practice; worst case under a rare race is
    undercounting by one clone, never an overspend of real money. Isolated
    from every other profile write, same reasoning as
    _set_subscription_plan_key: a missing column here must never break the
    clone itself (which already succeeded on ElevenLabs' side by the time
    this runs), only the monthly-cap bookkeeping."""
    if not uid or not n or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    import urllib.request as _ur
    try:
        current = _get_profile_clones_used(uid) or 0
        body = json.dumps({"subscription_clones_used": current + int(n)}).encode("utf-8")
        req = _ur.Request(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}",
            data=body,
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            method="PATCH",
        )
        with _ur.urlopen(req, timeout=10) as r:
            return r.status in (200, 204)
    except Exception as ex:
        print(f"[voice-library] _increment_clone_usage error (has the profiles.subscription_clones_used migration been run?): {ex}")
        return False


def _reset_subscription_clone_usage(uid):
    """Best-effort PATCH resetting profiles.subscription_clones_used back to
    0 -- called from _grant_subscription_credits exactly once per NEW
    billing period (never on an idempotent 'already-fulfilled' replay), so
    each period's cloning allowance starts fresh, same billing-period
    boundary that grants that period's credits. Isolated call, same
    reasoning as _set_subscription_plan_key: if the column doesn't exist yet
    this must never break credit granting itself."""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    import urllib.request as _ur
    try:
        body = json.dumps({"subscription_clones_used": 0}).encode("utf-8")
        req = _ur.Request(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}",
            data=body,
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            method="PATCH",
        )
        with _ur.urlopen(req, timeout=10) as r:
            return r.status in (200, 204)
    except Exception as ex:
        print(f"[subscription] _reset_subscription_clone_usage error (has the profiles.subscription_clones_used migration been run?): {ex}")
        return False


# ---- Voice library (user_voices) -- persisted, named cloned voices ----
# Needs the `user_voices` table (SQL given to Ali alongside
# profiles.subscription_plan_key / pricing_config.subscription_plans).
# See _authorize_new_clones' docstring for the overall gating design.

def _count_user_voices(uid):
    """Row count of uid's saved voices. Returns None -- NOT 0 -- on any
    failure, including user_voices not existing yet (migration not run),
    so callers can tell "confirmed zero saved voices" apart from "couldn't
    check" and fail closed on the latter rather than silently treating an
    unreachable table as an empty one."""
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    import urllib.request as _ur
    try:
        req = _ur.Request(
            f"{SUPABASE_URL}/rest/v1/user_voices?uid=eq.{uid}&select=id",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        with _ur.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        return len(rows)
    except Exception as ex:
        print(f"[voice-library] _count_user_voices error (has the user_voices migration been run?): {ex}")
        return None


def _save_user_voice(uid, elevenlabs_voice_id, name, description="", source_job_id=""):
    """Inserts one row into user_voices -- called right after a clone/
    upload actually succeeds on ElevenLabs' side. Best-effort: if this
    INSERT fails, the voice still exists and the user still got it for
    this session, so the clone itself must not be reported as failed --
    but it's logged loudly since a failure here means that voice is an
    orphan: not counted against the slot cap, not protected from
    cleanup_cloned_voices' sweep, and not reusable later. Rare in practice
    (same Supabase call pattern used everywhere else in this file)."""
    if not uid or not elevenlabs_voice_id or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    import urllib.request as _ur
    body = json.dumps({
        "uid": uid,
        "elevenlabs_voice_id": elevenlabs_voice_id,
        "name": (name or "Untitled voice")[:200],
        "description": (description or "")[:2000],
        "source_job_id": source_job_id or None,
    }).encode("utf-8")
    hdrs = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        req = _ur.Request(f"{SUPABASE_URL}/rest/v1/user_voices", data=body, headers=hdrs, method="POST")
        with _ur.urlopen(req, timeout=10) as r:
            return r.status in (200, 201, 204)
    except Exception as ex:
        print(f"[voice-library] _save_user_voice FAILED for uid={uid} voice={elevenlabs_voice_id} -- orphaned (not slot-counted, not cleanup-protected): {ex}")
        return False


def _voice_engines_for_ids(voice_ids: list) -> dict:
    """Looks up which engine (elevenlabs/inworld) created each of these
    saved voice_ids, via user_voices.voice_engine. Missing rows, a NULL
    voice_engine column, or the column not existing at all (migration not
    run yet) all fail soft to an empty dict here -- every caller treats a
    missing entry as "elevenlabs" (the only engine that existed before this
    feature), so a pre-existing saved voice keeps working exactly as
    before no matter what state the migration is in."""
    result = {}
    ids = [v for v in set(voice_ids or []) if v]
    # Inworld's own stock voices (the Step 4 library when the admin switch is on Inworld) are not in user_voices:
    # they belong to Inworld by what they are, whatever the switch says today.
    for vid in list(ids):
        if inworld_service.is_library_voice(vid, INWORLD_API_KEY):
            result[vid] = "inworld"
            ids.remove(vid)
    if not ids or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return result
    import urllib.request as _ur
    try:
        ids_csv = ",".join(ids)
        req = _ur.Request(
            f"{SUPABASE_URL}/rest/v1/user_voices?elevenlabs_voice_id=in.({ids_csv})&select=elevenlabs_voice_id,voice_engine",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        with _ur.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        for row in rows:
            result[row.get("elevenlabs_voice_id")] = row.get("voice_engine") or "elevenlabs"
    except Exception as ex:
        print(f"[voice-engine] _voice_engines_for_ids lookup failed (has the user_voices.voice_engine migration been run?): {ex}")
    return result


def _tag_voice_engine(elevenlabs_voice_id: str, engine: str):
    """Isolated PATCH -- separate call so a missing user_voices.voice_engine
    column (migration not run yet) can never block the INSERT in
    _save_user_voice that actually saves the voice itself. Best-effort;
    failing here just means this one voice falls back to the safe
    "elevenlabs" default at lookup time (_voice_engines_for_ids above)
    until the migration is run and it's re-tagged. Only called for
    engine == "inworld" -- "elevenlabs" is already the fallback default, so
    tagging it explicitly would just be an extra write for no behavior
    change."""
    if not elevenlabs_voice_id or engine not in ("elevenlabs", "inworld") or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    import urllib.request as _ur
    try:
        body = json.dumps({"voice_engine": engine}).encode("utf-8")
        req = _ur.Request(
            f"{SUPABASE_URL}/rest/v1/user_voices?elevenlabs_voice_id=eq.{elevenlabs_voice_id}",
            data=body,
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            method="PATCH",
        )
        with _ur.urlopen(req, timeout=10) as r:
            r.read()
    except Exception as ex:
        print(f"[voice-engine] _tag_voice_engine failed for {elevenlabs_voice_id} (has the user_voices.voice_engine migration been run?): {ex}")


def _active_voice_engine() -> str:
    """Which engine NEW clones use right now, per the admin panel's
    Settings tab "Voice Engine" switch -- defaults to "elevenlabs"
    (unchanged behavior) until Ali switches it. Never affects
    ALREADY-cloned voices; see _voice_engines_for_ids for how those keep
    using whichever engine actually created them regardless of this
    setting."""
    engine = str(_get_pricing_config().get("voiceEngine") or "elevenlabs").strip().lower()
    return engine if engine in ("elevenlabs", "inworld") else "elevenlabs"


def _all_saved_voice_ids():
    """Every ElevenLabs voice_id saved in ANY user's library right now --
    used to protect saved voices from cleanup_cloned_voices()'s
    account-wide sweep (see /api/cleanup_voices), since the ElevenLabs
    account itself is shared across every user of this app, not scoped per
    user. Raises on failure rather than returning [] -- the caller must
    treat "couldn't verify" as "assume everything needs protecting" (fail
    closed), never as "nothing needs protecting", since the latter would
    let a transient Supabase hiccup permanently delete a paying user's
    saved voice."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("Supabase not configured")
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/user_voices?select=elevenlabs_voice_id",
        headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        rows = json.load(r)
    return [row.get("elevenlabs_voice_id") for row in rows if row.get("elevenlabs_voice_id")]


def _authorize_new_clones(uid, num_new, engine="elevenlabs"):
    """Central gate for creating NEW cloned voices -- shared by /api/clone
    (can create several at once, one per speaker) and
    /api/upload_custom_voice (always exactly one). Voice cloning is
    subscription-only (Ali's 2026-09-27 decision: the account's own
    cloning quota is a shared, limited resource that can no longer be
    offered unmetered to every free/pay-once user).

    Returns (True, plan_dict) if the request may proceed, or
    (False, error_message) if it must be rejected -- callers return
    error_message as a 402 JSON error WITHOUT spending any ElevenLabs
    quota or credits.

    Fails CLOSED on every uncertainty (no active subscription, can't count
    existing saved voices, can't reach the real ElevenLabs quota) -- the
    entire point of this gate is to stop over-promising a shared resource,
    so an unknown state must block a clone, never silently allow it.

    `engine` -- which engine will actually create this clone (from the
    caller's own _active_voice_engine() call, resolved BEFORE calling this
    gate). Only used to decide whether the ElevenLabs account-wide quota
    check below applies at all: it's ElevenLabs' own shared quota, so it
    has nothing to do with a clone that's actually going to be created via
    Inworld. Before this parameter existed the check ran unconditionally,
    so once ElevenLabs' shared quota was exhausted it also blocked
    Inworld cloning even though Inworld hadn't used any of that quota --
    caught 2026-09-27 while Ali was testing Inworld cloning right after
    ElevenLabs' shared limit was hit."""
    if not uid:
        return False, "Please log in to clone or upload a voice."
    if not _subscription_active(uid):
        return False, "Voice cloning is a subscription feature now. Subscribe to unlock your own saved voice slots."
    plan = _get_subscription_plan(_get_profile_plan_key(uid))
    slots = int(plan.get("voice_slots") or 0)
    used = _count_user_voices(uid)
    if used is None:
        return False, "Couldn't verify your saved-voice count right now -- please try again shortly."
    if used + num_new > slots:
        remaining = max(0, slots - used)
        return False, (
            f"You have {used} of {slots} saved voice slot{'s' if slots != 1 else ''} used ({remaining} free). "
            f"Delete a saved voice to free a slot, or upgrade your plan, before cloning {num_new} more."
        )
    # Per-tier monthly clone allowance -- separate from the slot cap above.
    # None means the tier has no separate cap set in admin (see
    # _get_subscription_plan's docstring); only enforced once Ali actually
    # sets a number for a tier, so this is inert until then.
    clone_limit = plan.get("clones_per_month")
    if clone_limit is not None:
        clones_used = _get_profile_clones_used(uid)
        if clones_used is None:
            return False, "Couldn't verify your monthly cloning usage right now -- please try again shortly."
        if clones_used + num_new > clone_limit:
            remaining = max(0, clone_limit - clones_used)
            return False, (
                f"You've used {clones_used} of {clone_limit} voice clone{'s' if clone_limit != 1 else ''} allowed this billing period "
                f"({remaining} left). This resets on your next billing date, or upgrade your plan for more."
            )
    # Real ElevenLabs account-wide quota, shared across ALL users -- can
    # still block this even when the user's own slot cap has room. This
    # reads the cached poll (service_usage_monitor), refreshed every ~20
    # minutes, not a live call -- see that module's docstring; good enough
    # for "are we basically out", not meant to be exact to the unit.
    # Only applies when ElevenLabs is actually the engine about to create
    # this clone -- Inworld cloning never touches ElevenLabs' quota at
    # all, so this must not block it (see this function's docstring).
    if engine == "elevenlabs":
        eleven = service_usage_monitor.get_eleven_cached()
        if eleven.get("ok"):
            used_ops = eleven.get("clone_ops_used")
            limit_ops = eleven.get("clone_ops_limit")
            if used_ops is not None and limit_ops:
                if (limit_ops - used_ops) < num_new:
                    return False, "Voice cloning is temporarily unavailable due to high demand. Please try again later, or choose a studio voice for this speaker in Step 4."
    return True, plan


def _grant_subscription_credits(uid, invoice_id, credits, period_start=None, period_end=None):
    """Only a confirmed new subscription receipt can replace the allowance.

    Missing SQL/periods, old receipts and identity conflicts never use the
    former read/PATCH/permanent-credit fallback. SQL owns grant/history/reset.
    """
    from credit_actions import ActionProblem, subscription_grant
    try:
        return subscription_grant(_sb_rpc, uid, invoice_id, credits, period_start, period_end)
    except (ActionProblem, ValueError, TypeError):
        return None


def _set_subscription_credits(uid, amount):
    """Best-effort PATCH setting profiles.subscription_credits to EXACTLY
    `amount` -- called once per NEW billing period from
    _grant_subscription_credits, right after that same idempotency check.
    This OVERWRITES (never adds to) whatever was left from the previous
    cycle -- Ali's 2026-09-27 "use it or lose it" decision for subscription
    credits specifically. One-time pack purchases and admin grants are a
    separate, permanent bucket (`credits`, via add_credits/set_credits)
    and are never touched by this. Isolated call: if the column doesn't
    exist yet (migration not run), this fails and returns False -- the
    caller then falls back to the old add-to-permanent-credits behavior,
    so a failure here must never raise or silently drop a cycle's grant."""
    from credit_billing import number
    try:
        amount = number(amount, "Credit balance", zero=True, integer=True)
    except ValueError:
        return False
    if not uid or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    import urllib.request as _ur
    try:
        body = json.dumps({"subscription_credits": amount}).encode("utf-8")
        req = _ur.Request(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}",
            data=body,
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            method="PATCH",
        )
        with _ur.urlopen(req, timeout=10) as r:
            return r.status in (200, 204)
    except Exception as ex:
        print(f"[subscription] _set_subscription_credits error (has the profiles.subscription_credits migration been run?): {ex}")
        return False


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


def _resend_quota_header(response):
    """Best-effort read of Resend's x-resend-monthly-quota response header
    (a running count of emails sent this month) -- the only usage signal
    Resend exposes at all; see service_usage_monitor.py's module docstring
    for why there's no endpoint to actually poll for remaining quota."""
    try:
        val = response.headers.get("x-resend-monthly-quota")
        return int(val) if val is not None else None
    except Exception:
        return None


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
            try:
                service_usage_monitor.record_resend_usage(_resend_quota_header(r))
            except Exception:
                pass
            return r.status in (200, 201)
    except Exception as ex:
        print(f"[expiry-notice] Resend send failed: {ex}")
        return False


def _check_expiring_outputs():
    """Emails a job's owner once when their finished output enters the
    CLEANUP_WARNING_DAYS window before CLEANUP_FINAL_OUTPUT_DAYS auto-delete.
    De-duped via the expiry_notices table (see the SQL note above), so this
    is safe to call repeatedly -- a job already recorded there is skipped.

    Only applies to active subscribers (the 30-day retention tier) -- a
    pay-once job is only ever kept PAYONCE_OUTPUT_HOURS (see
    _final_output_cutoff), far shorter than this 3-day-out warning window
    could ever fire meaningfully, and this email's copy explicitly says
    "30-day storage policy", which would be wrong for that user. No
    equivalent warning is sent for the short pay-once window in this first
    pass -- flagged to Ali as a known gap, not an oversight."""
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
            if not uid or not _subscription_active(uid):
                continue
            email = _email_for_uid(uid)
            if not email:
                continue
            days_left = max(1, round(CLEANUP_FINAL_OUTPUT_DAYS - age_days))
            if _send_expiry_email(email, days_left):
                _mark_notified(job_id, uid)
    except Exception as e:
        print("[expiry-notice] error:", e)


_last_expiry_check = 0.0

# When the server has been quiet this long, hand freed memory back to the system once (garbage collection plus
# malloc_trim) and log how much real program memory that released. One trim per quiet period.
IDLE_TRIM_MINUTES = 5
_idle_trim_for = [None]


def _idle_memory_trim():
    """True when a trim ran. Skipped while any transcription or long-dub worker is running, and after the first trim
    of a quiet period. The log line (before -> after) shows whether the memory held after a job was really free."""
    last = app_state.last_job_activity
    if _idle_trim_for[0] == last or (_time.time() - last) / 60 < IDLE_TRIM_MINUTES:
        return False
    try:
        if whisper_service._transcribe_queue.running() > 0 or longdub_service._RUNNING:
            return False
    except Exception:
        return False
    _idle_trim_for[0] = last
    import gc
    before = resource_meter.read_mem()[1]
    gc.collect()
    whisper_service._trim_memory()
    after = resource_meter.read_mem()[1]
    if before is not None and after is not None:
        print(f"[memory] idle trim: real program memory {before * 1024:.0f} MB -> {after * 1024:.0f} MB")
    else:
        print("[memory] idle trim done (memory figures unavailable)")
    return True


def _cleanup_worker():
    global _last_expiry_check
    # First database backup about 2 minutes after start-up (then once a day, checked every sweep).
    _time.sleep(120)
    try:
        r2_backup.backup_db_tables(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception as e:
        print("[db-backup] first run error:", e)
    try:
        import voice_numbers
        r2_backup.backup_register_files([voice_numbers.FILE])
    except Exception as e:
        print("[register-backup] first run error:", e)
    while True:
        _time.sleep(CLEANUP_INTERVAL_MIN * 60)
        try:
            r2_backup.backup_final_outputs(OUTPUT_DIR, _is_final_output)
        except Exception as e:
            print("[r2-backup] sweep error:", e)
        try:
            r2_backup.backup_db_tables(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        except Exception as e:
            print("[db-backup] sweep error:", e)
        try:
            import voice_numbers
            r2_backup.backup_register_files([voice_numbers.FILE])
        except Exception as e:
            print("[register-backup] sweep error:", e)
        try:
            now = _time.time()
            short_cutoff = now - CLEANUP_RETENTION_HOURS * 3600
            # One Supabase lookup per distinct uid per sweep, not per file --
            # see _final_output_cutoff().
            sub_cache = {}
            removed = 0
            for d in (UPLOAD_DIR, OUTPUT_DIR):
                for p in d.glob("*"):
                    try:
                        if not p.is_file():
                            continue
                        # Durable lip-sync intents have no approved expiry yet.
                        if p.name.endswith("_lipsync_active.json"):
                            continue
                        if _is_final_output(p):
                            cutoff = _final_output_cutoff(p, now, sub_cache)
                        else:
                            cutoff = short_cutoff
                        if p.stat().st_mtime < cutoff:
                            p.unlink()
                            removed += 1
                    except Exception:
                        pass
            try:
                from shortdub_operations import maintain_receipts
                maintain_receipts(OUTPUT_DIR, UPLOAD_DIR)
                from lipsync_operations import maintain_requests
                maintain_requests(OUTPUT_DIR)
                freed_dirs = disk_guard.remove_stale_dirs((UPLOAD_DIR, OUTPUT_DIR), short_cutoff)
                if freed_dirs:
                    print(f"[cleanup] removed old working folders, freed {freed_dirs / 1048576:.0f} MB")
            except Exception as _dg_ex:
                print("[cleanup] folder sweep error:", _dg_ex)
            for jid in list(_job_started.keys()):
                if _job_started[jid] < short_cutoff:
                    jobs_progress.pop(jid, None)
                    # /api/emotions, /api/generate, and /api/lipsync each track
                    # their own progress under a differently-prefixed key (not
                    # the plain job_id the line above already pops), so those
                    # were silently accumulating forever -- one dict entry per
                    # job ever run since the process last restarted, no matter
                    # how old. This is what was behind idle RAM slowly
                    # climbing over the process's lifetime even with nobody
                    # using the site (see mem_diag's "anon" figure). Same fix
                    # for USAGE/_job_charges/_abandoned_jobs below -- all three
                    # are keyed by job_id and had the identical never-purged
                    # bug.
                    jobs_progress.pop(f"emotions_{jid}", None)
                    jobs_progress.pop(f"generate_{jid}", None)
                    jobs_progress.pop(f"lipsync_{jid}", None)
                    app_state.USAGE.pop(jid, None)
                    _job_charges.pop(jid, None)
                    _abandoned_jobs.discard(jid)
                    _job_started.pop(jid, None)
            if removed:
                print(f"[cleanup] removed {removed} old file(s)")
        except Exception as e:
            print("[cleanup] error:", e)

        try:
            _idle_memory_trim()
        except Exception as e:
            print("[cleanup] idle memory trim error:", e)

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
resource_meter.start()
service_usage_monitor.start()
disk_guard.start(_is_final_output)

# ==================== GEMINI HELPER ====================

def _gemini_text(prompt: str):
    last = ""
    for m in GEMINI_TEXT_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={GEMINI_API_KEY}"
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            resource_meter.add_api_usd(resource_meter.gemini_usd(data))      # its price goes onto the job being measured
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
    if not LOCAL_DEV and _re.fullmatch(r"G-[A-Za-z0-9]{4,20}", ga_id):
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
    # no-cache (see _NO_CACHE_HEADERS below, which this predates in the file
    # but not in spirit) -- this route already re-reads landing.html and
    # pricing_config fresh on every request, but without this header nothing
    # stops a browser from serving an already-open tab's cached copy of the
    # page instead of re-requesting it, so an admin-panel change (like the
    # subscription plan name) can look like it never took effect even though
    # the server-side data is correct. Same class of bug _NO_CACHE_HEADERS
    # was added for on app.js/index.html/styles.css; this route just never
    # got it.
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


def _plans_for_public_display(plans):
    """Copy of the subscription tiers for the public pricing page and the
    in-app Buy modal. While Inworld is the active voice engine (no clone-
    count limit -- see _get_subscription_plan) every tier's clones_per_month
    is blanked, so neither page advertises a monthly clone allowance that
    isn't enforced. The stored admin values are never modified; on
    ElevenLabs the list is returned unchanged."""
    out = []
    for p in plans:
        if not isinstance(p, dict):
            out.append(p)
            continue
        q = dict(p, storage_gb=_plan_storage_gb(p))
        if _active_voice_engine() == "inworld":
            q["clones_per_month"] = None
        out.append(q)
    return out


@app.get("/pricing")
def pricing_page():
    """Dedicated pricing page (task #62, 2026-09-27) -- lists every
    subscription tier and one-time credit pack as a clickable card, reading
    live from /api/billing/packs (which already exposes both packs and the
    new subscriptionPlans array -- see that route's docstring). Public
    (see PUBLIC_PATHS below) so logged-out visitors can browse pricing
    before creating an account; clicking a card while logged out sends
    them to /login instead of attempting checkout. Same GA4-injection +
    no-cache treatment as landing() above, for the same reasons."""
    html = (BASE_DIR / "pricing.html").read_text(encoding="utf-8")
    import re as _re
    ga_id = (_get_pricing_config().get("gaMeasurementId") or "").strip()
    if not LOCAL_DEV and _re.fullmatch(r"G-[A-Za-z0-9]{4,20}", ga_id):
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
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


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
    # Same server-side injection pattern as landing()/pricing_page() above
    # (this route used to just be a static FileResponse, but that can't set
    # a class on <body> before the page paints) -- reads the admin-set
    # uiStyle and, only when it's "new", adds class="ui-new" to <body> so
    # styles.css's body.ui-new block applies with zero flash of the classic
    # style first. index.html's <body> tag has no attributes of its own
    # (confirmed before writing this), so this exact-string replace is safe.
    html = (BASE_DIR / "index.html").read_text(encoding="utf-8")
    if (_get_pricing_config().get("uiStyle") or "classic") == "new":
        html = html.replace("<body>", '<body class="ui-new">', 1)
    return HTMLResponse(html, headers=_NO_CACHE_HEADERS)

@app.get("/app.js")
def app_js():
    return FileResponse(BASE_DIR / "app.js", media_type="application/javascript", headers=_NO_CACHE_HEADERS)

@app.get("/dialogs.js")
def dialogs_js():
    return FileResponse(BASE_DIR / "dialogs.js", media_type="application/javascript", headers=_NO_CACHE_HEADERS)

@app.get("/site.js")
def site_js():
    return FileResponse(BASE_DIR / "site.js", media_type="application/javascript", headers=_NO_CACHE_HEADERS)

@app.get("/site.css")
def site_css():
    return FileResponse(BASE_DIR / "site.css", media_type="text/css", headers=_NO_CACHE_HEADERS)

@app.get("/chat.js")
def chat_js():
    return FileResponse(BASE_DIR / "chat.js", media_type="application/javascript", headers=_NO_CACHE_HEADERS)

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
def enhance_progress(job_id: str, request: Request):
    _g = _job_guard(request, job_id)
    if _g:
        return _g
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
# Lowered from 60 to 30 (Sept 2026, same OOM investigation as the
# concurrency cap above): a real 49-second/82MB test upload alone pushed
# this container to 7.5 of its 7.6GB limit even with the concurrency fix in
# place (most of that was the one-time Whisper/pyannote model-load spike,
# not the clip itself, but the part that DOES scale with the clip -- audio
# decode buffers, Demucs vocal separation, Whisper's own attention buffers
# -- still matters at the margin). Ali's choice: cut the safety margin
# clips can eat into by half.
NO_LIPSYNC_MAX_SEC = 30
# No file-size cap existed here at all before this (only duration was
# checked) -- app.js's own client-side check quietly allowed up to 400MB,
# unenforced server-side, so a direct POST to this endpoint could bypass it
# entirely. 50MB pairs naturally with the 30s duration cap above: Ali's
# daughter's real test clip was 82MB for 49 seconds (~13-14Mbps, ordinary
# smartphone 1080p) -- at that same bitrate a 30-second clip lands right
# around 50MB, so this isn't an arbitrary number, it's sized to match real
# phone-video bitrates at the new duration cap.
MAX_UPLOAD_MB = 50
# Bigger ceiling that applies ONLY when the upload comes with a chosen
# section (trim_start/trim_end -- the "choose the part to dub" box in
# Step 1). The server cuts that 15/30-second section out with ffmpeg right
# after the upload lands and deletes the original, so the memory-heavy
# steps (Whisper, Demucs, cloning) still only ever see a short clip. The
# 50MB cap above exists to bound THAT processing, not the disk write.
# 300MB covers roughly a 5-minute phone video or a longer, lower-bitrate one.
MAX_TRIM_UPLOAD_MB = 300
# One section cut at a time: a re-encode of a 1080p/4K source can take a few
# hundred MB of RAM, and this container also hosts Whisper/Demucs.
_trim_lock = threading.Semaphore(1)


def _cut_upload_section(src: Path, job_id: str, start: float, duration: float):
    """Blocking (run it in a worker thread). Replaces the uploaded original
    with just the chosen section, named {job_id}.mp4 (video) or
    {job_id}.wav (audio-only) so everything downstream treats it like any
    normal short upload. The original file is deleted. Returns the new
    path; raises on failure after cleaning up the temp output."""
    is_vid = src.suffix.lower() in VIDEO_EXTS
    final_ext = ".mp4" if is_vid else ".wav"
    tmp = UPLOAD_DIR / f"{job_id}_trimtmp{final_ext}"
    final = UPLOAD_DIR / f"{job_id}{final_ext}"
    try:
        with _trim_lock:
            ffmpeg_utils.trim_media(src, tmp, start, duration, is_vid)
        if not tmp.exists() or tmp.stat().st_size < 1024:
            raise Exception("ffmpeg produced no output")
        try: src.unlink()
        except Exception: pass
        tmp.replace(final)
        return final
    except Exception:
        try: tmp.unlink()
        except Exception: pass
        raise


# Below this, cloning is still allowed (see the 4s floor above) but the
# result may not sound convincing -- ElevenLabs' own guidance is that ~30s
# of clean audio is where they've seen consistently good clones. This only
# drives a non-blocking warning shown to the user, not a rejection.
CLONE_QUALITY_WARN_SEC = 30


@app.post("/api/transcribe")
async def transcribe(request: Request, file: UploadFile = File(...), speaker_count: int = Form(0), hf_token: str = Form(""), voice_consent: str = Form(""), lipsync: str = Form("false"), trim_start: float = Form(-1.0), trim_end: float = Form(-1.0)):
    if _rate_limited(request, "transcribe", HEAVY_RATE_MAX, HEAVY_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    # The client already blocks the button until the voice-rights checkbox is
    # checked (Step 1), but that's JS and can't be trusted -- anyone posting
    # directly to this endpoint bypasses it, so this is the enforcement that
    # actually matters. Same reasoning as the duration cap a few lines below.
    if voice_consent.strip().lower() not in ("true", "1", "yes", "on"):
        return JSONResponse({"error": "You must certify you have the necessary rights or consents for the voices in this file before uploading."}, status_code=400)
    uid, _no_acct = _paid_uid(request)
    if _no_acct is not None:
        return _no_acct
    bal = get_credits(uid)
    transcribe_cost = int(_get_pricing_config().get("transcribeCredits", 3))
    if bal is not None and bal < transcribe_cost:
        return JSONResponse({"error": customer_message(f"Insufficient credits ({bal} left). Transcription costs {transcribe_cost} credits. Use âž• Buy to get a pack.")}, status_code=402)
    job_id = str(uuid.uuid4())
    _job_started[job_id] = _time.time()
    _register_job_owner(job_id, uid)
    ext = Path(file.filename or "audio.mp4").suffix.lower() or ".mp4"
    dest = UPLOAD_DIR / f"{job_id}{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    # Server-side file-size cap (Sept 2026 OOM investigation). app.js's own
    # client-side check (MAX_UPLOAD_BYTES) allowed up to 400MB, but that's
    # JavaScript and can't be trusted -- exactly like the duration check
    # below, anyone posting directly to this endpoint bypassed it entirely
    # until now. See MAX_UPLOAD_MB's own comment above for why 50MB.
    trim_requested = trim_start >= 0 and trim_end > trim_start
    size_cap_mb = MAX_TRIM_UPLOAD_MB if trim_requested else MAX_UPLOAD_MB
    size_mb = dest.stat().st_size / (1024 * 1024)
    if size_mb > size_cap_mb:
        try: dest.unlink()
        except Exception: pass
        _job_started.pop(job_id, None)
        return JSONResponse({"error": customer_message(f"This file is {round(size_mb, 1)} MB. The limit is {size_cap_mb} MB â€” please compress it or cut it shorter first.")}, status_code=413)
    lipsync_wanted = lipsync.strip().lower() in ("true", "1", "yes", "on")
    if trim_requested:
        # The user picked a section of a longer (or larger) file. Cut it out
        # now, in a worker thread so the event loop keeps serving everyone
        # else, and let the normal duration checks below run on the SLICE.
        _cut_max = LIPSYNC_MAX_SEC if lipsync_wanted else NO_LIPSYNC_MAX_SEC
        _cut_len = trim_end - trim_start
        if _cut_len > _cut_max + 0.05:
            try: dest.unlink()
            except Exception: pass
            _job_started.pop(job_id, None)
            return JSONResponse({"error": customer_message(f"The chosen section is {round(_cut_len)} seconds long. The limit is {_cut_max} seconds.")}, status_code=413)
        try:
            _src_dur = ffmpeg_utils.get_media_duration(dest)
        except Exception:
            _src_dur = None
        if _src_dur is not None:
            if trim_start >= _src_dur - 0.5:
                try: dest.unlink()
                except Exception: pass
                _job_started.pop(job_id, None)
                return JSONResponse({"error": "The chosen start time is past the end of this file. Please choose the section again."}, status_code=400)
            _cut_len = min(_cut_len, _src_dur - trim_start)
        try:
            dest = await asyncio.to_thread(_cut_upload_section, dest, job_id, trim_start, _cut_len)
        except Exception as _cut_ex:
            print(f"[transcribe] section cut failed: {str(_cut_ex)[-400:]}")
            for _p in UPLOAD_DIR.glob(f"{job_id}*"):
                try: _p.unlink()
                except Exception: pass
            _job_started.pop(job_id, None)
            return JSONResponse({"error": "Couldn't cut that section out of the file. Please try a different section, or convert the file to MP4 first."}, status_code=400)
        ext = dest.suffix.lower()
    # Server-side duration cap. The client already blocks out-of-range
    # clips in its own UI, but that check runs in JavaScript and can't be
    # trusted -- anyone posting directly to this endpoint bypasses it
    # entirely, so this is the enforcement that actually matters. Which
    # range applies depends on whether this upload wants lip-sync (see the
    # LIPSYNC_MIN_SEC block above) -- a real duration probe decides, not a
    # client-supplied flag alone, since a wrong duration here means either
    # blocking a valid upload or letting through one that will only fail
    # later, after the user has already waited through transcription.
    min_sec = LIPSYNC_MIN_SEC if lipsync_wanted else NO_LIPSYNC_MIN_SEC
    max_sec = LIPSYNC_MAX_SEC if lipsync_wanted else NO_LIPSYNC_MAX_SEC
    dur = None
    try:
        dur = ffmpeg_utils.get_media_duration(dest)
    except Exception as _dur_ex:
        print(f"[transcribe] duration probe failed, allowing upload through: {_dur_ex}")
    # A section we just cut can read a few hundredths of a second off its
    # nominal length (AAC frame padding), so a section the user legitimately
    # picked at exactly the limit must not bounce off it.
    _tol = 0.5 if trim_requested else 0.0
    if dur is not None:
        if dur < min_sec - _tol:
            try: dest.unlink()
            except Exception: pass
            _job_started.pop(job_id, None)
            return JSONResponse({"error": customer_message(f"This clip is only {round(dur, 1)} seconds long. The minimum is {min_sec} seconds.")}, status_code=413)
        if dur > max_sec + _tol:
            try: dest.unlink()
            except Exception: pass
            _job_started.pop(job_id, None)
            limit_desc = "For a lip-synced clip, the" if lipsync_wanted else "The"
            return JSONResponse({"error": customer_message(f"This clip is {round(dur)} seconds long. {limit_desc} limit is {max_sec} seconds â€” please trim it first.")}, status_code=413)
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
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)

    job_id = str(uuid.uuid4())
    _job_started[job_id] = _time.time()
    _register_job_owner(job_id, uid)
    ext = Path(file.filename or "audio.mp4").suffix.lower() or ".mp4"
    dest = UPLOAD_DIR / f"{job_id}{ext}"

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Same size/duration enforcement as /api/transcribe (Sept 2026 OOM
    # investigation) -- this route runs the identical Demucs vocal-
    # separation step below, so it's exactly as capable of triggering the
    # same memory spike, and had none of these checks at all before now.
    # No lipsync flag exists on this endpoint, so it's held to the plain
    # (non-lipsync) NO_LIPSYNC_MIN_SEC/NO_LIPSYNC_MAX_SEC range -- the only
    # duration policy this endpoint can apply without that information.
    size_mb = dest.stat().st_size / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        try: dest.unlink()
        except Exception: pass
        _job_started.pop(job_id, None)
        return JSONResponse({"error": customer_message(f"This file is {round(size_mb, 1)} MB. The limit is {MAX_UPLOAD_MB} MB â€” please trim or compress it first.")}, status_code=413)
    try:
        dur = ffmpeg_utils.get_media_duration(dest)
    except Exception as _dur_ex:
        dur = None
        print(f"[attach_media] duration probe failed, allowing upload through: {_dur_ex}")
    if dur is not None:
        if dur < NO_LIPSYNC_MIN_SEC:
            try: dest.unlink()
            except Exception: pass
            _job_started.pop(job_id, None)
            return JSONResponse({"error": customer_message(f"This clip is only {round(dur, 1)} seconds long. The minimum is {NO_LIPSYNC_MIN_SEC} seconds.")}, status_code=413)
        if dur > NO_LIPSYNC_MAX_SEC:
            try: dest.unlink()
            except Exception: pass
            _job_started.pop(job_id, None)
            return JSONResponse({"error": customer_message(f"This clip is {round(dur)} seconds long. The limit is {NO_LIPSYNC_MAX_SEC} seconds â€” please trim it first.")}, status_code=413)

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
def abandon_job(job_id: str, request: Request):
    _g = _job_guard(request, job_id, allow_empty=False)
    if _g:
        return _g
    _abandoned_jobs.add(job_id)
    jobs_progress.pop(job_id, None)
    try:
        for d in (UPLOAD_DIR, OUTPUT_DIR):
            for p in list(d.glob(f"{job_id}.*")) + list(d.glob(f"{job_id}_*")):
                if p.is_file():
                    p.unlink()
    except Exception:
        pass
    try:
        from shortdub_operations import clear_receipts
        clear_receipts(OUTPUT_DIR, job_id)
    except OSError:
        pass
    return {"ok": True}

# Must be registered BEFORE the generic "/api/progress/{job_id}" route
# just below -- FastAPI/Starlette matches routes in registration order,
# so without this ordering a request to "/api/progress/generate" would
# match {job_id}="generate" on the generic route first and always look up
# the wrong (nonexistent) "generate" key, returning not_found forever.
# (This was the actual cause of the frozen Generate progress bar.)
@app.get("/api/progress/generate")
def generate_progress(request: Request, job_id: str = ""):
    _g = _job_guard(request, job_id)
    if _g:
        return _g
    progress = jobs_progress.get(f"generate_{job_id}", {"status": "not_found"})
    if progress.get("status") == "done" and operation_active(job_id):
        return {"status": "processing", "percent": 99, "status_text": "Completing your audio"}
    return _public_progress(progress)


def _merge_say(job_id, percent, text, status="processing"):
    """What the merge is doing right now, for the progress bar on the Merge button (best effort, never raises)."""
    try:
        jobs_progress[f"merge_{job_id}"] = {"status": status, "percent": int(max(0, min(100, percent))), "status_text": text}
    except Exception:
        pass


@app.get("/api/progress/merge")
def merge_progress(request: Request, job_id: str = ""):
    _g = _job_guard(request, job_id, allow_empty=False)
    if _g:
        return _g
    return _public_progress(jobs_progress.get(f"merge_{job_id}", {"status": "not_found"}))

@app.get("/api/job_status")
def job_status(request: Request, job_id: str = ""):
    """For the main page when it restores a saved session: is the original media of
    this job still on the server, and was its Arabic audio already generated?"""
    _g = _job_guard(request, job_id, allow_empty=False)
    if _g:
        return _g
    return {"media": resolve_job_audio(job_id) is not None,
            "generated": (OUTPUT_DIR / f"{job_id}_final_dubbed.mp3").exists()}

@app.get("/api/progress/{job_id}")
def progress(job_id: str, request: Request):
    _g = _job_guard(request, job_id)
    if _g:
        return _g
    return _public_progress(jobs_progress.get(job_id, {"status": "not_found"}))

@app.get("/api/source/{job_id}")
def source(job_id: str, request: Request):
    _g = _job_guard(request, job_id, allow_empty=False)
    if _g:
        return _g
    p = resolve_job_audio(job_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p)

def _library_voices():
    """The voice list of Step 4 and Auto-Assign. It follows the admin "Voice Engine" switch: on Inworld the stock
    voices of Inworld are listed (Arabic ones first); on the other engine, or when Inworld cannot be reached at all
    (not even a saved copy), the original list is used, so Step 4 is never empty.
    Every voice carries a permanent `number` (voice_numbers.py): "Male voice 17" always means the same voice."""
    engine, got = "elevenlabs", None
    if _active_voice_engine() == "inworld":
        got = inworld_service.fetch_library(INWORLD_API_KEY)
        if got.get("voices"):
            engine = "inworld"
        else:
            print("[voices] the Inworld voice list is unavailable; using the other list for now")
    if engine != "inworld":
        got = eleven_service.fetch_voices(ELEVENLABS_API_KEY)
    voices = got.get("voices") if isinstance(got, dict) else None
    if not voices:
        return got
    rows = [dict(v) for v in voices]
    if engine == "elevenlabs":
        # the original numbering was the position in the list sorted by voice id: a new register starts from exactly that
        rows.sort(key=lambda v: (str(v.get("voice_id", "")).lower(), str(v.get("voice_id", "")).swapcase()))
    import voice_numbers
    voice_numbers.assign(engine, rows)
    # shown in this order: the voices made for Arabic first, otherwise by number (a number never changes, the order may)
    rows.sort(key=lambda v: (v.get("arabic") is False, v.get("number") or 0))
    for i, v in enumerate(rows):
        v["order"] = i
    return dict(got, voices=rows)
_library_voices.cache_key = lambda: _active_voice_engine()        # the Auto-Assign cache must not outlive a switch of engine

@app.post("/api/voices")
def voices(payload: dict = {}):
    return _library_voices()

@app.get("/api/voices/preview/{voice_id}")
def voice_preview(voice_id: str, request: Request):
    """A short sample of one stock voice (Inworld library only). The first play makes the sample (one short
    sentence), every later play - by anyone - is served from disk."""
    if not inworld_service.is_library_voice(voice_id, INWORLD_API_KEY):
        return JSONResponse({"error": "not found"}, status_code=404)
    path = inworld_service.preview_path(voice_id)
    if not (path.is_file() and path.stat().st_size > 0):
        if _rate_limited(request, "voice_preview", 20, 600):
            return JSONResponse({"error": _RATE_LIMIT_MSG}, status_code=429)
        try:
            path = inworld_service.make_preview(voice_id, INWORLD_API_KEY)
        except Exception as ex:
            print(f"[voices] preview of {voice_id} failed: {ex}")
            return JSONResponse({"error": "This preview is not available right now."}, status_code=503)
    return FileResponse(path, media_type="audio/mpeg")

@app.post("/api/voice_library/search")
def voice_library_search(req: VoiceLibrarySearchRequest):
    """Search ElevenLabs' full Voice Library (not just your own account) â€”
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
    ElevenLabs â€” check your subscription page's counter after your first use."""
    return eleven_service.add_shared_voice(ELEVENLABS_API_KEY, req.public_owner_id, req.voice_id, req.new_name)

# Auto-Assign: pick library voices by the speaker's age, pitch and tone (see voice_match.py).
import voice_match_routes
voice_match_routes.register(app, job_guard=_job_guard, rate_limited=_rate_limited, rate_message=_RATE_LIMIT_MSG,
                            resolve_audio=resolve_job_audio, fetch_voices=_library_voices,
                            gemini_key=lambda: GEMINI_API_KEY)

@app.post("/api/analyze_speakers")
def analyze_speakers(req: AnalyzeRequest, request: Request):
    _g = _job_guard(request, req.job_id, allow_empty=False)
    if _g:
        return _g
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
    from credit_billing import credit_amount, clone_refund_amount
    import uuid as _u
    if _rate_limited(request, "clone", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    _g = _job_guard(request, req.job_id, allow_empty=False)
    if _g:
        return _g
    uid, _no_acct = _paid_uid(request)
    if _no_acct is not None:
        return _no_acct
    # Count exactly the non-empty speakers the engine will try to clone.
    speakers_requested = sorted(set(s.speaker for s in req.segments if (s.text or "").strip()))
    if req.speakers_to_clone:
        speakers_requested = [s for s in speakers_requested if s in req.speakers_to_clone]
    if not speakers_requested:
        return JSONResponse({"error": "Choose at least one speaker with speech to clone."}, status_code=400)
    engine = _active_voice_engine()
    ok, plan_or_error = _authorize_new_clones(uid, len(speakers_requested), engine=engine)
    if not ok:
        return JSONResponse({"error": customer_message(plan_or_error)}, status_code=402)
    cfg = _get_pricing_config()
    try:
        clone_cost = credit_amount(cfg.get("inworldCloneCredits" if engine == "inworld" else "cloneCredits", 5))
    except ValueError:
        return JSONResponse({"error": "The cloning price is unavailable. Please try again later."}, status_code=503)
    debit_id = str(_u.uuid4())
    balance = deduct_credits(uid, clone_cost, "clone", req.job_id, operation_id=debit_id)
    if not debit_confirmed(balance):
        if balance is None:
            _short_clone_settle(uid, debit_id, "cancelled", clone_cost)
        message = "Not enough credits to clone these voices." if balance is False else "We couldn't confirm the payment. No voices were cloned; please try again later."
        return JSONResponse({"error": message, "operation_id": debit_id}, status_code=402 if balance is False else 503)
    try:
        if engine == "inworld":
            result = inworld_service.clone_voices(req.job_id, req.segments, INWORLD_API_KEY, req.speakers_to_clone)
        else:
            result = eleven_service.clone_voices(req.job_id, req.segments, ELEVENLABS_API_KEY, req.speakers_to_clone)
    except Exception:
        result = {"error": "The voices could not be cloned. Please try again later.", "cloned_voices": {}}
    if not isinstance(result, dict):
        result = {"error": "The voices could not be cloned. Please try again later.", "cloned_voices": {}}
    voices = result.get("cloned_voices")
    voices = voices if isinstance(voices, dict) else {}
    delivered = {speaker: voices[speaker] for speaker in speakers_requested
                 if isinstance(voices.get(speaker), str) and voices[speaker].strip()
                 and not voices[speaker].startswith("ERROR")}
    for speaker, voice_id in delivered.items():
        _save_user_voice(uid, voice_id, speaker, "", req.job_id)
        if engine == "inworld":
            _tag_voice_engine(voice_id, engine)
    if delivered:
        _increment_clone_usage(uid, len(delivered))
    failed = len(speakers_requested) - len(delivered)
    refund_due = clone_refund_amount(clone_cost, failed, len(speakers_requested))
    outcome = "failed" if not delivered else "partial" if failed else "delivered"
    settled = _short_clone_settle(uid, debit_id, outcome, refund_due)
    refunded = refund_due if settled is not None else 0
    result.update(credits_charged=clone_cost-refunded, credits_refunded=refunded,
                  refund_pending=refund_due-refunded, operation_id=debit_id)
    result["cloned_voices"] = {speaker: delivered.get(speaker) or (voices[speaker] if isinstance(voices.get(speaker), str) and voices[speaker].startswith("ERROR") else "ERROR: This voice could not be cloned.")
                               for speaker in speakers_requested}
    if not delivered:
        result["status"] = "error"
        result["error"] = (f"The voices could not be cloned. We refunded {refunded} credits." if refunded else
                           "The voices could not be cloned. Your refund is pending and will be retried automatically.")
    else:
        result["status"] = "success"
        result.pop("error", None)
        warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
        result["warnings"] = warnings + [f"This request cost {clone_cost-refunded} credits; {refunded} credits refunded."]
    if refund_due and settled is None:
        result["notice"] = "Your refund is pending; it will be retried automatically."
        if delivered:
            result["warnings"].append(result["notice"])
    return customer_payload(result)

@app.get("/api/my_voices")
def my_voices(request: Request):
    """This user's saved voice library, plus their plan's slot cap -- used
    by the Account page's Voices tab (task #61) and, later, the dubbing
    flow's voice picker so a saved voice can be reused across projects."""
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    plan = _get_subscription_plan(_get_profile_plan_key(uid))
    voices = []
    if SUPABASE_URL and SUPABASE_SERVICE_KEY:
        try:
            req = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/user_voices?uid=eq.{uid}&select=id,elevenlabs_voice_id,name,description,created_at&order=created_at.desc",
                headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                voices = json.load(r)
        except Exception as ex:
            print(f"[voice-library] /api/my_voices list error (has the user_voices migration been run?): {ex}")
    clone_limit = plan.get("clones_per_month")
    clones_used = _get_profile_clones_used(uid) if clone_limit is not None else None
    return {
        "voices": voices,
        "voice_slots_used": len(voices),
        "voice_slots_total": int(plan.get("voice_slots") or 0),
        "plan_name": plan.get("name"),
        "subscription_active": _subscription_active(uid),
        # None (not 0) when the tier has no separate monthly clone cap set in
        # admin, or the count couldn't be read -- lets the frontend hide this
        # line entirely instead of showing a misleading "0 of None".
        "clones_used_this_period": clones_used if clone_limit is not None else None,
        "clones_limit_this_period": clone_limit,
    }

@app.patch("/api/my_voices/{voice_row_id}")
def update_my_voice(voice_row_id: str, req: VoiceUpdateRequest, request: Request):
    """Renames/describes one saved voice -- purely a label change, doesn't
    touch ElevenLabs or the slot count at all."""
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return JSONResponse({"error": "This feature is temporarily unavailable. Please try again later."}, status_code=503)
    if not _SAFE_TOKEN_RE.fullmatch(str(voice_row_id)):
        return JSONResponse({"error": "We couldn't find that voice."}, status_code=404)
    name = (req.name or "").strip()[:200] or "Untitled voice"
    description = (req.description or "").strip()[:2000]
    body = json.dumps({"name": name, "description": description}).encode("utf-8")
    # Scoped to uid=eq.{uid} as well as id -- a user can never rename/
    # describe another user's saved voice even by guessing a row id.
    url = f"{SUPABASE_URL}/rest/v1/user_voices?id=eq.{voice_row_id}&uid=eq.{uid}"
    hdrs = {
        "apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json", "Prefer": "return=minimal",
    }
    try:
        r = urllib.request.Request(url, data=body, headers=hdrs, method="PATCH")
        with urllib.request.urlopen(r, timeout=10) as resp:
            ok = resp.status in (200, 204)
    except Exception as ex:
        print(f"[voice-library] rename failed: {ex}")
        return JSONResponse({"error": "We couldn't rename that voice. Please try again."}, status_code=502)
    return {"ok": ok, "name": name, "description": description}

@app.delete("/api/my_voices/{voice_row_id}")
def delete_my_voice(voice_row_id: str, request: Request):
    """Deletes one saved voice -- frees its slot immediately (the DB row is
    what the slot cap counts, see _count_user_voices), then best-effort
    deletes the underlying ElevenLabs voice too. If that second step fails,
    the slot is still freed correctly; the orphaned ElevenLabs voice is no
    longer protected (see _all_saved_voice_ids) and gets swept up by the
    next /api/cleanup_voices pass instead."""
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return JSONResponse({"error": "This feature is temporarily unavailable. Please try again later."}, status_code=503)
    if not _SAFE_TOKEN_RE.fullmatch(str(voice_row_id)):
        return JSONResponse({"error": "We couldn't find that voice."}, status_code=404)
    try:
        lookup = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/user_voices?id=eq.{voice_row_id}&uid=eq.{uid}&select=elevenlabs_voice_id,voice_engine",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        with urllib.request.urlopen(lookup, timeout=10) as r:
            rows = json.load(r)
    except Exception:
        # Falls back to the pre-voice_engine query shape -- covers the case
        # where user_voices.voice_engine hasn't been migrated in yet, so
        # deleting a saved voice never breaks just because that column is
        # missing. Every row from before this feature existed has no engine
        # tag anyway, which safely defaults to "elevenlabs" below.
        try:
            lookup = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/user_voices?id=eq.{voice_row_id}&uid=eq.{uid}&select=elevenlabs_voice_id",
                headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
            with urllib.request.urlopen(lookup, timeout=10) as r:
                rows = json.load(r)
        except Exception as ex:
            print(f"[voice-library] delete lookup failed: {ex}")
            return JSONResponse({"error": "We couldn't delete that voice. Please try again."}, status_code=502)
    if not rows:
        return JSONResponse({"error": "We couldn't find that voice."}, status_code=404)
    eleven_voice_id = rows[0].get("elevenlabs_voice_id")
    voice_engine = rows[0].get("voice_engine") or "elevenlabs"
    try:
        delreq = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/user_voices?id=eq.{voice_row_id}&uid=eq.{uid}",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Prefer": "return=minimal"},
            method="DELETE")
        with urllib.request.urlopen(delreq, timeout=10) as r:
            r.read()
    except Exception as ex:
        print(f"[voice-library] delete failed: {ex}")
        return JSONResponse({"error": "We couldn't delete that voice. Please try again."}, status_code=502)
    if eleven_voice_id:
        try:
            if voice_engine == "inworld":
                inworld_service.delete_voice(eleven_voice_id, INWORLD_API_KEY)
            else:
                eleven_service.delete_voice(eleven_voice_id, ELEVENLABS_API_KEY)
        except Exception as ex:
            print(f"[voice-library] delete_voice best-effort failed for {eleven_voice_id}: {ex}")
    return {"ok": True}

@app.post("/api/translate")
@resource_meter.metered("shortdub_translate", lambda req, *a, **k: getattr(req, "job_id", ""))
def translate(req: TranslateRequest, request: Request):
    if _rate_limited(request, "translate", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    _g = _job_guard(request, req.job_id)
    if _g:
        return _g
    return gemini_service.translate_segments(req.job_id, req.segments, GEMINI_API_KEY)

@app.post("/api/detect_emotions")
def detect_emotions(req: EmotionRequest, request: Request):
    if _rate_limited(request, "detect_emotions", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    _g = _job_guard(request, req.job_id, allow_empty=False)
    if _g:
        return _g
    audio = resolve_job_audio(req.job_id)
    if audio is None:
        return {"error": "We couldn't find the audio for this project. Please upload it again."}
    jobs_progress[f"emotions_{req.job_id}"] = {"status": "processing", "percent": 0,
                                               "current": 0, "total": len(req.segments)}
    threading.Thread(target=gemini_service.detect_emotions_worker,
                     args=(req.job_id, str(audio), GEMINI_API_KEY, req.segments), daemon=True).start()
    return {"status": "started"}

@app.get("/api/progress/emotions/{job_id}")
def emotions_progress(job_id: str, request: Request):
    _g = _job_guard(request, job_id)
    if _g:
        return _g
    return _public_progress(jobs_progress.get(f"emotions_{job_id}", {"status": "not_found"}))

@app.post("/api/tashkeel")
@resource_meter.metered("shortdub_tashkeel", lambda req, *a, **k: (req.items[0].job_id if getattr(req, "items", None) and getattr(req.items[0], "job_id", None) else ""))
def tashkeel(req: TashkeelRequest, request: Request):
    if _rate_limited(request, "tashkeel", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
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
    items = json.loads(txt.strip())
    return {"items": items}

def _resolve_short_voices(req):
    voice_ids = list(req.speaker_voices.values()) + ([req.default_voice_id] if req.default_voice_id else [])
    engines = _voice_engines_for_ids(voice_ids)
    req.speaker_voice_engines = {spk: engines.get(vid, "elevenlabs") for spk, vid in req.speaker_voices.items() if vid}
    req.default_voice_engine = engines.get(req.default_voice_id, "elevenlabs") if req.default_voice_id else "elevenlabs"


def _short_quote(req, regenerate=False):
    cfg = _get_pricing_config()
    characters = {"elevenlabs": 0, "inworld": 0}
    if regenerate:
        req.voice_engine = _voice_engines_for_ids([req.voice_id]).get(req.voice_id, "elevenlabs")
        rows = [(req.segment, req.voice_engine, req.voice_id)]
    else:
        if req.tts_provider == "gemini":
            raise ValueError("A fixed quote is available for studio voices only.")
        _resolve_short_voices(req)
        rows = [(s, req.speaker_voice_engines.get(s.speaker, "elevenlabs") if req.speaker_voices.get(s.speaker, "").strip() else req.default_voice_engine,
                 req.speaker_voices.get(s.speaker, "").strip() or req.default_voice_id.strip()) for s in req.segments]
    seen = set()
    for segment, engine, voice_id in rows:
        if segment.segment_id in seen:
            raise ValueError("Each line must have a different identifier. Please reload your project.")
        seen.add(segment.segment_id)
        if not segment.arabic_text.strip():
            continue
        if not voice_id.strip():
            raise ValueError("Choose a voice for every translated speaker before generating audio.")
        engine = "inworld" if engine == "inworld" else "elevenlabs"
        prompt = (inworld_service.instruction_tag(segment.emotion) + segment.arabic_text if engine == "inworld"
                  else eleven_service._emotion_tags(segment.emotion) + " " + segment.arabic_text)
        characters[engine] += len(prompt)
    if not sum(characters.values()):
        raise ValueError("Fill at least one Arabic translation.")
    # Regeneration buys only this line's new voice. Analysis is settled with
    # a full dub; it must not be rebilled with every line or subsequent dub.
    quote = studio_quote(characters, {} if regenerate else usage_bucket(req.job_id), cfg)
    quote["required_balance"] = max(quote["credits"], int(cfg.get("minReserve", 20))) if not regenerate else quote["credits"]
    return quote


def _quote_response(req, request, regenerate=False):
    guard = _job_guard(request, req.job_id, allow_empty=False)
    if guard is not None:
        return guard
    uid, error = _paid_uid(request)
    if error is not None:
        return error
    try:
        quote = _short_quote(req, regenerate)
    except ValueError as ex:
        return JSONResponse({"error": customer_message(str(ex))}, status_code=400)
    return {key: value for key, value in quote.items() if key != "ai_snapshot"}


@app.post("/api/generate/quote")
def generate_quote(req: GenerateRequest, request: Request):
    return _quote_response(req, request)


@app.post("/api/regenerate_line/quote")
def regenerate_quote(req: RegenerateLineRequest, request: Request):
    return _quote_response(req, request, regenerate=True)


def _check_short_payment(req, uid, quote):
    if req.accepted_credits != quote["credits"]:
        return JSONResponse({"error": "Please check and confirm the current price before starting. The price may have changed."}, status_code=409)
    balance = get_credits(uid)
    if balance is None:
        return JSONResponse({"error": "Your credit balance could not be checked. Please try again shortly."}, status_code=503)
    if balance < quote["required_balance"]:
        return JSONResponse({"error": f"You need {quote['required_balance']} credits to start (you have {balance}). This action costs {quote['credits']} credits. Use âž• Buy to top up."}, status_code=402)
    return None


def _run_short_generate(req, uid, quote):
    try:
        eleven_service.generate_worker(req)
        progress = jobs_progress.get(f"generate_{req.job_id}") or {}
        if progress.get("status") != "done" or req.job_id in _abandoned_jobs:
            return
        if quote is None:  # Legacy API-only Gemini TTS mode keeps its existing variable pricing.
            _watch_and_deduct(req.job_id, uid, "generate")
            charged = _job_charges.get(req.job_id, {}).get("credits_charged")
            if charged is not None:
                progress["result"] = dict(progress.get("result") or {}, credits_charged=charged)
            return
        result = progress.get("result") or {}
        balance = deduct_credits(uid, quote["credits"], "generate", req.job_id, result.get("final_duration"))
        if not debit_confirmed(balance):
            progress.update(status="error", error="Audio was generated, but payment could not be confirmed. Please contact support before retrying.")
            return
        usage_bucket(req.job_id)["shortdub_ai_settled"] = quote["ai_snapshot"]
        _job_charges[req.job_id] = {"credits_charged": quote["credits"], "balance_after": get_credits(uid) if balance is True else balance}
        progress["result"] = dict(result, credits_charged=quote["credits"])
    except Exception:
        jobs_progress[f"generate_{req.job_id}"] = {"status": "error", "error": "Audio generation could not be completed. Please contact support before retrying."}
    finally:
        finish_operation(req.job_id)


def _short_edit(job_id, operation):
    if not begin_operation(job_id):
        return JSONResponse({"error": "This project is already processing audio. Please wait for it to finish."}, status_code=409)
    try:
        return operation()
    finally:
        finish_operation(job_id)


@app.post("/api/generate")
def generate(req: GenerateRequest, request: Request):
    if _rate_limited(request, "generate", HEAVY_RATE_MAX, HEAVY_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    _g = _job_guard(request, req.job_id, allow_empty=False)
    if _g:
        return _g
    uid, _no_acct = _paid_uid(request)
    if _no_acct is not None:
        return _no_acct
    quote = None
    if req.tts_provider != "gemini":
        try:
            quote = _short_quote(req)
        except ValueError as ex:
            return JSONResponse({"error": customer_message(str(ex))}, status_code=400)
        payment_error = _check_short_payment(req, uid, quote)
        if payment_error is not None:
            return payment_error
    if quote is None:
        bal = get_credits(uid)
        if bal is None:
            return JSONResponse({"error": "Your credit balance could not be checked. Please try again shortly."}, status_code=503)
        min_reserve = int(_get_pricing_config().get("minReserve", 20))
        if bal < min_reserve:
            return JSONResponse({"error": customer_message(f"You need at least {min_reserve} credits to start generating audio (you have {bal}). Use âž• Buy to top up.")}, status_code=402)
    _blk = _storage_block(uid, 0)
    if _blk is not None:
        return _blk          # storage full: delete finished files first (nothing is charged)
    req.elevenlabs_api_key = ELEVENLABS_API_KEY
    req.gemini_api_key = GEMINI_API_KEY
    req.inworld_api_key = INWORLD_API_KEY
    # Resolve which engine created each speaker's already-cloned voice_id
    # (never trust req.speaker_voice_engines from the client -- it isn't
    # even a field the frontend sends yet). This is intentionally NOT
    # "whatever the admin panel's Voice Engine switch currently says" --
    # see GenerateRequest.speaker_voice_engines' comment for why.
    if quote is None:
        _resolve_short_voices(req)
    # Keyed by job_id (not a single shared "generate" slot) so two jobs
    # running at the same time â€” two users, or two tabs â€” never overwrite
    # each other's progress/result, and credits never get charged against
    # the wrong job's character count.
    if not begin_operation(req.job_id):
        return JSONResponse({"error": "This project is already processing audio. Please wait for it to finish."}, status_code=409)
    jobs_progress[f"generate_{req.job_id}"] = {"status": "processing", "percent": 0, "result": None, "error": None}
    try:
        threading.Thread(target=_run_short_generate, args=(req, uid, quote), daemon=True).start()
    except Exception:
        finish_operation(req.job_id)
        raise
    return {"status": "started"}


@app.post("/api/regenerate_line")
def regenerate_line(req: RegenerateLineRequest, request: Request):
    if _rate_limited(request, "regenerate_line", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    guard = _job_guard(request, req.job_id, allow_empty=False)
    if guard is not None:
        return guard
    uid, error = _paid_uid(request)
    if error is not None:
        return error
    from shortdub_operations import RequestProblem, operation_path, run as run_request
    from shortdub_paths import line_audio_path
    try:
        operation_path(OUTPUT_DIR, req.job_id, getattr(req, "operation_id", None))
    except RequestProblem as ex:
        return JSONResponse(ex.payload, status_code=ex.status)
    def prepare():
        try:
            quote = _short_quote(req, regenerate=True)
        except ValueError as ex:
            raise RequestProblem(customer_message(str(ex)), 400, operation_complete=True) from None
        payment_error = _check_short_payment(req, uid, quote)
        if payment_error is not None:
            payload = json.loads(payment_error.body) if hasattr(payment_error, "body") else payment_error.content
            raise RequestProblem(payload["error"], payment_error.status_code, operation_complete=True)
        return quote["credits"]
    def generate_take():
        req.elevenlabs_api_key = ELEVENLABS_API_KEY
        req.inworld_api_key = INWORLD_API_KEY
        return customer_payload(eleven_service.regenerate_line(req))
    def run():
        try:
            result = run_request(OUTPUT_DIR, req, uid, prepare,
                lambda amount, operation_id: deduct_credits(uid, amount, "regenerate", req.job_id, operation_id=operation_id),
                generate_take, lambda operation_id, outcome, amount: _short_clone_settle(uid, operation_id, outcome, amount),
                _sb_rpc, lambda: [line_audio_path(OUTPUT_DIR, req.job_id, req.segment.segment_id, "raw", ".mp3"),
                                 line_audio_path(OUTPUT_DIR, req.job_id, req.segment.segment_id, "stretched", ".wav"),
                                 OUTPUT_DIR / f"{req.job_id}_final_dubbed.mp3"])
            if result.get("status") == "success":
                _job_charges[req.job_id] = {"credits_charged": result["credits_charged"], "balance_after": result["balance_after"]}
            return result
        except RequestProblem as ex:
            return JSONResponse(ex.payload, status_code=ex.status)
        except (OSError, ValueError, TypeError):
            return JSONResponse({"error": "This request could not be saved safely. Please contact support before retrying."}, status_code=503)
    return _short_edit(req.job_id, run)

@app.post("/api/restretch_line")
def restretch_line(req: RegenerateLineRequest, request: Request):
    _g = _job_guard(request, req.job_id, allow_empty=False)
    if _g:
        return _g
    # Pure editing action for the Step 5.5 Time Stretch dropdown: re-warps the
    # line's already-generated audio to its current setting, no TTS call and
    # no ElevenLabs key needed.
    return _short_edit(req.job_id, lambda: eleven_service.restretch_line(req))

@app.post("/api/remix_audio")
def remix_audio(req: RemixRequest, request: Request):
    _g = _job_guard(request, req.job_id, allow_empty=False)
    if _g:
        return _g
    return _short_edit(req.job_id, lambda: eleven_service.remix_with_offsets(req))

@app.get("/api/room_profile")
def room_profile(job_id: str, request: Request):
    """How reverberant the ORIGINAL recording is (measured once, cached), and the room setting currently in use."""
    _g = _job_guard(request, job_id, allow_empty=False)
    if _g:
        return _g
    try:
        import room_acoustics
        prof = room_acoustics.profile_for_job(job_id, OUTPUT_DIR)
        return {"profile": prof, "settings": eleven_service.ROOM_SETTINGS.get(job_id) or {"mode": "auto"},
                "last": eleven_service.ROOM_LAST.get(job_id), "enabled": bool(room_acoustics.ENABLED)}
    except Exception as ex:
        return JSONResponse({"error": "Room sound is not available for this job.", "detail": customer_message(str(ex)[:160])}, status_code=200)

def _merge_video_run(req: MergeRequest, request: Request, price):
    if _rate_limited(request, "merge_video", HEAVY_RATE_MAX, HEAVY_RATE_WINDOW_SEC):
        return JSONResponse({"error": _RATE_LIMIT_MSG}, status_code=429)
    _g = _job_guard(request, req.job_id, allow_empty=False)
    if _g:
        return _g
    uid, _no_acct = _paid_uid(request)
    if _no_acct is not None:
        return _no_acct
    bal = get_credits(uid)
    merge_cost = int(_get_pricing_config().get("mergeCredits", 1))
    if merge_cost <= 0:
        merge_cost = 1
    if bal is None:
        return JSONResponse({"error": "We could not verify your balance. Nothing was charged."}, status_code=503)
    if bal < price["max_total"]:
        plural = "s" if merge_cost != 1 else ""
        return JSONResponse({"error": f"Insufficient credits (merge costs {merge_cost} credit{plural}). Use âž• Buy."}, status_code=402)
    _sv = find_job_video(req.job_id)
    _blk = _storage_block(uid, _sv.stat().st_size if _sv is not None else 0, OUTPUT_DIR / f"{req.job_id}_final_dubbed_video.mp4")
    if _blk is not None:
        return _blk          # before anything is charged
    # Charge after a successful merge, along with successful music repairs.
    _merge_say(req.job_id, 3, "Preparing your dubbed audio...")
    video = find_job_video(req.job_id)
    # Job-scoped filename -- see the comment on CLEANUP_RETENTION_HOURS below
    # for why this used to be a single shared filename for every job on the
    # server (a real bug: two jobs finishing near each other would silently
    # overwrite each other's file) and why it now includes the job_id.
    dub = OUTPUT_DIR / f"{req.job_id}_final_dubbed.mp3"
    if video is None or not dub.exists():
        return {"error": "We couldn't find your video or dubbed audio. Please generate the dub first, then merge."}

    bg = job_background_audio(req.job_id)
    published_final = OUTPUT_DIR / f"{req.job_id}_final_dubbed_video.mp4"
    final = OUTPUT_DIR / f"merge_pending_{req.job_id}.mp4"

    # Isolate the dubbed voice from noise before it goes back into the video.
    # Never fails the merge: on any problem the original dubbed audio is used.
    # (Off by default: set CLEAN_VOICE=1 to switch it on.)
    clean_dub = OUTPUT_DIR / f"merge_clean_{req.job_id}.wav"
    if voice_clean.ENABLED:
        try:
            _vc = voice_clean.clean_voice(dub, clean_dub, OUTPUT_DIR, copy_on_keep=False)
            print(f"[voice-clean] {req.job_id}: cleaned={_vc['cleaned']} loss={_vc['loss_db']} floor={_vc.get('floor_db')} denoised={_vc.get('denoised')} {_vc['reason']}")
            if _vc["cleaned"] and clean_dub.exists() and clean_dub.stat().st_size > 1000:
                dub = clean_dub
        except Exception as _vc_ex:
            print(f"[voice-clean] {req.job_id}: skipped ({_vc_ex})")

    music_incomplete = False
    if bg is not None:
        # Optionally enhance the separated background
        _merge_say(req.job_id, 8, "Preparing the background sound...")
        bg_to_use = bg
        if req.enhance_background:
            enhanced_bg = OUTPUT_DIR / f"{req.job_id}_bg_enhanced.wav"
            audio_enhance.enhance_background(req.job_id, str(bg), str(enhanced_bg))
            if enhanced_bg.exists() and enhanced_bg.stat().st_size > 0:
                bg_to_use = enhanced_bg
        # The separated background keeps a faint metallic copy of the original
        # voices; lower its voice range while the original speakers talk.
        # A steady background sound that the separator filed under "voices" (crowd, machine hum, traffic ...)
        # is rebuilt from the pauses between the speakers when it is clearly missing (never fails the merge).
        fee = price["music_each"]
        def allow_music():
            balance = get_credits(uid)
            return balance is not None and balance >= merge_cost + fee and price["music_charged"] + fee <= price["music_max"]
        def charge_music(a, b):
            if fee and not debit_confirmed(deduct_credits(uid, fee, "short_dub_music_fill", req.job_id)):
                raise RuntimeError("Music repair payment could not be verified")
            price["music_charged"] += fee
        def music_progress(done, total):
            _merge_say(req.job_id, 20 + 60 * done / max(1, total),
                       f"Rebuilding the background music under the speech: part {done + 1} of {total}. This takes about a minute per part...")
        spans = _short_speech_spans(req.job_id, bg)
        keep_music = bool(req.keep_music and price.get("music_kept", True))  # False: the quote said it cannot be restored
        _merge_say(req.job_id, 14, "Removing the original voices from the background sound...")
        # strict=False: a music hole that cannot be rebuilt stays silent instead of failing the whole merge. Only the
        # holes that were really rebuilt are charged (charge_music runs once per rebuilt hole).
        _bp = dub_background.prepare(bg_to_use, bg.parent / "vocals.wav", dub, OUTPUT_DIR,
            f"merge_{req.job_id}", spans, key=FAL_API_KEY if keep_music else "",
            gemini_key=GEMINI_API_KEY, allow=allow_music, on_filled=charge_music, preserve_music=keep_music,
            strict=False, progress=music_progress, log=lambda t: print(f"[merge-music] {req.job_id}: {t}"),
            original=video if Path(str(video)).exists() else None)
        music_incomplete = bool((_bp.get("music_fill") or {}).get("incomplete"))
        bg_to_use = _bp["path"]
        _merge_say(req.job_id, 82, "Mixing the dubbed voice with the background...")
        # Use the same original phrase mask for reactions, so no original dialogue is reintroduced there.
        map_ = OUTPUT_DIR / f"{req.job_id}_merge_speech.json"
        map_.write_text(json.dumps(spans), encoding="utf-8")
        _rx = bg_duck.prepare_reactions(bg.parent / "vocals.wav", map_, dub, OUTPUT_DIR, f"merge_{req.job_id}")
        map_.unlink(missing_ok=True)
        mixed = OUTPUT_DIR / f"merge_mixed_{req.job_id}.wav"
        ffmpeg_utils.mix_two_audio(dub, bg_to_use, mixed, extra_audio=_rx["path"])
        _merge_say(req.job_id, 90, "Building the final video...")
        ffmpeg_utils.mux_audio_into_video(video, mixed, final)
        for _tmp in [mixed] + list(_bp["temps"]) + list(_rx["temps"]):
            try:
                _tmp.unlink()
            except Exception:
                pass
    else:
        ffmpeg_utils.mux_audio_into_video(video, dub, final)
    try:
        clean_dub.unlink()
    except Exception:
        pass
    marked = False
    if _wm_needed(uid):
        _wr = watermark.apply_video(final)
        marked = bool(_wr["ok"])
        print(f"[watermark] merge {req.job_id}: {'ok' if marked else 'FAILED, delivered without'} ({_wr['reason']})")

    if not debit_confirmed(deduct_credits(uid, merge_cost, "merge", req.job_id)):
        final.unlink(missing_ok=True)
        raise RuntimeError("The merge payment could not be confirmed. Please contact support.")
    price["merge_charged"] = merge_cost
    os.replace(final, published_final)
    return {"status": "success", "has_background": bg is not None, "enhanced": req.enhance_background,
            "watermarked": marked, "credits_charged": merge_cost + price["music_charged"],
            "music_kept": bool(price.get("music_kept", True)), "music_incomplete": music_incomplete}

# Optional reference photos for Step 7 -- uploaded separately from the
# /api/lipsync call itself (this just saves them to disk under the job's
# id), then picked up by lipsync_service.job_reference_images() when the
# job actually runs. Kept as its own small endpoint rather than folded into
# LipSyncRequest so the JSON POST that actually starts the job stays simple.
LIPSYNC_REF_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
LIPSYNC_REF_IMAGE_MAX = 5
LIPSYNC_REF_IMAGE_MAX_BYTES = 20 * 1024 * 1024

@app.post("/api/lipsync/reference-images")
async def lipsync_reference_images(request: Request, job_id: str = Form(...), files: List[UploadFile] = File(...)):
    if not LIPSYNC_REF_IMAGES_ENABLED:
        return JSONResponse({"error": "Reference photos are not available at the moment."}, status_code=410)
    if not LIPSYNC_ENABLED:
        return JSONResponse({"error": "Lip-sync is temporarily unavailable. Please check back soon."}, status_code=503)
    if _rate_limited(request, "lipsync", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    _g = _job_guard(request, job_id, allow_empty=False)
    if _g:
        return _g
    if find_job_video(job_id) is None:
        return JSONResponse({"error": "We couldn't find that project."}, status_code=404)

    # Replace, not append -- re-picking files in the UI shouldn't keep
    # piling old photos onto the new set.
    for old in OUTPUT_DIR.glob(f"lipsync_ref_{job_id}_*"):
        try: old.unlink()
        except Exception: pass

    saved = 0
    for f in files[:LIPSYNC_REF_IMAGE_MAX]:
        ext = LIPSYNC_REF_IMAGE_TYPES.get((f.content_type or "").lower())
        if not ext:
            continue
        data = await f.read()
        if not data or len(data) > LIPSYNC_REF_IMAGE_MAX_BYTES:
            continue
        (OUTPUT_DIR / f"lipsync_ref_{job_id}_{saved}{ext}").write_bytes(data)
        saved += 1
    if saved == 0:
        return JSONResponse({"error": "No valid photos were saved -- use JPG, PNG, or WebP, under 20MB each."}, status_code=400)
    return {"status": "ok", "count": saved}

@app.post("/api/lipsync")
def lipsync(req: LipSyncRequest, request: Request):
    guard = _job_guard(request, req.job_id, allow_empty=False)
    if guard is not None:
        return guard
    if not LIPSYNC_ENABLED:
        return JSONResponse({"error": "Lip-sync is temporarily unavailable. Please check back soon."}, status_code=503)
    if _rate_limited(request, "lipsync", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    uid, error = _paid_uid(request)
    if error is not None:
        return error
    from shortdub_operations import RequestProblem
    from lipsync_operations import paths, work, run as run_intent
    try:
        paths(OUTPUT_DIR, req.job_id, getattr(req, "operation_id", None))
    except RequestProblem as ex:
        return JSONResponse(ex.payload, status_code=ex.status)
    def execute():
        try:
            video = find_job_video(req.job_id)
            if video is None:
                raise RequestProblem("We couldn't find your original video. Please upload it again.", 404, operation_complete=True)
            resolution = lipsync_res(req.resolution)
            watermark = _wm_needed(uid)
            photos = sorted(OUTPUT_DIR.glob(f"lipsync_ref_{req.job_id}_*")) if LIPSYNC_REF_IMAGES_ENABLED else []
            fingerprint = work(req, resolution, [video, OUTPUT_DIR / f"{req.job_id}_final_dubbed.mp3", *photos], watermark)
            def prepare():
                try:
                    duration = ffmpeg_utils.get_media_duration(video)
                except Exception:
                    raise RequestProblem("Could not determine this video's length. Please try again.", 503, operation_complete=True) from None
                if not duration or duration <= 0 or not math.isfinite(duration):
                    raise RequestProblem("Could not determine this video's length. Please try again.", 503, operation_complete=True)
                if duration < LIPSYNC_MIN_SEC or duration > LIPSYNC_MAX_SEC:
                    raise RequestProblem(f"Lip-sync only works on clips between {LIPSYNC_MIN_SEC} and {LIPSYNC_MAX_SEC} seconds.", 413, operation_complete=True)
                blocked = _storage_block(uid, video.stat().st_size, OUTPUT_DIR / f"{req.job_id}_final_lipsync.mp4")
                if blocked is not None:
                    payload = json.loads(blocked.body) if hasattr(blocked, "body") else blocked.content
                    raise RequestProblem(payload["error"], blocked.status_code, operation_complete=True)
                if LIPSYNC_TEST_MODE:
                    return 0
                cost = max(1, round(duration * lipsync_rate(_get_pricing_config().get("lipsyncCreditsPerSec", 10), resolution)))
                balance = get_credits(uid)
                if not debit_confirmed(balance):
                    raise RequestProblem("Your balance could not be checked. No generation has started.", 503)
                if balance < cost:
                    raise RequestProblem("You do not have enough credits for lip-sync. Add credits and start a new take.", 402, operation_complete=True)
                return cost
            def launch(receipt):
                jobs_progress[f"lipsync_{req.job_id}"] = {"status": "processing", "percent": 5, "message": "Preparing...",
                    "error": None, "result": None, "generation_id": None, "operation_id": receipt.stem, "operation_complete": False}
                def worker():
                    from lipsync_operations import finish
                    try:
                        lipsync_service.lipsync_worker(req.job_id, "wan3", "lipsync-2", ELEVENLABS_API_KEY, "", FAL_API_KEY,
                            DASHSCOPE_API_KEY, DASHSCOPE_WORKSPACE_ID, DASHSCOPE_REGION, resolution, watermark)
                    finally:
                        progress = jobs_progress.get(f"lipsync_{req.job_id}", {})
                        try:
                            refund_cb = None if LIPSYNC_TEST_MODE else (
                                lambda amount, operation_id: _ld_refund(uid, amount, req.job_id, operation_id, "lipsync-no-video"))
                            state = finish(receipt, OUTPUT_DIR, progress, refund=refund_cb)
                            if state == "unknown":
                                progress.update(status="error", error="The lip-sync result could not be confirmed. Please contact support before starting another take.")
                            progress.update(operation_id=receipt.stem, operation_complete=state in ("done", "failed"))
                        except Exception:
                            progress.update(status="error", error="The lip-sync result could not be saved safely. Please contact support before retrying.",
                                            operation_id=receipt.stem, operation_complete=False)
                try:
                    threading.Thread(target=worker, daemon=True).start()
                except Exception:
                    jobs_progress[f"lipsync_{req.job_id}"] = {"status": "error", "percent": 0,
                        "error": "The lip-sync request could not be confirmed. Please contact support before retrying.", "operation_complete": False}
                    raise
            return run_intent(OUTPUT_DIR, req, uid, fingerprint, prepare,
                lambda amount, operation_id: deduct_credits(uid, amount, "lipsync", req.job_id, operation_id=operation_id),
                launch, jobs_progress.get(f"lipsync_{req.job_id}"), _sb_rpc, LIPSYNC_TEST_MODE)
        except RequestProblem as ex:
            return JSONResponse(ex.payload, status_code=ex.status)
        except (OSError, ValueError, TypeError):
            return JSONResponse({"error": "The lip-sync request could not be saved safely. Please contact support before retrying."}, status_code=503)
    return _short_edit(req.job_id, execute)

@app.get("/api/progress/lipsync/{job_id}")
def lipsync_progress(job_id: str, request: Request):
    guard = _job_guard(request, job_id)
    if guard is not None:
        return guard
    progress = jobs_progress.get(f"lipsync_{job_id}")
    if progress is None:
        from lipsync_operations import saved_progress
        progress = saved_progress(OUTPUT_DIR, job_id)
    return _public_progress(progress or {"status": "not_found"})

@app.get("/api/usage/{job_id}")
def usage(job_id: str, request: Request):
    _g = _job_guard(request, job_id)
    if _g:
        return _g
    # Customers only ever see credits. The provider-side counters (tokens, characters, dollar cost) stay
    # inside usage_bucket() for the admin reports and are never sent to the browser.
    # "Credits used for this dubbing" is the real total of this project's credit history (every charge minus every
    # refund), read from the same history the Account page shows. The in-memory record only remembers the last
    # single charge, so it is just the fallback when the history cannot be read.
    import job_usage
    shown = dict(_job_charges.get(job_id, {}))
    real = job_usage.for_job(_current_uid(request), job_id, SUPABASE_URL, SUPABASE_SERVICE_KEY)
    if real is not None:
        shown.update(real)
    return shown

@app.get("/api/download/{filename}")
def download(filename: str, request: Request):
    # Only finished outputs ("<job id>_final_....") are ever served here, and
    # only to the user they belong to (was: any file name in OUTPUT_DIR, for
    # anyone, even without a login when the name ended in .mp4).
    if not _is_final_output(OUTPUT_DIR / filename):
        return JSONResponse({"error": "not found"}, status_code=404)
    _g = _job_guard(request, _job_id_from_output_path(OUTPUT_DIR / filename), allow_empty=False)
    if _g:
        return _g
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
def segment_audio(job_id: str, segment_id: str, request: Request):
    _g = _job_guard(request, job_id, allow_empty=False)
    if _g:
        return _g
    if _bad_segment_id(segment_id): return JSONResponse({"error": "bad id"}, status_code=400)
    for ext, mt2 in ((".wav", "audio/wav"), (".mp3", "audio/mpeg")):
        p = line_audio_path(OUTPUT_DIR, job_id, segment_id, "stretched", ext)
        if p.exists():
            fr = FileResponse(p, media_type=mt2); fr.headers["Cache-Control"] = "no-store"; return fr
    return JSONResponse({"error": "not found"}, status_code=404)

@app.post("/api/cleanup_voices")
def cleanup_voices(request: Request, payload: dict = {}):
    if _rate_limited(request, "cleanup_voices", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    _g = _job_guard(request, payload.get("job_id") or "")
    if _g:
        return _g
    # Some flows (new project / reset) call this with keep: [] -- meaning
    # "wipe every Cloned_/Custom_ voice in the account". That must never be
    # allowed to sweep away a voice ANY user has saved to their library
    # (user_voices), since the ElevenLabs account is shared across every
    # user of this app, not scoped to whoever's session triggered cleanup.
    # Fail CLOSED: if we can't confirm the current saved-voice list (the
    # migration not run yet, or a transient Supabase problem), skip this
    # cleanup pass entirely rather than risk permanently deleting a paying
    # user's named, saved voice -- a few extra temp voices lingering an
    # extra cycle is cheap and harmless; deleting someone's saved voice by
    # mistake is not.
    keep = list(payload.get("keep") or [])
    try:
        keep.extend(_all_saved_voice_ids())
    except Exception as ex:
        print(f"[cleanup_voices] SKIPPED sweep -- could not verify saved voice list: {ex}")
        return {"deleted": 0, "errors": ["cleanup skipped: could not verify the saved-voice list (has the user_voices migration been run?)"]}
    result = eleven_service.cleanup_cloned_voices(ELEVENLABS_API_KEY, keep)
    # Also sweep Inworld -- unconditionally, not gated on the current admin
    # Voice Engine switch, since a voice created under Inworld while the
    # switch was pointed there still needs cleaning up even after Ali
    # flips back to ElevenLabs. No-ops harmlessly if INWORLD_API_KEY was
    # never set (see inworld_service.cleanup_cloned_voices).
    inworld_result = inworld_service.cleanup_cloned_voices(INWORLD_API_KEY, keep)
    result = {
        "deleted": int(result.get("deleted", 0)) + int(inworld_result.get("deleted", 0)),
        "errors": list(result.get("errors", [])) + list(inworld_result.get("errors", [])),
    }
    # Also remove this job's downloadable voice sample file(s) -- but ONLY
    # when the caller explicitly asks for it via wipe_samples: true (a real
    # "start fresh" moment: the ðŸ§¹ clean-old-clones button, or a workspace
    # reset). This must NOT happen on the routine after-every-clone
    # auto-cleanup call app.js fires to protect the shared ElevenLabs/Inworld
    # quota (see confirmCloning's wrapper) -- that call passes this same
    # job_id moments after /api/clone just wrote these exact sample files,
    # so wiping them here deleted the Download button's file before the user
    # ever got a chance to click it. Confirmed via Railway logs 2026-09-28:
    # POST /api/clone and POST /api/cleanup_voices for the same job_id landed
    # in the same instant, and the very next requests were 404s on
    # /api/download_voice_sample for that job. keep (above) only protects
    # voice IDs at the provider level -- it has no bearing on these local
    # sample files, so it can't be used to distinguish the two cases; an
    # explicit flag is the only reliable way.
    job_id = payload.get("job_id") or ""
    if job_id and payload.get("wipe_samples"):
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
def download_voice_sample(job_id: str, speaker: str, request: Request):
    _g = _job_guard(request, job_id, allow_empty=False)
    if _g:
        return _g
    """Serves the isolated voice sample used to create a speaker's clone â€”
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
    if not uid: return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    paused = _billing_pause_response()
    if paused is not None:
        return paused
    _g = _job_guard(request, job_id)
    if _g:
        return _g
    # Which engine will actually create this voice -- resolved BEFORE the
    # authorize gate (used to be after) so the gate's ElevenLabs shared-quota
    # check only applies when ElevenLabs is actually the engine being used;
    # also feeds the cost check below so it uses THIS engine's own rate --
    # same reasoning as /api/clone above.
    engine = _active_voice_engine()
    ok, plan_or_error = _authorize_new_clones(uid, 1, engine=engine)
    if not ok:
        return JSONResponse({"error": customer_message(plan_or_error)}, status_code=402)
    nm = (file.filename or "").lower()
    if not nm.endswith((".mp3", ".wav")): return {"error": "Only MP3 or WAV files are allowed."}
    data = await file.read()
    if len(data) > 10 * 1024 * 1024: return {"error": "File too large (max 10 MB / 20 seconds)."}
    # Uploading a custom voice hits the same ElevenLabs POST /v1/voices/add
    # endpoint -- and the same paid voice-creation quota -- as "Clone Selected
    # Voices" in /api/clone above, so charge it the same admin-configurable
    # price (cloneCredits). Checked up front so a low balance is rejected
    # before spending provider quota; debited after local upload validation
    # and returned if no voice is delivered.
    cfg = _get_pricing_config()
    from credit_billing import credit_amount
    try:
        clone_cost = credit_amount(cfg.get("inworldCloneCredits" if engine == "inworld" else "cloneCredits", 5))
    except ValueError:
        return JSONResponse({"error": "The cloning price is unavailable. Please try again later."}, status_code=503)
    bal = get_credits(uid)
    if bal is not None and bal < clone_cost:
        plural = "s" if clone_cost != 1 else ""
        return JSONResponse({"error": customer_message(f"Insufficient credits (creating a custom voice costs {clone_cost} credit{plural}). Use âž• Buy.")}, status_code=402)
    import uuid as _u
    tmp = OUTPUT_DIR / f"custom_upload_{_u.uuid4().hex}.bin"
    tmp.write_bytes(data)
    debit_id = str(_u.uuid4())
    balance = deduct_credits(uid, clone_cost, "custom_voice", job_id or None, operation_id=debit_id)
    if not debit_confirmed(balance):
        tmp.unlink(missing_ok=True)
        if balance is None:
            _short_clone_settle(uid, debit_id, "cancelled", clone_cost)
        message = "Not enough credits to clone this voice." if balance is False else "We couldn't confirm the payment. No voice was cloned; please try again later."
        return JSONResponse({"error": message, "operation_id": debit_id}, status_code=402 if balance is False else 503)
    try:
        if engine == "inworld":
            res = inworld_service.add_custom_voice(job_id or "custom", speaker, tmp, INWORLD_API_KEY)
        else:
            res = eleven_service.add_custom_voice(job_id or "custom", speaker, tmp, ELEVENLABS_API_KEY)
    except Exception as e:
        from user_errors import friendly_error as _fe
        res = "ERROR: " + _fe(e, "custom voice")
    finally:
        try: tmp.unlink()
        except Exception: pass
    if not isinstance(res, str) or not res.strip() or res.startswith("ERROR"):
        settled = _short_clone_settle(uid, debit_id, "failed", clone_cost)
        refunded = clone_cost if settled is not None else 0
        return {"error": (f"The voice could not be cloned. We refunded {refunded} credits." if refunded else
                          "The voice could not be cloned. Your refund is pending and will be retried automatically."), "credits_charged": clone_cost-refunded,
                "credits_refunded": refunded, "refund_pending": clone_cost-refunded, "operation_id": debit_id}
    _short_clone_settle(uid, debit_id, "delivered", 0)
    _save_user_voice(uid, res, speaker, "", job_id or "")
    if engine == "inworld":
        _tag_voice_engine(res, engine)
    _increment_clone_usage(uid, 1)
    return {"status": "success", "voice_id": res, "credits_charged": clone_cost, "credits_refunded": 0, "operation_id": debit_id}

def account_summary(request: Request):
    uid = _current_uid(request)
    if not uid: return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    credits = get_credits(uid)
    if credits is None:
        credits = int(_get_pricing_config().get("freeCredits", 100))
    return {"credits": credits, "purchases": [], "spends": []}

@app.get("/account")
def account_page():
    # Same uiStyle injection as home() above, plus the no-cache header that
    # route already had and this one was previously missing (so an admin's
    # uiStyle flip -- or any future account.html change -- shows up without
    # needing a hard refresh, matching app.js/index.html/styles.css's
    # existing no-cache treatment).
    html = (BASE_DIR / "account.html").read_text(encoding="utf-8")
    if (_get_pricing_config().get("uiStyle") or "classic") == "new":
        html = html.replace("<body>", '<body class="ui-new">', 1)
    return HTMLResponse(html, headers=_NO_CACHE_HEADERS)


# ===== USAGE RECORDING: log every credit deduction to credit_spends =====
def _record_spend(uid, action, credits, job_id=None, generated_seconds=None):
    try:
        body = {"uid": uid, "action": action, "job_id": job_id, "credits": credits}
        # generated_seconds: duration (seconds) of the final dubbed audio this
        # spend represents â€” only set for "generate" jobs. Requires the
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
# Debit history is written atomically by lisan_atomic_debit.
threading.Thread(target=_credit_recovery, daemon=True, name="credit-recovery").start()




# ===== SUBTITLE FILE -> CORRECTS THE TRANSCRIPT (short dub page; the long dub has its own route above) =====
class SubsAlignRequest(BaseModel):
    text: str = ""
    filename: str = ""
    mode: str = "auto"
    add_missed: bool = False
    segments: List[dict] = []


@app.post("/api/subs/align")
async def subs_align_route(body: SubsAlignRequest, request: Request):
    """The editor sends its lines and the text of the user's English subtitle file; the answer is the same lines with
    the subtitle's wording in them (see subs_align.py: matched by the words, so a subtitle of a whole film with another
    clock works). Nothing is stored and nothing is charged."""
    if not _current_uid(request) and not _is_logged_in(request):
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    if len(body.segments) > 3000 or len(body.text) > subs_align.MAX_CHARS:
        return JSONResponse({"error": customer_message(subs_align.reason_message("too_big")), "reason": "too_big"}, status_code=400)
    ok, info = subs_align.check_file(body.text, body.filename)
    if not ok:
        return JSONResponse({"error": customer_message(subs_align.reason_message(info)), "reason": info}, status_code=400)
    rows = [r for r in body.segments if isinstance(r, dict)]
    new_rows, rep = await asyncio.to_thread(subs_align.correct_rows, rows, body.text, body.filename, body.mode, body.add_missed)
    if not rep.get("ok"):
        return JSONResponse({"error": customer_message(subs_align.reason_message(rep.get("reason"))), "reason": rep.get("reason")}, status_code=400)
    return {"ok": True, "segments": new_rows, "report": rep}


# ===== DUB LONG VIDEO (videos of several minutes; see longdub_service.py) =====
# Everything heavy lives in longdub_service.py; this block only wires it to
# this app's credits, e-mail and pricing settings and exposes the routes.
# Every /api/longdub/<id> route checks the job belongs to the logged-in user.
_ld_email_last = {"reason": ""}      # why the last long-dub e-mail was not sent (shown in the event log)


def _send_plain_email(to_email, subject, body_text):
    """Same Resend call as _send_expiry_email / /api/contact, for any subject
    and text. Silently does nothing when RESEND_API_KEY isn't set."""
    if not RESEND_API_KEY:
        _ld_email_last["reason"] = "RESEND_API_KEY is not set on the server"
        print("[longdub] e-mail not sent: RESEND_API_KEY is not set")
        return False
    if not to_email:
        _ld_email_last["reason"] = "no e-mail address was found for this user"
        print("[longdub] e-mail not sent: no address found for the user")
        return False
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
            try:
                service_usage_monitor.record_resend_usage(_resend_quota_header(r))
            except Exception:
                pass
            if r.status in (200, 201):
                return True
            _ld_email_last["reason"] = f"Resend answered HTTP {r.status}"
            return False
    except Exception as ex:
        detail = str(ex)
        try:
            detail += " " + ex.read().decode("utf-8", "ignore")[:200]     # the answer Resend gave (HTTPError)
        except Exception:
            pass
        _ld_email_last["reason"] = f"Resend send failed: {detail}"[:300]
        print(f"[longdub] Resend send failed: {detail}")
        return False


def _ld_pricing():
    cfg = _get_pricing_config()
    # Long dubs always use Inworld (cloning there is unlimited, so every
    # speaker is cloned fresh and the copy is deleted when the job ends) --
    # so its rates apply whatever the admin's Voice Engine switch says.

    def _num(v, d):
        try:
            return float(v) if v is not None else float(d)
        except (TypeError, ValueError):
            return float(d)

    return {
        "fee": int(_num(cfg.get("transcribeCredits"), 3)),
        "analysis_per_min": _num(cfg.get("longDubAnalysisPerMin"), 2),
        # flat server-time fee per long dub, paid with the analysis (see longdub_service.compute_estimate)
        "flat": int(_num(cfg.get("longDubFlatCredits"), 10)),
        "chars_per_credit": int(_num(cfg.get("inworldCharsPerCredit"), 60)) or 60,
        "clone_credits": int(_num(cfg.get("inworldCloneCredits"), 5)),
        "merge_credits": int(_num(cfg.get("mergeCredits"), 1)),
        # The longest video is the longest one the speaker check can listen to (one number in speaker_vote_live, 60 minutes);
        # old persisted ten-minute settings no longer cap uploads.
        "max_min": longdub_service.speaker_vote_live.ENGINE_MAX_MIN,
        # the speaker check: two extra listening requests per video (measured about $0.05 for 7.8 minutes), flat + per minute
        "speaker_check_flat": int(_num(os.environ.get("SPEAKER_CHECK_FLAT_CREDITS"), longdub_service.SPEAKER_CHECK_FLAT)),
        "speaker_check_per_min": _num(os.environ.get("SPEAKER_CHECK_CREDITS_PER_MIN"), longdub_service.SPEAKER_CHECK_PER_MIN),
        # lip-sync: same per-second price as Step 7, and its own length limit
        "lipsync_per_sec": _num(cfg.get("lipsyncCreditsPerSec"), 40),
        "lipsync_max_min": _num(cfg.get("longDubLipsyncMaxMin"), 3),
        # music repair (music_fill.py): credits per hole in the kept music that the AI model fills; only holes really filled are charged
        "music_fill_credits": int(_num(cfg.get("musicFillCredits"), os.environ.get("MUSIC_FILL_CREDITS", 10) or 10)),
    }


def _ld_charge(uid, amount, action, job_id, seconds=None, operation_id=None):
    """A long-video charge. The operation ID saved in the job makes a repeat return the earlier receipt."""
    return deduct_credits(uid, int(amount), action, job_id, None, operation_id)


def _ld_refund(uid, amount, job_id, debit_id=None, key=None):
    """Returns a confirmed receipt, or None. With the ID of the original charge the refund can be repeated safely
    (and is repeated in the background if the reply is lost); without it (a job paid before IDs existed) it is the
    older single grant. The history row is written only after the grant is confirmed."""
    import longdub_billing
    amount = int(amount)
    if amount <= 0:
        return True
    if debit_id:
        return longdub_billing.settle(_sb_rpc, DATA_DIR, uid, amount, debit_id, key or "refund")
    return longdub_billing.legacy(_sb_rpc, _record_spend, uid, amount, job_id)


def _longdub_refund_recovery():
    import longdub_billing
    while True:
        try:
            longdub_billing.reconcile(_sb_rpc, DATA_DIR)
        except Exception:
            print("[credits] long-video refund recovery will retry later")
        _time.sleep(60)


threading.Thread(target=_longdub_refund_recovery, daemon=True, name="longdub-refund-recovery").start()


def _ld_email_address(uid):
    """The user's e-mail: the profiles table first, then the sign-in account
    itself (Supabase auth), so a missing profile e-mail can't silence a job's mail."""
    addr = _email_for_uid(uid)
    if addr:
        return addr
    if uid and SUPABASE_URL and SUPABASE_SERVICE_KEY:
        try:
            import urllib.parse as _uparse
            req = urllib.request.Request(f"{SUPABASE_URL}/auth/v1/admin/users/{_uparse.quote(str(uid), safe='')}",
                                         headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                addr = (json.load(r).get("email") or "").strip() or None
            if addr:
                print("[longdub] profile had no e-mail; used the sign-in account's address")
            return addr
        except Exception as ex:
            print(f"[longdub] could not look up the user's e-mail: {ex}")
    return None


def _ld_email(uid, subject, text):
    _ld_email_last["reason"] = ""
    return _send_plain_email(_ld_email_address(uid), subject, text)


# ---- Permanent record of every step (table long_dub_events, SQL sent with
# v1.52.0). Written by one background thread so a slow database never slows a
# job down; a failed write is printed to the server log, never raised.
import queue as _queue
_ld_event_q = _queue.Queue()


def _ld_event_writer():
    while True:
        row = _ld_event_q.get()
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    f"{SUPABASE_URL}/rest/v1/long_dub_events",
                    data=json.dumps(row).encode("utf-8"),
                    headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                             "Content-Type": "application/json", "Prefer": "return=minimal"},
                    method="POST")
                urllib.request.urlopen(req, timeout=8).read()
                break
            except Exception as ex:
                if attempt == 2:
                    print(f"[longdub] event NOT saved (has the long_dub_events SQL been run?): {row} -> {_http_error_detail(ex)}")
                else:
                    _time.sleep(2)


threading.Thread(target=_ld_event_writer, daemon=True).start()


def _ld_log_event(uid, job_id, step, status, detail="", credits=None):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    row = {"job_id": job_id, "uid": uid, "step": step, "status": status, "detail": str(detail or "")[:1500]}
    if credits is not None:
        row["credits"] = int(credits)
    _ld_event_q.put(row)


def _ld_is_studio(uid):
    """Dub Long Video is a Studio-plan feature: an active subscription whose
    plan key or plan name says 'studio'."""
    prof = _read_subscription_profile(uid) or {}
    if prof.get("subscription_status") != "active":
        return False
    key = str(prof.get("subscription_plan_key") or "").strip().lower()
    if "studio" in key:
        return True
    try:
        plan = _get_subscription_plan(key) or {}
    except Exception:
        plan = {}
    return "studio" in str(plan.get("name") or "").lower()


def _ld_allowed(uid):
    if LONGDUB_ALL_TIERS or _ld_is_studio(uid):
        return True, ""
    return False, "Dub Long Video is available with the Studio plan. Upgrade from the Buy menu or the Pricing page."


def _ld_capacity(job, need):
    with longdub_service.account_lock(job['uid']):
        return _ld_reserve_capacity(job, need)


def _ld_reserve_capacity(job, need):
    blocked = _storage_block(job["uid"], need, reservation_id=job["id"])
    if blocked is not None:
        detail = json.loads(blocked.body)
        return False, detail.get("error", "Storage unavailable"), blocked.status_code
    seconds = longdub_service._total_secs(job)
    scratch = max(disk_guard.WORK_LONG_GB, seconds * 44100 * 4 * 8 / _GB + 0.5)
    ok, _ = disk_guard.check(int(job.get("size") or 0), scratch, _is_final_output)
    if not ok:
        return False, disk_guard.REFUSAL_MESSAGE, 503
    job['reserved_output_bytes'] = max(0, int(need))
    job['capacity_reserved'] = True
    longdub_service._save(job)
    return True, "", 200


longdub_service.configure(capacity=_ld_capacity, get_credits=get_credits, charge=_ld_charge, refund=_ld_refund, watermark=_wm_needed,
                          send_email=_ld_email, email_error=lambda: _ld_email_last.get("reason", ""), pricing=_ld_pricing,
                          log_event=_ld_log_event, allowed=_ld_allowed)


def _ld_studio_only(uid):
    ok, msg = _ld_allowed(uid)
    return None if ok else JSONResponse({"error": msg, "studio_required": True}, status_code=403)


def _ld_job(request: Request, job_id: str):
    """(uid, job, error_response) -- error_response is set when the caller
    isn't logged in or the job isn't theirs (404 either way, so job ids of
    other users are never confirmed to exist)."""
    uid = _current_uid(request)
    if not uid:
        return None, None, JSONResponse({"error": "Please log in to continue."}, status_code=401)
    job = longdub_service.load_job(job_id)
    if not job or job.get("uid") != uid:
        return uid, None, JSONResponse({"error": "We couldn't find that project."}, status_code=404)
    return uid, job, None


@app.get("/dub-long")
def dub_long_page():
    html = (BASE_DIR / "dub_long.html").read_text(encoding="utf-8")
    if (_get_pricing_config().get("uiStyle") or "classic") == "new":
        html = html.replace("<body>", '<body class="ui-new">', 1)
    return HTMLResponse(html, headers=_NO_CACHE_HEADERS)


@app.get("/api/longdub/config")
def longdub_config(request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    p = _ld_pricing()
    return {
        "max_min": p["max_min"], "min_sec": longdub_service.MIN_SEC,
        "chunk_bytes": longdub_service.CHUNK_BYTES,
        "max_upload_mb": longdub_service.MAX_UPLOAD_BYTES // 1048576,
        "fee": p["fee"], "analysis_per_min": p["analysis_per_min"], "flat": p["flat"],
        "speaker_check_flat": p["speaker_check_flat"], "speaker_check_per_min": p["speaker_check_per_min"],
        "credits": get_credits(uid), "studio": bool(LONGDUB_ALL_TIERS or _ld_is_studio(uid)), "watermark": bool(_wm_needed(uid)),
        "max_speakers": longdub_service.MAX_SPEAKERS, "terms_version": longdub_service.TERMS_VERSION,
        "lipsync": {"available": longdub_service.lipsync_available(), "per_sec": p["lipsync_per_sec"],
                    "rates": lipsync_rates(p["lipsync_per_sec"]), "max_min": p["lipsync_max_min"]},
        "music_fill": {"available": bool(FAL_API_KEY) and longdub_service.music_fill.ENABLED, "credits": p["music_fill_credits"]},
    }


@app.get("/api/longdub")
def longdub_list(request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    return {"jobs": [customer_payload(longdub_service.public_view(j)) for j in longdub_service.list_jobs_for_uid(uid)],
            "credits": get_credits(uid)}


class LongDubInit(BaseModel):
    filename: str = ""
    size: int = 0
    speakers: int = 2
    lipsync: bool = False
    lip_res: str = ""            # "480P" / "720P" / "1080P"; empty = 720P
    name: str = ""
    description: str = ""


@app.post("/api/longdub/init")
def longdub_init(body: LongDubInit, request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    paused = _billing_pause_response()
    if paused is not None:
        return paused
    blocked = _ld_studio_only(uid)
    if blocked:
        return blocked
    _vid = str(body.filename or "").lower().endswith(tuple(longdub_service.VIDEO_EXTS))
    _blk = _storage_block(uid, dub_review.output_budget(body.size if _vid else 0, 0, _vid))
    if _blk is not None:
        return _blk          # no point in uploading a big file that could not be saved
    _dg_ok, _ = disk_guard.check(int(body.size or 0), disk_guard.WORK_LONG_GB, _is_final_output)
    if not _dg_ok:
        return JSONResponse({"error": customer_message(disk_guard.REFUSAL_MESSAGE), "capacity": True}, status_code=503)
    job, err = longdub_service.init_upload(uid, body.filename, body.size, body.speakers, body.lipsync,
                                            body.name, body.description, body.lip_res)
    if err:
        return JSONResponse({"error": customer_message(err[0])}, status_code=err[1])
    return customer_payload(longdub_service.public_view(job))


@app.put("/api/longdub/{job_id}/chunk")
async def longdub_chunk(job_id: str, index: int, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    try:
        clen = int(request.headers.get("content-length") or 0)
    except ValueError:
        clen = 0
    if clen > longdub_service.CHUNK_BYTES + 4096:
        return JSONResponse({"error": "The upload was interrupted. Please try again."}, status_code=413)
    try:
        data = await request.body()
    except ClientDisconnect:
        # The browser dropped the connection part-way through this chunk (a phone changing network, a closed
        # laptop lid, Safari pausing a background tab). Nothing has been written yet -- the chunk is only saved after
        # it has arrived whole -- and the page already sends the same chunk again, so this is not an error.
        return JSONResponse({"error": "The upload was interrupted. Please try again."}, status_code=400)
    ok, msg = await asyncio.to_thread(longdub_service.write_chunk, job, index, data)
    if not ok:
        return JSONResponse({"error": customer_message(msg)}, status_code=400)
    _held = job.get("reattach") if job.get("status") == "editing" and job.get("reattach") else job
    return {"ok": True, "received_count": len(_held.get("received", []))}


@app.post("/api/longdub/{job_id}/finish")
def longdub_finish(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    if job.get("reattach") or (job.get("status") == "analyzing" and job.get("restoring")):
        ok, e = longdub_service.finish_restore(job, uid)
    else:
        ok, e = longdub_service.finish_upload(job, uid)
    if not ok:
        return JSONResponse({"error": customer_message(e[0])}, status_code=e[1])
    v = customer_payload(longdub_service.public_view(longdub_service.load_job(job_id) or job))
    v["credits"] = get_credits(uid)
    return v


class LongDubProject(BaseModel):
    name: str = ""
    description: str = ""


@app.put("/api/longdub/{job_id}/project")
def longdub_project_update(job_id: str, body: LongDubProject, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, e = longdub_service.update_project(job, body.name, body.description)
    if not ok:
        return JSONResponse({"error": customer_message(e[0])}, status_code=e[1])
    return customer_payload(longdub_service.public_view(job))


class LongDubSubtitle(BaseModel):
    text: str = ""
    filename: str = ""
    mode: str = "auto"
    add_missed: bool = False


@app.post("/api/longdub/{job_id}/subtitle")
def longdub_subtitle_attach(job_id: str, body: LongDubSubtitle, request: Request):
    """Optional: the English subtitle of this video. Used by the analysis to correct the transcript (see subs_align.py)."""
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, e = longdub_service.attach_subtitle(job, body.text, body.filename, body.mode, body.add_missed)
    if not ok:
        return JSONResponse({"error": customer_message(e[0])}, status_code=e[1])
    return customer_payload(longdub_service.public_view(job))


@app.delete("/api/longdub/{job_id}/subtitle")
def longdub_subtitle_remove(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, e = longdub_service.remove_subtitle(job)
    if not ok:
        return JSONResponse({"error": customer_message(e[0])}, status_code=e[1])
    return customer_payload(longdub_service.public_view(job))


class LongDubLipRes(BaseModel):
    resolution: str = ""


@app.post("/api/longdub/{job_id}/lipres")
def longdub_lipres(job_id: str, body: LongDubLipRes, request: Request):
    """Change the lip-sync resolution of a project that is not paid for yet."""
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    blocked = _ld_studio_only(uid)
    if blocked:
        return blocked
    ok, e = longdub_service.set_lipsync_resolution(job, uid, body.resolution)
    if not ok:
        return JSONResponse({"error": customer_message(e[0])}, status_code=e[1])
    return customer_payload(longdub_service.public_view(job))


@app.post("/api/longdub/{job_id}/redo")
def longdub_redo(job_id: str, request: Request):
    """Redo a finished project: a new project with the same lines, translations and options.
    Costs nothing here -- the user attaches the original file again, sees the exact price and confirms."""
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    blocked = _ld_studio_only(uid)
    if blocked:
        return blocked
    ok, res = longdub_service.redo_project(job, uid)
    if not ok:
        return JSONResponse({"error": customer_message(res[0])}, status_code=res[1])
    return customer_payload(longdub_service.public_view(res))


@app.post("/api/longdub/{job_id}/park")
def longdub_park(job_id: str, request: Request):
    """Save and close: the text stays, the video and audio copies leave the server. Costs nothing."""
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, e = longdub_service.park_job(job, "user")
    if not ok:
        return JSONResponse({"error": customer_message(e[0])}, status_code=e[1])
    return customer_payload(longdub_service.public_view(job))


class LongDubReattach(BaseModel):
    filename: str = ""
    size: int = 0


@app.post("/api/longdub/{job_id}/reattach")
def longdub_reattach(job_id: str, body: LongDubReattach, request: Request):
    """The user picked the original file again for a saved project (upload then uses the same /chunk and /finish routes)."""
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    blocked = _ld_studio_only(uid)
    if blocked:
        return blocked
    _dg_ok, _ = disk_guard.check(int(body.size or 0), disk_guard.WORK_LONG_GB, _is_final_output)
    if not _dg_ok:
        return JSONResponse({"error": customer_message(disk_guard.REFUSAL_MESSAGE), "capacity": True}, status_code=503)
    ok, e = longdub_service.restore_init(job, uid, body.filename, body.size)
    if not ok:
        return JSONResponse({"error": customer_message(e[0])}, status_code=e[1])
    return customer_payload(longdub_service.public_view(job))


class LongDubAccept(BaseModel):
    agree: bool = False


@app.post("/api/longdub/{job_id}/accept")
def longdub_accept(job_id: str, body: LongDubAccept, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    paused = _billing_pause_response()
    if paused is not None:
        return paused
    blocked = _ld_studio_only(uid)
    if blocked:
        return blocked
    ok, e = longdub_service.accept(job, uid, body.agree)
    if not ok:
        return JSONResponse({"error": customer_message(e[0])}, status_code=e[1])
    v = customer_payload(longdub_service.public_view(job))
    v["credits"] = get_credits(uid)
    return v


@app.get("/api/longdub/{job_id}")
def longdub_status(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    v = customer_payload(longdub_service.public_view(job))
    if job.get("status") == "uploading":
        v["received"] = sorted(job.get("received", []))
    elif job.get("status") == "editing" and job.get("reattach"):
        v["received"] = sorted(job["reattach"].get("received", []))
    else:
        v["received"] = []
    return v


_LD_SPEAKER_REASONS = ("no_detected_turn", "speaker_not_supported", "weak_time_coverage", "overlapping_speech", "short_reply",
                       "word_in_gap", "word_speaker_disagrees", "word_crosses_turns", "smoothed_assignment",
                       "missing_word_times", "little_speaker_evidence", "invalid_line_times", "voters_split", "candidate_rejected")


def _ld_speaker_review(r):
    """The "check speaker" evidence saved with a line: only a known confidence, a bounded coverage number and reasons from a fixed list
    (a project made before this existed simply has none, and the editor shows nothing)."""
    conf = r.get("speaker_confidence")
    if conf not in ("low", "medium", "high"):
        return {}
    reasons = [x for x in (r.get("speaker_reasons") or []) if x in _LD_SPEAKER_REASONS][:8]
    try:
        cov = min(1.0, max(0.0, float(r.get("speaker_time_coverage") or 0.0)))
    except (TypeError, ValueError):
        cov = 0.0
    return {"speaker_confidence": conf, "speaker_reasons": reasons, "speaker_time_coverage": round(cov, 3)}


def _ld_public_rows(rows, job=None):
    # Word-level timings stay on the server -- the editor only needs the text and the times.
    unheard = set()
    if job is not None:
        try:
            unheard = longdub_service.unheard_ids(job, rows)    # lines where the AI heard no speech (added or timed by hand)
        except Exception:
            unheard = set()
    overlap_map = dub_review.overlaps(rows)
    out = []
    for r in rows:
        d = {k: r.get(k) for k in ("segment_id", "start", "end", "speaker", "speaker_id", "gender", "emotion", "text", "arabic_text")}
        d["waqf"] = r.get("waqf") or "auto"
        d["overlaps"] = overlap_map.get(r.get("segment_id"), [])
        d["emotion_review"] = dub_review.emotion_review(r)
        d["heard"] = r.get("segment_id") not in unheard
        d["manual_time"] = bool(r.get("manual_time"))
        d.update(_ld_speaker_review(r))
        out.append(d)
    return out


@app.get("/api/longdub/{job_id}/segments")
def longdub_get_segments(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    rows = _ld_public_rows(longdub_service.read_segments(job), job)
    return {"segments": rows, "status": job.get("status"), "reviewed_batches": job.get("reviewed_batches", {}),
            "batches": dub_review.batches(rows, job.get("reviewed_batches")),
            "warnings": job.get("warnings", []), "speaker_list": job.get("speaker_list", []),
            "stated_speakers": job.get("stated_speakers"), "detected_speakers": job.get("detected_speakers")}


class LongDubEdits(BaseModel):
    edits: List[dict] = []


@app.put("/api/longdub/{job_id}/segments")
def longdub_put_segments(job_id: str, body: LongDubEdits, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, res = longdub_service.update_segments(job, body.edits)
    if not ok:
        return JSONResponse({"error": customer_message(res)}, status_code=409)
    return {"ok": True, "changed": res}


class LongDubLineTime(BaseModel):
    segment_id: str = ""
    start: float = 0.0
    end: float = 0.0
    manual: bool = False


@app.post("/api/longdub/{job_id}/segments/time")
def longdub_line_time(job_id: str, body: LongDubLineTime, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, msg, rows = longdub_service.set_line_time(job, body.segment_id, body.start, body.end, body.manual)
    if not ok:
        return JSONResponse({"error": customer_message(msg)}, status_code=409)
    return {"ok": True, "segments": _ld_public_rows(rows, job)}


@app.post("/api/longdub/{job_id}/player")
async def longdub_player(job_id: str, request: Request):
    """Makes (the first time) a small playable copy of the original for the "Enter man." player."""
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    path, e = await asyncio.to_thread(longdub_service.ensure_preview, job)
    if e:
        return JSONResponse({"error": customer_message(e[0])}, status_code=e[1])
    return {"ok": True, "url": f"/api/longdub/{job_id}/media", "kind": "video" if job.get("has_video") else "audio",
            "duration": job.get("duration") or 0}


@app.get("/api/longdub/{job_id}/media")
def longdub_media(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    p = longdub_service.preview_path(job)
    if not p.exists():
        return JSONResponse({"error": "The player copy is not ready."}, status_code=404)
    return FileResponse(p, media_type="video/mp4" if job.get("has_video") else "audio/mp4",
                        headers={"Cache-Control": "private, max-age=600"})


class LongDubLineRef(BaseModel):
    segment_id: str = ""


@app.post("/api/longdub/{job_id}/segments/insert")
def longdub_line_insert(job_id: str, body: LongDubLineRef, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, msg, rows, new_id = longdub_service.insert_line(job, body.segment_id)
    if not ok:
        return JSONResponse({"error": customer_message(msg)}, status_code=409)
    return {"ok": True, "segments": _ld_public_rows(rows, job), "new_id": new_id}


class LongDubLineSplit(BaseModel):
    segment_id: str = ""
    position: int = -1


@app.post("/api/longdub/{job_id}/segments/split")
def longdub_line_split(job_id: str, body: LongDubLineSplit, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, msg, rows, new_id = longdub_service.split_line(job, body.segment_id, body.position)
    if not ok:
        return JSONResponse({"error": customer_message(msg)}, status_code=409)
    return {"ok": True, "segments": _ld_public_rows(rows, job), "new_id": new_id}


@app.post("/api/longdub/{job_id}/segments/delete")
def longdub_line_delete(job_id: str, body: LongDubLineRef, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, msg, rows = longdub_service.delete_line(job, body.segment_id)
    if not ok:
        return JSONResponse({"error": customer_message(msg)}, status_code=409)
    return {"ok": True, "segments": _ld_public_rows(rows, job)}


class LongDubSpeakers(BaseModel):
    speakers: List[dict] = []


@app.put("/api/longdub/{job_id}/speakers")
def longdub_put_speakers(job_id: str, body: LongDubSpeakers, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, msg = longdub_service.set_speakers(job, body.speakers)
    if not ok:
        return JSONResponse({"error": customer_message(msg)}, status_code=409)
    return {"ok": True, "speaker_list": job.get("speaker_list", [])}


@app.get("/api/longdub/{job_id}/preview")
def longdub_preview(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    if job.get("status") != "editing":
        return JSONResponse({"error": "This job is not open for editing."}, status_code=409)
    # Arabic words that have no tashkeel get it before the price is fixed
    # (the marks are characters, so this can raise the price).
    added, terr = longdub_service.ensure_tashkeel(job)
    if terr:
        return JSONResponse({"error": customer_message(terr)}, status_code=503)
    p = longdub_service.dub_price(job)
    p["credits"] = get_credits(uid)
    try:
        p["music"] = longdub_service.music_quote(job)
    except Exception as ex:
        p["music"] = {"repairs": 0, "max_credits": 0, "error": customer_message(str(ex))}
    p["tashkeel_added"] = added
    return customer_payload(p)


class LongDubConfirm(BaseModel):
    expected_due: int = -1
    room: str = ""
    tracks: bool = False        # also keep the dubbed voices and the music / effects as separate files
    keep_music: bool = True
    music_budget: int = 0  # user-confirmed maximum; charge only successful repairs


@app.post("/api/longdub/{job_id}/confirm")
def longdub_confirm(job_id: str, body: LongDubConfirm, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    paused = _billing_pause_response()
    if paused is not None:
        return paused
    blocked = _ld_studio_only(uid)
    if blocked:
        return blocked
    _vid = str(job.get("ext") or "").lower() in longdub_service.VIDEO_EXTS
    _need = dub_review.output_budget(job.get("size"), job.get("duration"), _vid, body.tracks)
    permitted, reason, status = _ld_capacity(job, _need)
    if not permitted:
        return JSONResponse({"error": customer_message(reason)}, status_code=status)
    ok, e = longdub_service.confirm(job, uid, body.expected_due, body.room, body.tracks, body.keep_music, body.music_budget)
    if not ok:
        extra = {}
        if e[1] == 409 and job.get("status") == "editing":
            extra = {"price": longdub_service.dub_price(job)}
        return JSONResponse({"error": customer_message(e[0]), **extra}, status_code=e[1])
    v = customer_payload(longdub_service.public_view(job))
    v["credits"] = get_credits(uid)
    return v


@app.get("/api/longdub/{job_id}/download")
def longdub_download(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    p = longdub_service.result_file(job)
    if job.get("status") != "done" or p is None:
        return JSONResponse({"error": "This file has expired or was already deleted."}, status_code=404)
    base = Path(job.get("filename") or "video").stem[:80] or "video"
    ext = p.suffix
    return FileResponse(p, media_type="video/mp4" if ext == ".mp4" else ("audio/mp4" if ext == ".m4a" else "audio/mpeg"), filename=f"{base}_dubbed{ext}")


@app.get("/api/longdub/{job_id}/track/{which}")
def longdub_track(job_id: str, which: str, request: Request):
    """The optional separate tracks of a finished dub: the dubbed voices alone, or the music and effects alone."""
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    p = longdub_service.track_file(job, which)
    if p is None:
        return JSONResponse({"error": "This file was not made, or it has expired or was deleted."}, status_code=404)
    base = Path(job.get("filename") or "video").stem[:80] or "video"
    label = {"voices": "dubbed_voices", "effects": "music_and_effects"}[which]
    return FileResponse(p, media_type="audio/mp4", filename=f"{base}_{label}.m4a")


@app.get("/api/longdub/{job_id}/subtitles")
def longdub_subtitles(job_id: str, request: Request, lang: str = "ar", fmt: str = "srt"):
    """Subtitle file (.srt or .vtt) of the lines of a long dub: Arabic, English or both. Free; nothing is generated."""
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    if lang not in subs_export.LANGS or fmt not in subs_export.FORMATS:
        return JSONResponse({"error": "Choose Arabic, English or both, and SRT or VTT."}, status_code=400)
    if job.get("status") not in ("editing", "done"):
        return JSONResponse({"error": "The subtitles are available once the lines are ready."}, status_code=409)
    text, n = subs_export.export(longdub_service.read_segments(job), lang, fmt)
    if not n:
        return JSONResponse({"error": "There is no text for this language yet."}, status_code=404)
    base = (Path(job.get("filename") or "video").stem[:80] or "video")
    fname = f"{base}_{lang}.{fmt}"
    from urllib.parse import quote as _q
    ascii_name = "".join(c if (c.isascii() and c not in '"\\;') else "_" for c in fname) or f"subtitles.{fmt}"
    return Response(
        content=("\ufeff" + text).encode("utf-8"),
        media_type=("text/vtt" if fmt == "vtt" else "application/x-subrip") + "; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{_q(fname)}"})


class LongDubGlossary(BaseModel):
    text: str = ""


@app.put("/api/longdub/{job_id}/glossary")
def longdub_glossary_put(job_id: str, body: LongDubGlossary, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, res = longdub_service.set_glossary(job, body.text[:20000])
    if not ok:
        return JSONResponse({"error": customer_message(res[0])}, status_code=res[1])
    return res


@app.get("/api/longdub/{job_id}/glossary/check")
def longdub_glossary_check(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    missing, mine = longdub_service.glossary_check(job)
    return {"missing": len(missing), "mine": len(mine)}


@app.post("/api/longdub/{job_id}/glossary/apply")
def longdub_glossary_apply(job_id: str, request: Request):
    if _rate_limited(request, "glossary", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, res = longdub_service.glossary_apply(job)
    if not ok:
        return JSONResponse({"error": customer_message(res[0])}, status_code=res[1])
    return res


class LongDubRetranslate(BaseModel):
    segment_id: str = ""


@app.post("/api/longdub/{job_id}/retranslate")
def longdub_retranslate(job_id: str, body: LongDubRetranslate, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, msg, arabic = longdub_service.retranslate_line(job, body.segment_id)
    if not ok:
        return JSONResponse({"error": customer_message(msg)}, status_code=409)
    return {"ok": True, "arabic_text": arabic, "emotion": longdub_service.line_emotion(job, body.segment_id)}


@app.post("/api/longdub/{job_id}/tashkeel")
def longdub_tashkeel(job_id: str, body: LongDubRetranslate, request: Request):
    if _rate_limited(request, "tashkeel", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, msg, arabic = longdub_service.tashkeel_line(job, body.segment_id)
    if not ok:
        return JSONResponse({"error": customer_message(msg)}, status_code=409)
    return {"ok": True, "arabic_text": arabic}


@app.delete("/api/longdub/{job_id}")
def longdub_delete(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err:
        return err
    ok, e = longdub_service.delete_job(job, uid)
    if not ok:
        return JSONResponse({"error": customer_message(e[0])}, status_code=e[1])
    return {"ok": True}


def _ld_startup():
    try:
        longdub_service.resume_all()
        longdub_service.start_housekeeping()
    except Exception as _ld_ex:
        print(f"[longdub] startup error: {_ld_ex}")


# Resume interrupted long jobs a little after start-up so the app is fully up
# (and the first normal requests aren't competing with a resumed job).
_ld_timer = threading.Timer(20.0, _ld_startup)
_ld_timer.daemon = True
_ld_timer.start()


@app.get("/api/account/summary")
def account_summary(request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
        
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
    orders = [dict(o, kind="pack") for o in _fetch_and_filter("credit_orders")]

    # Monthly plan credits (one row per paid subscription invoice, written by
    # _grant_subscription_credits) belong in the same list -- until now they
    # only showed up inside the total balance. Tolerant of the table having
    # no created_at column yet: falls back to unordered rows with no date.
    grants = []
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    for order_q in ("&order=created_at.desc", ""):
        try:
            req = _ur.Request(
                f"{SUPABASE_URL}/rest/v1/subscription_invoices?uid=eq.{uid}&select=*{order_q}&limit=200",
                headers=hdrs)
            with _ur.urlopen(req, timeout=10) as r:
                rows = json.load(r)
            grants = [{"created_at": g.get("created_at"), "credits": g.get("credits"),
                       "session_id": g.get("invoice_id"), "kind": "subscription"} for g in rows]
            break
        except Exception as e:
            print(f"[account_summary] Error fetching subscription_invoices{order_q}: {_http_err_detail(e)}")
    purchases = orders + grants
    purchases.sort(key=lambda p: p.get("created_at") or "", reverse=True)

    return {
        "credits": get_credits(uid) or 0,
        "purchases": purchases,
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
    "voices": "_final_voices.m4a",        # long dub: the dubbed voices alone (optional)
    "effects": "_final_effects.m4a",      # long dub: retained music / effects
    "corrections": "_final_corrections.m4a",
}


def _job_belongs_to_uid(uid: str, job_id: str) -> bool:
    if not uid or not job_id or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    import urllib.request as _ur
    import urllib.parse as _up
    url = f"{SUPABASE_URL}/rest/v1/credit_spends?uid=eq.{_up.quote(str(uid), safe='')}&job_id=eq.{_up.quote(str(job_id), safe='')}&select=job_id&limit=1"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            return bool(json.load(r))
    except Exception:
        return False


# ---- storage per user -------------------------------------------------------------
def _user_job_ids(uid):
    """{job_id: newest created_at} of this user's jobs (from credit_spends), or None when the lookup failed."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    import urllib.request as _ur
    url = f"{SUPABASE_URL}/rest/v1/credit_spends?uid=eq.{uid}&select=job_id,created_at&order=created_at.desc&limit=1000"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        rows = []
        for offset in range(0, 200000, 1000):
            req = _ur.Request(url + f"&offset={offset}", headers=hdrs)
            with _ur.urlopen(req, timeout=10) as r:
                page = json.load(r)
            rows.extend(page)
            if len(page) < 1000:
                break
        else:
            return None  # refuse a storage check instead of using an incomplete history
    except Exception as e:
        print("[storage] job lookup error:", e)
        return None
    latest_seen = {}
    for row in rows:
        jid = row.get("job_id")
        if not jid or "/" in jid or "\\" in jid or ".." in jid:
            continue
        ts = row.get("created_at") or ""
        if jid not in latest_seen or ts > latest_seen[jid]:
            latest_seen[jid] = ts
    return latest_seen


def _job_output_files(jid):
    return [OUTPUT_DIR / f"{jid}{suffix}" for suffix in _MY_JOB_FILE_SUFFIXES.values()]


def _fmt_storage(nbytes):
    gb = nbytes / _GB
    if gb >= 1:
        return f"{gb:.2f} GB".replace(".00 GB", " GB")
    return f"{max(0.0, nbytes) / 1048576:.0f} MB"


def _storage_summary(uid, job_ids=None):
    """{"used_bytes", "quota_bytes", "quota_gb", "percent", "full", "plan_name", "subscribed"} for one user, or None
    when the user's jobs could not be looked up (callers then never block anybody)."""
    jobs = job_ids if job_ids is not None else _user_job_ids(uid)
    if jobs is None:
        return None
    used = 0
    for project in longdub_service.list_jobs_for_uid(uid):
        jobs.setdefault(project["id"], "")
    for jid in jobs:
        for p in _job_output_files(jid):
            try:
                if p.exists():
                    used += p.stat().st_size
            except Exception:
                pass
    subscribed, plan_name, gb = False, "", PAYONCE_STORAGE_GB
    try:
        prof = _read_subscription_profile(uid) if SUPABASE_SERVICE_KEY else None
        if prof is None:
            return None
        if prof.get("subscription_status") == "active":
            plan = _get_subscription_plan(prof.get("subscription_plan_key") or "")
            subscribed, plan_name, gb = True, plan.get("name") or "", float(plan.get("storage_gb") or STORAGE_FALLBACK_GB)
    except Exception as ex:
        print("[storage] plan lookup error:", ex)
        return None
    # Reference clips belong to the owner and count toward storage, too.
    for project in longdub_service.list_jobs_for_uid(uid):
        refdir = longdub_service.job_dir(project["id"]) / "editrefs"
        if refdir.is_dir():
            used += sum(p.stat().st_size for p in refdir.glob("*.wav") if p.is_file())
    quota = int(gb * _GB)
    return {"used_bytes": used, "quota_bytes": quota, "quota_gb": gb,
            "percent": round(100.0 * used / quota, 1) if quota > 0 else 100.0,
            "full": used >= quota, "plan_name": plan_name, "subscribed": subscribed}


def _storage_block(uid, need_bytes=0, replaces=None, reservation_id=None):
    """None when a new file of about need_bytes may be saved, else a 409 JSONResponse telling the user to delete
    finished files first. `replaces` = a file this job will overwrite (its size is given back). Guests and lookup
    failures are never blocked."""
    if not uid:
        return None
    try:
        sm = _storage_summary(uid)
    except Exception as ex:
        print("[storage] check failed:", ex)
        return JSONResponse({"error": "We could not check available storage. Please retry before starting."}, status_code=503)
    if sm is None:
        return JSONResponse({"error": "We could not check available storage. Please retry before starting."}, status_code=503)
    used, quota = sm["used_bytes"], sm["quota_bytes"]
    for project in longdub_service.list_jobs_for_uid(uid):
        reserved = project.get('status') in ('accepted', 'analyzing', 'confirmed', 'dubbing') or (
            project.get('capacity_reserved') and project.get('status') in ('uploading', 'estimated', 'editing') and not project.get('parked_at'))
        if project.get("id") != reservation_id and reserved:
            used += int(project.get("reserved_output_bytes") or 0)
    try:
        if replaces is not None and Path(replaces).exists():
            used = max(0, used - Path(replaces).stat().st_size)
    except Exception:
        pass
    need = max(0, int(need_bytes or 0))
    tail = ("Delete some finished files on your Account page to free space" +
            ("" if sm["subscribed"] and sm["plan_name"] == "Studio" else ", or upgrade your plan for more storage") + ".")
    if used >= quota:
        msg = f"Your storage is full ({_fmt_storage(used)} of {_fmt_storage(quota)} used). {tail}"
    elif need and used + need > quota:
        msg = (f"Not enough storage: this needs about {_fmt_storage(need)} but only {_fmt_storage(quota - used)} is free "
               f"({_fmt_storage(used)} of {_fmt_storage(quota)} used). {tail}")
    else:
        return None
    return JSONResponse({"error": msg, "storage_full": True, "storage": sm}, status_code=409)


@app.get("/api/storage")
def my_storage(request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    sm = _storage_summary(uid)
    return {"storage": sm}


@app.get("/api/my_jobs")
def my_jobs(request: Request):
    """Every job of this user's that still has a finished output on disk --
    backs the Account page's file list. Reuses credit_spends for ownership
    instead of a new table; a job with no final file left (never produced
    one, or past the 30-day window) is simply left out. Also returns the
    user's storage meter (used / quota)."""
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    latest_seen = _user_job_ids(uid)
    if latest_seen is None:
        return {"jobs": [], "storage": None}

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
            "bytes": sum(p.stat().st_size for p in existing) + sum(
                (OUTPUT_DIR / f"{jid}{_MY_JOB_FILE_SUFFIXES[k]}").stat().st_size for k in ("voices", "effects")
                if (OUTPUT_DIR / f"{jid}{_MY_JOB_FILE_SUFFIXES[k]}").exists()),
            "expires_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(newest_mtime + CLEANUP_FINAL_OUTPUT_DAYS * 86400)),
        })
    jobs.sort(key=lambda j: j["created_at"] or "", reverse=True)
    return {"jobs": jobs, "storage": _storage_summary(uid, latest_seen)}


@app.get("/api/my_jobs/{job_id}/{kind}")
def my_job_file(job_id: str, kind: str, request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
    suffix = _MY_JOB_FILE_SUFFIXES.get(kind)
    if not suffix or "/" in job_id or "\\" in job_id or ".." in job_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _job_belongs_to_uid(uid, job_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    p = OUTPUT_DIR / f"{job_id}{suffix}"
    if not p.exists():
        return JSONResponse({"error": "This file has expired or was already deleted."}, status_code=404)
    media_type = "audio/mpeg" if kind == "audio" else ("audio/mp4" if kind in ("voices", "effects") else "video/mp4")
    return FileResponse(p, media_type=media_type, filename=p.name)


@app.delete("/api/my_jobs/{job_id}")
def delete_my_job(job_id: str, request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
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
    try:
        sm = _storage_summary(uid)
    except Exception:
        sm = None
    return {"status": "success", "removed": removed, "storage": sm}


def account_summary_diag(request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "Please log in to continue."}, status_code=401)
        
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


# --- SEARCH ENGINES: robots.txt + sitemap.xml ---------------------------------
# Public pages are listed for Google; the app, the admin pages, the API and the
# login/gate pages are not. SITE_URL (Railway variable) overrides the address.
SITE_URL = (os.environ.get("SITE_URL") or "https://lisanai.org").rstrip("/")
SITEMAP_PAGES = ["/", "/pricing", "/help", "/privacy", "/terms"]


@app.get("/robots.txt")
def robots_txt():
    from fastapi.responses import PlainTextResponse
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /admin\n"
        "Disallow: /app$\n"
        "Disallow: /app/\n"
        "Disallow: /account\n"
        "Disallow: /auth/\n"
        "Disallow: /gate\n"
        "Disallow: /debug-keys\n"
        "\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    return PlainTextResponse(body, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sitemap.xml")
def sitemap_xml():
    from fastapi.responses import Response
    urls = "".join(f"  <url><loc>{SITE_URL}{p}</loc></url>\n" for p in SITEMAP_PAGES)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + '</urlset>\n')
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})

    
    # ============================================================
# ADMIN ROUTES â€” protected by APP_PASSWORD env var
# ============================================================
import hmac
import time
import secrets

# In-memory admin session tokens (valid for 4 hours)
_ADMIN_TOKENS = {}
ADMIN_TOKEN_TTL = 4 * 3600  # 4 hours

# ---- Durable backing store for admin sessions, same reasoning as
# _persist_session/_restore_session_from_db up in AUTH: _ADMIN_TOKENS is
# plain in-memory state, wiped on every Railway restart, which used to
# force a fresh /admin login after every deploy even mid-session.
# Needs one new Supabase table (run once in the SQL editor):
#   CREATE TABLE IF NOT EXISTS admin_sessions (
#     token text PRIMARY KEY,
#     created_at timestamptz DEFAULT now()
#   );
def _persist_admin_session(token):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not token:
        return

    def _run():
        import urllib.request as _ur
        body = json.dumps({"token": token}).encode("utf-8")
        req = _ur.Request(
            f"{SUPABASE_URL}/rest/v1/admin_sessions",
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
            print(f"[admin-session] could not persist session: {ex}")

    threading.Thread(target=_run, daemon=True).start()


def _restore_admin_session_from_db(token):
    if not token or not _SAFE_TOKEN_RE.fullmatch(str(token)):
        return None
    """Returns the session's created_at as epoch seconds if the token
    exists and is still within ADMIN_TOKEN_TTL, else None. The expiry
    check happens server-side in the query itself (created_at=gt.<cutoff>)
    so an already-expired row is never even fetched."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not token:
        return None
    import urllib.parse as _uparse
    try:
        cutoff = _uparse.quote(
            (datetime.now(timezone.utc) - timedelta(seconds=ADMIN_TOKEN_TTL)).isoformat(), safe="")
        url = (f"{SUPABASE_URL}/rest/v1/admin_sessions?token=eq.{token}"
               f"&created_at=gt.{cutoff}&select=created_at")
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.load(r)
    except Exception as ex:
        print(f"[admin-session] could not restore session: {ex}")
        return None
    if not rows:
        return None
    try:
        return datetime.fromisoformat(rows[0]["created_at"].replace("Z", "+00:00")).timestamp()
    except Exception:
        # Found a valid, non-expired row but couldn't parse its timestamp --
        # treat it as freshly restored rather than reject a legitimate login.
        return time.time()


def _admin_check(request):
    """Returns True if request has a valid admin token."""
    tok = request.headers.get("X-Admin-Token", "")
    if not tok:
        return False
    if tok not in _ADMIN_TOKENS:
        restored_ts = _restore_admin_session_from_db(tok)
        if restored_ts is None:
            return False
        _ADMIN_TOKENS[tok] = restored_ts
    # Check expiry
    if time.time() - _ADMIN_TOKENS[tok] > ADMIN_TOKEN_TTL:
        del _ADMIN_TOKENS[tok]
        return False
    return True

def _get_pricing_config():
    """Returns pricing config from DB, with defaults if not set."""
    # Uses Supabase service key to read from a `pricing_config` table
    # If table doesn't exist or is empty, returns defaults
    from signup_credits import configured_amount, DEFAULT_SIGNUP_CREDITS
    defaults = {
        "freeCredits": DEFAULT_SIGNUP_CREDITS,
        "minReserve": 150,
        "maxVideoMin": 60,
        # Real per-step charges -- these are the ones actually read by
        # _watch_and_deduct() and /api/merge_video below.
        "transcribeCredits": 3,
        "mergeCredits": 1,
        "charsPerCredit": 60,
        "cloneCredits": 5,
        # Lip-sync (Step 7, Wan 3.0 via Alibaba Model Studio) is billed per
        # second of the source video, not a flat fee. Real cost, confirmed
        # by Ali's own test: $1.73 for a 15-second clip at 720P = ~$0.1153/sec.
        # Credit packs sell for as little as $0.006/credit (the 25,000-credit
        # Studio pack), so pricing has to clear the cost even at that bulk
        # rate, not just at the $0.01/credit starter rate. 40 credits/sec
        # gives ~52% margin at the cheapest pack and up to ~71% at the
        # starter pack (2025-09 numbers -- re-check if Alibaba's per-second
        # rate changes, or if the resolution in lipsync_service.py's Wan 3.0
        # call ever changes from 720P). See /api/lipsync for how it's charged.
        # Editable live from the admin panel without a redeploy -- this is
        # only the fallback used if that panel has never set a value.
        "lipsyncCreditsPerSec": 40,
        # Google Analytics 4 Measurement ID (e.g. "G-XXXXXXXXXX"), set from
        # the admin panel. Empty string = analytics off. The landing() route
        # below injects Google's gtag.js snippet server-side into the page
        # only when this is set, so nothing is tracked until admin turns it on.
        "gaMeasurementId": "",
        "packs": DEFAULT_PACKS,
        # Monthly subscription (Ali's request, 2026-09-26): an alternative to
        # the one-time packs above -- recurring monthly charge that grants
        # this many credits every billing period AND keeps that user's
        # finished outputs for the full CLEANUP_FINAL_OUTPUT_DAYS (30) instead
        # of the short PAYONCE_OUTPUT_HOURS (48h) window pay-once/free users
        # get. See /api/billing/subscribe and the stripe_webhook handling of
        # invoice.paid / customer.subscription.* further down. Editable live
        # from the admin panel (Pricing tab) -- these are only the fallback
        # defaults used if that panel has never saved a value.
        "subscriptionName": "Pro Monthly",
        "subscriptionCredits": 4000,
        "subscriptionPriceUsd": 29.0,
        # Draft multi-tier replacement for the three flat fields just above
        # -- see DEFAULT_SUBSCRIPTION_PLANS' comment. Additive/inert until
        # /api/billing/subscribe is generalized to read it.
        "subscriptionPlans": DEFAULT_SUBSCRIPTION_PLANS,
        # On/off switch for the site-wide "private testing" access gate
        # (see SITE_GATE_PASSWORD in config.py and AuthMiddleware in this
        # file). The actual password lives only in Railway's env var --
        # this is just whether that gate is currently enforced, so Ali can
        # flip it off/on from the admin panel without a Railway trip.
        # Defaults to True so setting the env var alone reproduces the
        # original always-on behavior until someone changes this in admin.
        "siteGateEnabled": True,
        # Independent on/off switches for the two automatic usage-alert
        # emails -- see _eleven_alerts_enabled / _railway_alerts_enabled
        # above. Both default to True (send them) so nothing changes until
        # Ali visits admin and turns one off. Only gates the EMAIL; polling
        # and the admin dashboard's live numbers are unaffected.
        "elevenAlertsEnabled": True,
        "railwayAlertsEnabled": True,
        # Third e-mail switch (Oct 2026): "the server disk is getting full".
        # See disk_guard.py. Defaults to True like the other two.
        "diskAlertsEnabled": True,
        # Which voice engine NEW clones + generations use -- "elevenlabs"
        # (default, unchanged behavior) or "inworld" (added 2026-09-27, see
        # inworld_service.py). A voice already cloned keeps using whichever
        # engine created it forever (_voice_engines_for_ids) -- this switch
        # only decides what happens going forward, so flipping it back and
        # forth never breaks an existing saved voice.
        "voiceEngine": "elevenlabs",
        # Inworld's OWN per-step rates, separate from charsPerCredit/
        # cloneCredits above (which are ElevenLabs' rates, kept under their
        # original names for backward compatibility -- no DB migration
        # needed for those two). Added 2026-09-27 because the two engines'
        # real costs are genuinely different (see the research notes in this
        # session's history) -- a single shared rate would over- or under-
        # charge whichever engine it wasn't tuned for. _watch_and_deduct()
        # below reads each engine's own char count from usage_bucket() and
        # applies its own rate; /api/clone and /api/upload_custom_voice pick
        # the matching clone rate by engine. Defaulted equal to ElevenLabs'
        # own defaults as a safe starting point -- Ali should verify/adjust
        # these once real Inworld invoice data is in (per this session's
        # established pattern: trust the vendor's own billing over a guess).
        "inworldCharsPerCredit": 60,
        "inworldCloneCredits": 5,
        # Custom-voice storage slots on the Inworld plan (100 on On-Demand).
        # Inworld has no API that reports this, so it's set by hand here and
        # used by service_usage_monitor to show "used / limit" and to send the
        # "slots running out" alert email. Raise it when the plan is upgraded.
        "inworldSlotLimit": 100,
        # Which visual skin the customer-facing app pages (index.html /app,
        # account.html /account) use -- "classic" (default, today's live
        # design, unchanged) or "new" (the ElevenLabs-inspired redesign
        # reviewed at https://claude.ai/artifact/7bxXu3iYVkriAssDK4QvNn,
        # added 2026-09-28). Purely cosmetic: home()/account_page() below
        # set class="ui-new" on <body> server-side when this is "new", and
        # styles.css's body.ui-new block re-tints CSS custom properties --
        # same mechanism as the existing dark-mode toggle, nothing
        # functional changes either way. Flippable live from the admin
        # panel's "App Appearance" card -- takes effect on next page load,
        # no redeploy needed.
        "uiStyle": "classic",
        # Dub Long Video (longdub_service.py): longest video allowed, in
        # minutes, and the per-minute charge for transcribing + speaker
        # detection + translating it. Both editable in the admin panel.
        "longDubMaxMin": 60,
        "longDubAnalysisPerMin": 2,
        "longDubFlatCredits": 10,
        "longDubLipsyncMaxMin": 3,
        # Credits charged per cent of Gemini cost (translate, emotions and the other Gemini calls of a short dub).
        # 1 = a credit per cent, as before. Used by the Generate charge and by the button prices (see /api/pricing).
        "geminiCreditsPerCent": 1.0,
        # Parallel jobs, gated by memory (see whisper_service.CONC): max jobs at once, real memory one job can reach (GB),
        # the ceiling as % of the server's memory limit, and the limit itself in GB (0 = read it from the container).
        "concurrency": {"max": 2, "needGb": 5.0, "pct": 80, "limitGb": 0},
        # The AI helper (chat box): on/off, the whole site's daily spending cap in dollars, daily messages per signed-in user / per guest.
        "assistant": {"enabled": True, "dailyBudgetUsd": 2.0, "userDailyMsgs": 60, "guestDailyMsgs": 15, "notes": "", "creditsPerCent": 1.0},
    }
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return defaults
    try:
        import urllib.request as _ur
        # order=updated_at.desc&limit=1 -- this table is meant to hold exactly
        # one row (id="singleton"), but nothing in Supabase actually enforces
        # that uniqueness, and the upsert in _save_pricing_config() below can
        # only truly merge into the existing row if "id" is a real primary/
        # unique key there. If it isn't, every save quietly INSERTs another
        # "singleton" row instead of updating one, and a plain SELECT with no
        # ORDER BY returns them in whatever order Postgres feels like -- which
        # is exactly the "admin edit shows up, then reverts" flicker Ali saw
        # with the subscription name. Explicitly taking the most-recently-
        # updated row makes every read deterministic regardless of whether
        # duplicates exist underneath; it's a no-op if there's truly only one
        # row. Worth checking Supabase's table editor for multiple rows with
        # id="singleton" -- if there are several, a UNIQUE constraint on id
        # would stop new ones from being created (this fix only papers over
        # existing duplicates, it doesn't prevent new ones).
        url = f"{SUPABASE_URL}/rest/v1/pricing_config?id=eq.singleton&select=*&order=updated_at.desc&limit=1"
        hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=5) as r:
            rows = json.load(r)
        if rows and isinstance(rows, list) and len(rows) > 0:
            row = rows[0]
            return {
                "freeCredits": configured_amount(row.get("free_credits", defaults["freeCredits"])),
                "minReserve": row.get("min_reserve", defaults["minReserve"]),
                "maxVideoMin": row.get("max_video_min", defaults["maxVideoMin"]),
                "transcribeCredits": row.get("transcribe_credits", defaults["transcribeCredits"]),
                "mergeCredits": row.get("merge_credits", defaults["mergeCredits"]),
                "charsPerCredit": row.get("chars_per_credit", defaults["charsPerCredit"]),
                "cloneCredits": row.get("clone_credits", defaults["cloneCredits"]),
                "lipsyncCreditsPerSec": row.get("lipsync_credits_per_sec", defaults["lipsyncCreditsPerSec"]),
                "gaMeasurementId": row.get("ga_measurement_id", defaults["gaMeasurementId"]),
                "packs": row.get("packs", defaults["packs"]),
                # "or" (not a plain .get default) -- once the column exists,
                # an untouched singleton row has it present but NULL, and
                # .get()'s default only fires when the key is missing
                # entirely, not when its value is None.
                "subscriptionName": row.get("subscription_name") or defaults["subscriptionName"],
                "subscriptionCredits": row.get("subscription_credits") or defaults["subscriptionCredits"],
                "subscriptionPriceUsd": row.get("subscription_price_usd") or defaults["subscriptionPriceUsd"],
                "subscriptionPlans": row.get("subscription_plans") or defaults["subscriptionPlans"],
                # NOT the "or" pattern used above -- this is a boolean, and
                # "False or True" would wrongly become True, silently
                # ignoring an admin who turned the gate off. None (column
                # missing OR present-but-NULL) is the only case that should
                # fall back to the default; an explicit True/False from the
                # admin panel must always win.
                "siteGateEnabled": defaults["siteGateEnabled"] if row.get("site_gate_enabled") is None else bool(row.get("site_gate_enabled")),
                "elevenAlertsEnabled": defaults["elevenAlertsEnabled"] if row.get("eleven_alerts_enabled") is None else bool(row.get("eleven_alerts_enabled")),
                "railwayAlertsEnabled": defaults["railwayAlertsEnabled"] if row.get("railway_alerts_enabled") is None else bool(row.get("railway_alerts_enabled")),
                "diskAlertsEnabled": defaults["diskAlertsEnabled"] if row.get("disk_alerts_enabled") is None else bool(row.get("disk_alerts_enabled")),
                "voiceEngine": row.get("voice_engine") or defaults["voiceEngine"],
                "inworldCharsPerCredit": row.get("inworld_chars_per_credit") or defaults["inworldCharsPerCredit"],
                "inworldCloneCredits": row.get("inworld_clone_credits") or defaults["inworldCloneCredits"],
                "inworldSlotLimit": row.get("inworld_slot_limit") or defaults["inworldSlotLimit"],
                # "or" (not a plain .get default), same reasoning as
                # siteGateEnabled above but for a string column: before the
                # ui_style migration is run, or on an untouched row, this
                # column simply isn't present in `row` and .get() returns
                # None, falling back to "classic" -- safe either way.
                "uiStyle": row.get("ui_style") or defaults["uiStyle"],
                "longDubMaxMin": row.get("long_dub_max_min") or defaults["longDubMaxMin"],
                "longDubAnalysisPerMin": defaults["longDubAnalysisPerMin"] if row.get("long_dub_analysis_per_min") is None else row.get("long_dub_analysis_per_min"),
                "longDubFlatCredits": defaults["longDubFlatCredits"] if row.get("long_dub_flat_credits") is None else row.get("long_dub_flat_credits"),
                "longDubLipsyncMaxMin": row.get("long_dub_lipsync_max_min") or defaults["longDubLipsyncMaxMin"],
                "geminiCreditsPerCent": _gemini_cpc(row.get("gemini_credits_per_cent")),
                "concurrency": _conc_cfg(row.get("concurrency")),
                "assistant": _assistant_cfg(row.get("assistant")),
            }
    except Exception as ex:
        print(f"[admin] pricing_config load error: {ex}")
    return defaults

def _conc_cfg(v):
    """Parallel-job settings from the database (a dict or JSON text), each value kept inside a safe range."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            v = None
    v = v if isinstance(v, dict) else {}

    def num(k, lo, hi, d):
        try:
            x = float(v.get(k))
        except (TypeError, ValueError):
            return d
        return min(hi, max(lo, x)) if x == x else d
    return {"max": int(num("max", 1, 4, 2)), "needGb": num("needGb", 1, 20, 5.0),
            "pct": num("pct", 30, 95, 80), "limitGb": num("limitGb", 0, 256, 0)}


def _assistant_cfg(v):
    """The AI helper's admin settings (a dict or JSON text), each value kept inside a safe range."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            v = None
    v = v if isinstance(v, dict) else {}

    def num(k, lo, hi, d):
        try:
            x = float(v.get(k))
        except (TypeError, ValueError):
            return d
        return min(hi, max(lo, x)) if x == x else d
    return {"enabled": bool(v.get("enabled", True)), "dailyBudgetUsd": num("dailyBudgetUsd", 0, 1000, 2.0),
            "userDailyMsgs": int(num("userDailyMsgs", 1, 1000, 60)), "guestDailyMsgs": int(num("guestDailyMsgs", 0, 1000, 15)),
            "notes": str(v.get("notes") or "").strip()[:6000], "creditsPerCent": num("creditsPerCent", 0, 100, 1.0)}


def _apply_assistant(cfg=None):
    try:
        assistant_service.set_settings(_assistant_cfg(cfg if cfg is not None else _get_pricing_config().get("assistant")))
    except Exception as ex:
        print(f"[assistant] could not apply the settings: {ex}")


def _apply_concurrency(cfg=None):
    """Hands the admin's parallel-job settings to the queue. Never raises."""
    try:
        whisper_service.set_concurrency(_conc_cfg(cfg if cfg is not None else _get_pricing_config().get("concurrency")))
    except Exception as ex:
        print(f"[queue] could not apply the parallel-job settings: {ex}")


def _concurrency_refresher():
    while True:
        _time.sleep(60)
        _apply_concurrency()
        _apply_assistant()


def _gemini_cpc(v):
    """Credits per cent of Gemini cost: a number from 0.1 to 100, 1 when missing or unreadable."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 1.0
    return min(100.0, max(0.1, x)) if x == x else 1.0


def _save_pricing_config(config):
    """Saves pricing config to DB (upsert â€” creates the singleton row if it
    doesn't exist yet, updates it if it does). Previously this used PATCH,
    which only updates an EXISTING row matching id=eq.singleton â€” if that
    row had never been created, PATCH silently matched zero rows and
    returned success without writing anything, so admin edits looked saved
    but never actually persisted (and public pages kept showing defaults)."""
    from credit_billing import validate_pricing
    try:
        config = validate_pricing(config)
    except ValueError as ex:
        return False, str(ex)
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False, "Supabase not configured"  # not in DB, but UI already shows current values
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
            "free_credits": config.get("freeCredits", 100),
            "min_reserve": config.get("minReserve", 150),
            "max_video_min": config.get("maxVideoMin", 60),
            "transcribe_credits": config.get("transcribeCredits", 3),
            "merge_credits": config.get("mergeCredits", 1),
            "chars_per_credit": config.get("charsPerCredit", 60),
            "clone_credits": config.get("cloneCredits", 5),
            "lipsync_credits_per_sec": config.get("lipsyncCreditsPerSec", 10),
            "ga_measurement_id": _clean_ga,
            "packs": config.get("packs", []),
            "subscription_name": config.get("subscriptionName", "Pro Monthly"),
            "subscription_credits": config.get("subscriptionCredits", 4000),
            "subscription_price_usd": config.get("subscriptionPriceUsd", 29.0),
            "subscription_plans": config.get("subscriptionPlans", []),
            "site_gate_enabled": bool(config.get("siteGateEnabled", True)),
            "eleven_alerts_enabled": bool(config.get("elevenAlertsEnabled", True)),
            "railway_alerts_enabled": bool(config.get("railwayAlertsEnabled", True)),
            "voice_engine": config.get("voiceEngine") if config.get("voiceEngine") in ("elevenlabs", "inworld") else "elevenlabs",
            "inworld_chars_per_credit": config.get("inworldCharsPerCredit", 60),
            "inworld_clone_credits": config.get("inworldCloneCredits", 5),
            "inworld_slot_limit": max(1, int(config.get("inworldSlotLimit") or 100)),
            "ui_style": config.get("uiStyle") if config.get("uiStyle") in ("classic", "new") else "classic",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }).encode("utf-8")
        # on_conflict=id -- without this, "resolution=merge-duplicates" only
        # upserts correctly if PostgREST can already tell "id" is this
        # table's unique/primary key on its own. If it can't (a very plausible
        # explanation for the flakiness reported here: an edit shows up once,
        # then reverts), every save silently INSERTs another row instead of
        # updating the one row this table is meant to hold -- explicit
        # on_conflict=id forces the ON CONFLICT target instead of leaving it
        # to guesswork. If "id" genuinely has no unique constraint in
        # Supabase, this makes the save fail loudly (surfaced to the admin
        # panel below) instead of quietly misbehaving -- that failure would
        # mean a UNIQUE constraint on pricing_config.id needs adding directly
        # in Supabase, which isn't something this code can do on its own.
        url = f"{SUPABASE_URL}/rest/v1/pricing_config?on_conflict=id"
        hdrs = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal"
        }
        req = _ur.Request(url, data=body, headers=hdrs, method="POST")
        with _ur.urlopen(req, timeout=10) as r:
            pass
        failed_settings = []
        # The two Dub Long Video settings are written in their OWN request:
        # if their columns don't exist yet (SQL not run), only this small
        # write fails -- the main save above (everything else) is unaffected.
        try:
            _ld_body = json.dumps({
                "id": "singleton",
                "long_dub_max_min": max(1, int(float(config.get("longDubMaxMin") or 10))),
                "long_dub_analysis_per_min": max(0.0, float(config.get("longDubAnalysisPerMin") if config.get("longDubAnalysisPerMin") is not None else 2)),
            }).encode("utf-8")
            with _ur.urlopen(_ur.Request(url, data=_ld_body, headers=hdrs, method="POST"), timeout=10):
                pass
        except Exception as _ld_ex:
            print(f"[admin] long-dub settings not saved (has the long_dub_* SQL been run?): {_ld_ex}")
            failed_settings.append("long-video limits and analysis price: " + _http_error_detail(_ld_ex))
        # The long-dub flat fee also gets its OWN request: its column
        # (long_dub_flat_credits) may not exist yet, and then only this write fails.
        try:
            _ff_body = json.dumps({
                "id": "singleton",
                "long_dub_flat_credits": max(0, int(float(config.get("longDubFlatCredits") if config.get("longDubFlatCredits") is not None else 10))),
            }).encode("utf-8")
            with _ur.urlopen(_ur.Request(url, data=_ff_body, headers=hdrs, method="POST"), timeout=10):
                pass
        except Exception as _ff_ex:
            print(f"[admin] long-dub flat fee not saved (has long_dub_flat_credits been added to pricing_config?): {_ff_ex}")
            failed_settings.append("long-video processing price: " + _http_error_detail(_ff_ex))
        # The disk-alert switch also gets its OWN request: if its column hasn't been
        # added yet (see the ALTER TABLE in the release notes), only this write
        # fails and the main save above is unaffected.
        try:
            _da_body = json.dumps({
                "id": "singleton",
                "disk_alerts_enabled": bool(config.get("diskAlertsEnabled", True)),
            }).encode("utf-8")
            with _ur.urlopen(_ur.Request(url, data=_da_body, headers=hdrs, method="POST"), timeout=10):
                pass
        except Exception as _da_ex:
            print(f"[admin] disk-alert switch not saved (has disk_alerts_enabled been added to pricing_config?): {_da_ex}")
            failed_settings.append("storage alerts: " + _http_error_detail(_da_ex))
        # The Gemini price factor in its own request too: if its column has not been added yet, only this write fails.
        try:
            _gc_body = json.dumps({
                "id": "singleton",
                "gemini_credits_per_cent": _gemini_cpc(config.get("geminiCreditsPerCent")),
            }).encode("utf-8")
            with _ur.urlopen(_ur.Request(url, data=_gc_body, headers=hdrs, method="POST"), timeout=10):
                pass
        except Exception as _gc_ex:
            print(f"[admin] Gemini price factor not saved (has gemini_credits_per_cent been added to pricing_config?): {_gc_ex}")
            failed_settings.append("text price factor: " + _http_error_detail(_gc_ex))
        # The AI helper's settings in their own request too (one JSON column, see the SQL in the admin page).
        try:
            _as_body = json.dumps({
                "id": "singleton",
                "assistant": _assistant_cfg(config.get("assistant")),
            }).encode("utf-8")
            with _ur.urlopen(_ur.Request(url, data=_as_body, headers=hdrs, method="POST"), timeout=10):
                pass
        except Exception as _as_ex:
            print(f"[admin] AI helper settings not saved (has the assistant column been added to pricing_config?): {_as_ex}")
            failed_settings.append("helper settings: " + _http_error_detail(_as_ex))
        # Parallel-job settings in their own request too (one JSON column, see the SQL in the admin page).
        try:
            _cc_body = json.dumps({
                "id": "singleton",
                "concurrency": _conc_cfg(config.get("concurrency")),
            }).encode("utf-8")
            with _ur.urlopen(_ur.Request(url, data=_cc_body, headers=hdrs, method="POST"), timeout=10):
                pass
        except Exception as _cc_ex:
            print(f"[admin] parallel-job settings not saved (has the concurrency column been added to pricing_config?): {_cc_ex}")
            failed_settings.append("parallel-job settings: " + _http_error_detail(_cc_ex))
        # ...and the lip-sync length limit in its own request too (its column came later).
        try:
            _ll_body = json.dumps({
                "id": "singleton",
                "long_dub_lipsync_max_min": max(1, int(float(config.get("longDubLipsyncMaxMin") or 3))),
            }).encode("utf-8")
            with _ur.urlopen(_ur.Request(url, data=_ll_body, headers=hdrs, method="POST"), timeout=10):
                pass
        except Exception as _ll_ex:
            print(f"[admin] long-dub lip-sync limit not saved (has the long_dub_lipsync_max_min SQL been run?): {_ll_ex}")
            failed_settings.append("lip-sync length limit: " + _http_error_detail(_ll_ex))
        if failed_settings:
            return False, "Some settings were saved, but these could not be saved: " + "; ".join(failed_settings) + ". Review and save again."
        return True, None
    except Exception as ex:
        detail = _http_error_detail(ex)
        print(f"[admin] pricing_config save error: {detail}")
        return False, detail

# Parallel-job settings: applied once at start-up and then every minute (and right after the admin saves).
# Placed after _get_pricing_config / _save_pricing_config so everything they use already exists.
_apply_concurrency()
assistant_service.set_key(GEMINI_API_KEY)
_apply_assistant()
threading.Thread(target=_concurrency_refresher, daemon=True, name="concurrency-refresh").start()

_admin_fail_all: List[float] = []


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
    # Global cap on top of the per-IP one: the per-IP key comes from the
    # X-Forwarded-For header, which a client can forge to look like a new
    # address on every try. Across ALL callers, no more than this many wrong
    # admin codes per window.
    _now = time.time()
    _admin_fail_all[:] = [t for t in _admin_fail_all if _now - t < 600]
    if len(_admin_fail_all) >= 30:
        return JSONResponse({"error": "Too many attempts. Try again in a few minutes."}, status_code=429)
    if not _safe_eq(code, ADMIN_PASSWORD):
        _record_login_fail(request)
        _admin_fail_all.append(_now)
        print(f"[security] wrong admin code from {_client_ip(request)}")
        return JSONResponse({"error": "invalid code"}, status_code=401)
    token = secrets.token_urlsafe(32)
    _ADMIN_TOKENS[token] = _time.time()
    _persist_admin_session(token)
    return {"token": token}
def _get_generated_minutes():
    """Aggregate generated-audio duration from credit_spends (action='generate'
    rows carry a generated_seconds field â€” see _record_spend). Returns
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


def _get_lipsync_spend_this_month():
    """Estimated real-dollar Alibaba spend this month. Alibaba has no balance API
    this app can reach, so it is worked out from our own tables: every Alibaba
    task is a row in lipsync_runs (billed, whether or not a customer was charged),
    and credit_spends holds what customers were charged. The prices, the 30%
    promotion (ends 2026-11-01) and how billed seconds are found are all explained
    in alibaba_cost.py. An estimate of money already spent, not a balance."""
    import urllib.request as _ur
    import urllib.parse as _up
    now = time.gmtime()
    month_start = f"{now.tm_year:04d}-{now.tm_mon:02d}-01T00:00:00"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}

    def _rows(table, select, extra=""):
        out, off = [], 0
        while off < 20000:
            url = (f"{SUPABASE_URL}/rest/v1/{table}?select={select}&created_at=gte.{_up.quote(month_start)}"
                   f"{extra}&order=created_at.asc&limit=1000&offset={off}")
            try:
                with _ur.urlopen(_ur.Request(url, headers=hdrs), timeout=10) as r:
                    chunk = json.load(r) or []
            except Exception:
                return None
            out.extend(chunk)
            if len(chunk) < 1000:
                break
            off += 1000
        return out

    spends = _rows("credit_spends", "credits,created_at,job_id", "&action=eq.lipsync") or []
    runs = _rows("lipsync_runs", "job_id,resolution,status,created_at,billed_seconds")
    if runs is None:      # the billed_seconds column has not been added yet
        runs = _rows("lipsync_runs", "job_id,resolution,status,created_at") or []
    base = float(_get_pricing_config().get("lipsyncCreditsPerSec", 40) or 40)
    est = alibaba_cost.estimate(spends, runs, base, today=time.strftime("%Y-%m-%d", now))
    est["credits_this_month"] = sum(float(r.get("credits") or 0) for r in spends)
    return est


# Removed 2026-09-25 (Ali): _get_clone_count_this_month(), a DB-side
# "cumulative clone operations this month" estimate that turned out to
# answer the wrong question -- Ali actually wanted ElevenLabs' own
# voice-cloning-credit quota (voice_add_edit_counter / max_voice_add_edits,
# a real monthly quota ElevenLabs itself enforces and that does NOT free up
# when a cloned voice is deleted), not a count of operations this app
# happened to log. That real number now comes straight from ElevenLabs via
# service_usage_monitor.get_eleven_cached() (clone_ops_used/clone_ops_limit/
# clone_ops_percent) -- see eleven_service.get_subscription_usage()'s
# docstring for the full explanation of the three different quotas.


@app.get("/api/admin/billing_health")
def admin_billing_health(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from billing_health import load_health
    return JSONResponse(load_health(_sb_rpc), headers={"Cache-Control": "no-store"})


@app.get("/api/admin/service_usage")
def admin_service_usage(request: Request):
    """Real (or best-available-estimate) usage numbers for every
    third-party service this app depends on, so the admin dashboard can
    show which one needs a plan/credit top-up before a user hits an
    error. See service_usage_monitor.py's module docstring for exactly
    what's real-time, what's cached, and what's an estimate for each
    service, and why."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    eleven = service_usage_monitor.get_eleven_cached()
    eleven["alert_percent"] = service_usage_monitor.ALERT_PERCENT
    inworld = service_usage_monitor.get_inworld_cached()
    inworld["alert_percent"] = service_usage_monitor.ALERT_PERCENT
    resend = service_usage_monitor.get_resend_cached()
    r2 = r2_backup.get_storage_usage()
    alibaba = _get_lipsync_spend_this_month()
    return {"elevenlabs": eleven, "inworld": inworld, "resend": resend, "r2": r2, "alibaba": alibaba,
            "fal": fal_usage.read(FAL_API_KEY)}


_biz_cache = {}   # days -> (epoch, result); a Stripe + database read takes a few seconds, so keep it for a minute


@app.get("/api/admin/business")
def admin_business(request: Request, days: int = 30, refresh: int = 0):
    """Everything the admin Business tab shows (see business_metrics.py). days = 7 / 30 / 90 / 365, 0 = all time."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    days = days if days in (7, 30, 90, 365, 0) else 30
    hit = _biz_cache.get(days)
    if hit and not refresh and _time.time() - hit[0] < 60:
        return hit[1]
    try:
        res = business_metrics.compute({
            "sb_url": SUPABASE_URL, "sb_key": SUPABASE_SERVICE_KEY,
            "stripe_key": STRIPE_SECRET_KEY, "stripe_mod": stripe,
            "pricing": _get_pricing_config(),
        }, days)
    except Exception as ex:
        print(f"[admin] business metrics failed: {type(ex).__name__}: {ex}")
        return JSONResponse({"error": f"{type(ex).__name__}: {ex}"[:300]}, status_code=500)
    _biz_cache[days] = (_time.time(), res)
    return res


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
    # Combined total (permanent + subscription_credits, same two buckets as
    # get_credits) -- subscription_credits is simply absent from each row
    # until that migration has been run, so `or 0` keeps this correct
    # either way.
    _total_balance = lambda p: (p.get("credits") or 0) + (p.get("subscription_credits") or 0)
    credits_outstanding = sum(_total_balance(p) for p in profiles)
    paying_users = sum(1 for p in profiles if not p.get("is_guest", True) and _total_balance(p) > 150)
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
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from credit_actions import ActionProblem, adjust
    try:
        return adjust(_sb_rpc, await request.json())
    except ValueError as ex:
        return JSONResponse({"error": str(ex)}, status_code=400)
    except ActionProblem as ex:
        return JSONResponse({"error": str(ex), "operation_complete": ex.complete}, status_code=ex.status)

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
                detail = f"{detail} â€” {body}"
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
    error string on failure (same missing-return bug as _log_spend above â€”
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

@app.get("/api/admin/pricing_debug")
def admin_pricing_debug(request: Request):
    """Read-only diagnostic: shows exactly what Supabase's REST API returns
    for the pricing_config singleton row -- the real HTTP status and body,
    or the exact exception -- instead of _get_pricing_config()'s silent
    fall-through to hardcoded defaults on ANY failure (which is the right
    behavior for public-facing pages during a real Supabase outage, but
    makes a permissions or schema problem invisible from the outside).
    Use this whenever Supabase's own Table Editor shows a value saved
    correctly but it still isn't showing up on the public site -- this
    tells you whether the read itself is being blocked or erroring (e.g. a
    Row Level Security policy on this table with no SELECT rule for
    whatever role SUPABASE_SERVICE_KEY actually authenticates as) rather
    than just silently returning nothing. Safe to leave in permanently --
    unlike the old test_save route this replaces in spirit, this makes no
    writes at all."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured"}
    import urllib.request as _ur
    url = f"{SUPABASE_URL}/rest/v1/pricing_config?id=eq.singleton&select=*&order=updated_at.desc&limit=1"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8")
        return {"http_status": r.status, "body": body}
    except Exception as ex:
        return {"error": _http_error_detail(ex)}

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
        return JSONResponse({"ok": False, "error": "Could not read the settings. Reload the page and save again."}, status_code=400)
    ok, err = _save_pricing_config(body)
    if ok:
        _apply_concurrency(body.get("concurrency"))
        _apply_assistant(body.get("assistant"))
    # Surface the real reason to the admin panel instead of a bare "ok:
    # false" -- previously a failed save just showed "unknown error" since
    # nothing but the server logs ever saw the actual exception.
    return {"ok": ok, "error": err}

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

@app.get("/api/admin/lipdebug/{job_id}")
def admin_lipdebug(job_id: str, request: Request):
    """The exact files a long dub's lip-sync clips were sent to the engine with (picture + sound) and what the engine
    returned, as one zip -- kept for 7 days. job_id may be the first characters of the id (8 or more)."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import re as _re
    import zipfile as _zf
    from starlette.background import BackgroundTask
    jid = (job_id or "").strip().lower()
    if not _re.fullmatch(r"[0-9a-f\-]{8,36}", jid):
        return JSONResponse({"error": "That is not a job id."}, status_code=400)
    base = longdub_service.LIPDBG_DIR
    hits = [d for d in (base.iterdir() if base.exists() else []) if d.is_dir() and d.name.startswith(jid)]
    if len(hits) != 1:
        return JSONResponse({"error": "No saved lip-sync files for that job (kept 7 days, and only for jobs run after this feature was added)." if not hits
                             else "More than one job starts with that; type more characters of the id."}, status_code=404)
    d = hits[0]
    tmp = base / f"_{d.name}.zip"
    with _zf.ZipFile(tmp, "w", compression=_zf.ZIP_STORED) as z:
        for f in sorted(d.iterdir()):
            if f.is_file():
                z.write(f, f.name)
    return FileResponse(str(tmp), media_type="application/zip", filename=f"lipsync_{d.name[:8]}.zip",
                        background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))


@app.get("/api/admin/longdub_log")
def admin_longdub_log(request: Request, q: str = "", limit: int = 300, date_from: str = "", date_to: str = ""):
    """Step-by-step record of Dub Long Video jobs (table long_dub_events) --
    for answering a complaint. q = a job id (or its first characters) or a
    user's email; empty = the newest events of all jobs."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import re as _re
    import urllib.parse as _up
    limit = max(1, min(int(limit or 300), 1000))
    q = (q or "").strip()
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    flt = ""
    note = ""
    # optional day range (UTC, both days included): date_from / date_to as YYYY-MM-DD
    import datetime as _dt
    try:
        d_from = _dt.date.fromisoformat(date_from.strip()) if (date_from or "").strip() else None
        d_to = _dt.date.fromisoformat(date_to.strip()) if (date_to or "").strip() else None
    except ValueError:
        return {"events": [], "note": "Dates must look like 2026-10-01."}
    if d_from:
        flt += f"created_at=gte.{d_from.isoformat()}T00:00:00Z&"
    if d_to:
        flt += f"created_at=lt.{(d_to + _dt.timedelta(days=1)).isoformat()}T00:00:00Z&"
    try:
        if "@" in q:
            # the e-mail lives in the sign-in accounts (Supabase auth), not in the profiles table
            want = q.lower()
            found_uid = None
            for page in range(1, 26):
                req = urllib.request.Request(f"{SUPABASE_URL}/auth/v1/admin/users?page={page}&per_page=200", headers=hdrs)
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = json.load(r)
                users = data.get("users") if isinstance(data, dict) else data
                if not users:
                    break
                for u in users:
                    if (u.get("email") or "").strip().lower() == want:
                        found_uid = u.get("id")
                        break
                if found_uid or len(users) < 200:
                    break
            if not found_uid:
                return {"events": [], "note": "No user with that email."}
            flt += f"uid=eq.{found_uid}&"
        elif q:
            if not _re.fullmatch(r"[0-9a-fA-F-]{4,36}", q):
                return {"events": [], "note": "Enter a job id (or its first characters) or an email."}
            flt += f"job_id=like.{q.lower()}*&"
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/long_dub_events?{flt}select=*&order=id.desc&limit={limit}", headers=hdrs)
        with urllib.request.urlopen(req, timeout=10) as r:
            events = json.load(r) or []
    except Exception as ex:
        return {"events": [], "note": f"Could not read the log (has the long_dub_events SQL been run?): {_http_error_detail(ex)}"}
    events.reverse()
    return {"events": events, "note": note}

@app.post("/api/admin/db_backup_now")
def admin_db_backup_now(request: Request):
    """Admin "Back up now" button: copies the Supabase tables to R2 right away (even if today's copy exists)
    and reports, table by table, what worked -- so a problem is visible here instead of only in the server log."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return r2_backup.backup_db_tables(SUPABASE_URL, SUPABASE_SERVICE_KEY, force=True)


@app.get("/api/admin/health")
def admin_health(request: Request):
    """Wired-or-not check for every third-party service this app depends
    on. Three different depths of "check" here, same honesty rule as
    service_usage_monitor.py: a real reachability call where one exists
    for free, without side effects, AND without false negatives for a
    legitimately-scoped key (Supabase, Stripe, R2), and a plain "is the
    key/config present" check everywhere that isn't true (ElevenLabs,
    Gemini, DashScope, Resend). ElevenLabs/Gemini would cost real quota
    just to check the light is green; DashScope has no free reachability
    endpoint at all with the credentials this app has (see
    service_usage_monitor.py's module docstring); Resend's only read
    endpoints require a "Full access" key and 401 for a "Sending access
    only" key even though that key sends mail fine, so a live check there
    would cry wolf for the more common, more secure key setup. Sentry is
    a special case: it checks whether sentry_sdk.init() actually
    succeeded (see SENTRY_INITIALIZED above), not just whether
    SENTRY_DSN is set, since that init call has silently failed before
    while the DSN stayed set."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import urllib.request as _ur
    import base64 as _b64
    def _check(url, hdrs):
        try:
            req = _ur.Request(url, headers=hdrs)
            with _ur.urlopen(req, timeout=5) as r:
                return "ok" if r.status == 200 else f"http_{r.status}"
        except Exception:
            return "fail"
    supabase = _check(f"{SUPABASE_URL}/rest/v1/profiles?limit=1",
                       {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}) \
        if (SUPABASE_URL and SUPABASE_SERVICE_KEY) else "not_configured"
    eleven = "ok" if ELEVENLABS_API_KEY else "not_configured"
    gemini = "ok" if GEMINI_API_KEY else "not_configured"
    dashscope = "ok" if (DASHSCOPE_API_KEY and DASHSCOPE_WORKSPACE_ID) else "not_configured"
    if STRIPE_SECRET_KEY:
        basic = _b64.b64encode(f"{STRIPE_SECRET_KEY}:".encode("utf-8")).decode("ascii")
        stripe_status = _check("https://api.stripe.com/v1/balance", {"Authorization": f"Basic {basic}"})
    else:
        stripe_status = "not_configured"
    # Resend: NOT a real reachability check, on purpose -- GET /domains
    # requires a "Full access" API key, and 401s/403s for a key scoped to
    # "Sending access only" even though that key sends mail just fine.
    # A live check here would show a false red FAIL for exactly the safer,
    # more common key setup, so this stays a shallow "key is set" check
    # like ElevenLabs/Gemini/DashScope above, all of which have the same
    # problem (no side-effect-free way to verify the key actually works).
    resend_status = "ok" if RESEND_API_KEY else "not_configured"
    r2_status = r2_backup.check_reachable()
    sentry_status = "ok" if SENTRY_INITIALIZED else ("fail" if SENTRY_DSN else "not_configured")
    inworld = "ok" if INWORLD_API_KEY else "not_configured"
    # Newest complete nightly copy of the Supabase tables in R2 (see r2_backup.backup_db_tables).
    db_backup = None
    if r2_status == "ok":
        try:
            db_backup = r2_backup.latest_db_backup()
        except Exception:
            db_backup = None
    return {
        "supabase": supabase, "inworld": inworld, "elevenlabs": eleven, "gemini": gemini,
        "dashscope": dashscope, "stripe": stripe_status, "resend": resend_status,
        "r2": r2_status, "sentry": sentry_status, "db_backup": db_backup,
    }


# ---------------------------------------------------------------------------
# Planned-maintenance banner. The admin sets a FROM / TO window (stored in UTC)
# and an optional note; every page that loads /dialogs.js shows a bar with the
# times in each visitor's own time zone. Stored as a tiny JSON file on the
# volume (DATA_DIR), so it needs no database change and survives the very
# redeploy it announces.
# ---------------------------------------------------------------------------
_MAINT_FILE = DATA_DIR / "maintenance.json"
_MAINT_MAX_NOTE = 300
_MAINT_MAX_DAYS = 14
_maint_cache = {"mtime": None, "data": None}


def _maint_read():
    """Saved maintenance settings as a dict ({} when none/unreadable)."""
    try:
        mt = os.path.getmtime(_MAINT_FILE)
    except OSError:
        _maint_cache.update(mtime=None, data={})
        return {}
    if _maint_cache["mtime"] == mt and _maint_cache["data"] is not None:
        return _maint_cache["data"]
    try:
        d = json.loads(_MAINT_FILE.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            d = {}
    except Exception:
        d = {}
    _maint_cache.update(mtime=mt, data=d)
    return d


def _maint_parse(v):
    """ISO-8601 time -> aware UTC datetime, or None."""
    if not isinstance(v, str) or not v.strip() or len(v) > 40:
        return None
    try:
        dt = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _maint_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@app.get("/api/maintenance")
def maintenance_public():
    """What the banner needs: nothing unless a window is switched on and has
    not finished yet."""
    d = _maint_read()
    start, end = _maint_parse(d.get("start")), _maint_parse(d.get("end"))
    now = datetime.now(timezone.utc)
    if d.get("enabled") and start and end and now < end:
        out = {"active": True, "start": _maint_iso(start), "end": _maint_iso(end),
               "message": str(d.get("message") or "")[:_MAINT_MAX_NOTE]}
    else:
        out = {"active": False}
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@app.get("/api/admin/maintenance")
def admin_maintenance_get(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    d = _maint_read()
    return {"enabled": bool(d.get("enabled")), "start": d.get("start") or "",
            "end": d.get("end") or "", "message": d.get("message") or ""}


@app.post("/api/admin/maintenance")
async def admin_maintenance_set(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid request."}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Invalid request."}, status_code=400)
    enabled = bool(body.get("enabled"))
    note = str(body.get("message") or "").strip()[:_MAINT_MAX_NOTE]
    start, end = _maint_parse(body.get("start")), _maint_parse(body.get("end"))
    if enabled:
        if not start or not end:
            return JSONResponse({"error": "Please choose both a From and a To time."}, status_code=400)
        if end <= start:
            return JSONResponse({"error": "The To time must be after the From time."}, status_code=400)
        if end <= datetime.now(timezone.utc):
            return JSONResponse({"error": "The To time is already in the past."}, status_code=400)
        if (end - start) > timedelta(days=_MAINT_MAX_DAYS):
            return JSONResponse({"error": f"A maintenance window can be at most {_MAINT_MAX_DAYS} days long."}, status_code=400)
    data = {"enabled": enabled, "message": note,
            "start": _maint_iso(start) if start else "", "end": _maint_iso(end) if end else ""}
    try:
        _MAINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _MAINT_FILE.with_name(_MAINT_FILE.name + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, _MAINT_FILE)
    except Exception as ex:
        print(f"[maintenance] could not save: {ex}")
        return JSONResponse({"error": "Could not save the maintenance notice."}, status_code=500)
    _maint_cache.update(mtime=None, data=None)
    return {"ok": True, **data}


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
        # where the space actually is, and what the disk guard is doing
        "breakdown_gb": {
            "uploads": round(disk_guard._tree_size(UPLOAD_DIR) / gb, 2),
            "outputs": round(disk_guard._tree_size(OUTPUT_DIR) / gb, 2),
            "long_dub_projects": round(disk_guard._tree_size(DATA_DIR / "longjobs") / gb, 2),
        },
        "guard": {
            "refuses_below_free_gb": round(disk_guard.RESERVE_GB + disk_guard.WORK_SHORT_GB, 2),
            "reserve_gb": disk_guard.RESERVE_GB,
            "refusals_since_start": disk_guard.get_cached().get("refusals", 0),
            "last_cleanup_freed_mb": disk_guard.get_cached().get("last_cleanup_freed_mb", 0),
            "alert_email": bool(_disk_alerts_enabled()),
        },
    }

@app.get("/api/admin/resource_usage")
def admin_resource_usage(request: Request):
    """What the server really uses and costs on Railway, measured inside the container (see resource_meter.py):
    memory now, the idle floor and average of the last 24 hours, the billing month so far with a projection, and what
    each kind of job costs on average. Compare the month figure with the Railway bill."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    out = resource_meter.summary()
    try:
        out["queue"] = whisper_service.queue_state()
    except Exception:
        out["queue"] = None
    return out

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

    # Disk cache sizes are context only: they do not measure resident weights
    # or prove which files contribute to the container page cache. The new
    # runtime flags report cached model objects, not their memory sizes.
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
    from memory_diagnostics import snapshot as _memory_snapshot
    try:
        runtime = _memory_snapshot(globals())
    except Exception:
        runtime = {"error": "The runtime inventory is temporarily unavailable."}

    return {
        "runtime": runtime,
        "cgroup": cgroup, "host_meminfo": host_meminfo, "processes": processes,
        "model_cache_dirs": model_cache_dirs,
        "idle_minutes": idle_minutes,
        "model_cache_idle_threshold_minutes": MODEL_CACHE_IDLE_MINUTES,
    }

# Removed 2026-09-26 (Ali confirmed): /api/admin/purge_old_jobs and
# /api/admin/clear_sessions, formerly wired to admin.html's "Danger Zone"
# card. Both were no-ops. purge_old_jobs looked for subdirectories under
# OUTPUT_DIR, but every job's files are stored flat (job_id_final_dubbed.mp3,
# etc.) -- os.path.isdir() was never true, so it always reported "Purged 0"
# regardless of file age, and real retention is already handled correctly
# by _cleanup_worker()'s two-tier (30d subscriber / 48h pay-once) sweep.
# clear_sessions only cleared _session_users, a minor uid-lookup cache --
# it never touched _valid_tokens, _sessions, or the persistent app_sessions
# table that actually decide whether a cookie is logged in, so it logged
# out precisely nobody; the cache just silently repopulated on the next
# request. If a real "force everyone to log out right now" tool is wanted
# later (e.g. after a key rotation), it would need to clear all three of
# those plus truncate app_sessions.

# Serve the admin HTML page
@app.get("/admin")
def admin_page(request: Request):
    # Was "if not APP_PASSWORD": that blocked the page itself as soon as the
    # shared APP_PASSWORD was removed, even with ADMIN_PASSWORD set.
    if not ADMIN_PASSWORD:
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
# PUBLIC PRICING â€” readable by anyone (no auth needed)
# ============================================================
@app.get("/api/pricing")
def public_pricing():
    """Returns the user-facing pricing config (packs), plus the real
    per-step charges (transcribeCredits/mergeCredits/charsPerCredit/
    cloneCredits/lipsyncCreditsPerSec) so the app's own credit badges can
    show what a button actually costs instead of a guessed or hardcoded
    number. No auth required â€” these are prices, not secrets.

    Also returns voiceEngine (which engine is active for brand-new clones
    right now) plus that engine's own inworldCharsPerCredit/
    inworldCloneCredits rates -- added 2026-09-27 so app.js's badges (e.g.
    the Clone/Upload Custom Voice cost badges) show the CORRECT rate for
    whichever engine is actually going to be charged, instead of always
    showing ElevenLabs' numbers even after admin switches to Inworld."""
    cfg = _get_pricing_config()
    return {
        "packs": cfg.get("packs", []),
        "transcribeCredits": cfg.get("transcribeCredits", 3),
        "mergeCredits": cfg.get("mergeCredits", 1),
        "charsPerCredit": cfg.get("charsPerCredit", 60),
        "cloneCredits": cfg.get("cloneCredits", 5),
        "lipsyncCreditsPerSec": cfg.get("lipsyncCreditsPerSec", 10),
        "lipsyncRates": lipsync_rates(cfg.get("lipsyncCreditsPerSec", 10)),      # credits per second at 480P / 720P / 1080P
        # Neutral names on purpose: this answer is public, so it must not name the voice providers.
        "voiceEngine": "v2" if cfg.get("voiceEngine") == "inworld" else "v1",
        "altCharsPerCredit": cfg.get("inworldCharsPerCredit", 60),
        "altCloneCredits": cfg.get("inworldCloneCredits", 5),
        "geminiCreditsPerCent": _gemini_cpc(cfg.get("geminiCreditsPerCent")),
    }

@app.get("/api/billing/packs")
def billing_packs_dynamic():
    """Returns credit packs from pricing_config (managed by admin panel),
    keyed by each pack's own 'key' field â€” see _keyed_packs(). This is what
    the landing page and the in-app buy modal both fetch to render pack
    cards, and both now loop over whatever keys come back here instead of
    a fixed list, so any number of packs with any keys will show up.

    Also returns the subscription tiers (admin's "Subscription Tiers" table)
    so both the public /pricing page and the in-app Buy modal can render one
    tile per tier and pass its key to /api/billing/subscribe -- see
    DEFAULT_SUBSCRIPTION_PLANS' comment for how this is actually used as of
    2026-09-27."""
    cfg = _get_pricing_config()
    packs_array = cfg.get("packs") or DEFAULT_PACKS
    # no-cache -- this is live, admin-editable data (the exact field that
    # prompted this: the subscription plan name), so a browser silently
    # reusing a cached copy of this response would show stale pricing
    # indefinitely after an admin change, same reasoning as landing()'s
    # HTMLResponse above.
    return JSONResponse({
        "packs": _keyed_packs(packs_array),
        # Legacy flat plan -- kept for backward compatibility with any
        # caller that still reads it, but nothing in this codebase does
        # anymore (both pricing.html and app.js render subscriptionPlans
        # below instead). Only _get_subscription_plan's own fallback path
        # (a profile with no subscription_plan_key) still uses these values,
        # read there directly from pricing_config, not from this response.
        "subscription": {
            "name": cfg.get("subscriptionName") or "Pro Monthly",
            "credits": int(cfg.get("subscriptionCredits") or 4000),
            "amount_usd": float(cfg.get("subscriptionPriceUsd") or 29.0),
        },
        "subscriptionPlans": _plans_for_public_display(cfg.get("subscriptionPlans") or DEFAULT_SUBSCRIPTION_PLANS),
    }, headers={"Cache-Control": "no-cache"})

# ==================== AI HELPER (chat box) ====================
# The chat widget (chat.js) posts here. The model only ever receives: the Help page text, the facts in
# assistant_service.py, the current prices (built below from the admin settings) and, for a signed-in visitor,
# a summary of THAT visitor's own account built here from their own user id. See assistant_service.py.

def _assistant_pricing_text():
    cfg = _get_pricing_config()
    L = ["1 credit is worth about 1 US cent at the standard rate (packs and subscriptions can make a credit cheaper)."]
    try:
        for p in (cfg.get("packs") or DEFAULT_PACKS):
            if isinstance(p, dict):
                L.append(f"Credit pack \"{p.get('name') or p.get('key')}\": {p.get('credits')} credits for ${p.get('price_usd', p.get('price', '?'))} (pay once).")
    except Exception:
        pass
    try:
        for p in _plans_for_public_display(cfg.get("subscriptionPlans") or DEFAULT_SUBSCRIPTION_PLANS):
            if isinstance(p, dict):
                extra = f", up to {p.get('clones_per_month')} new voice clones a month" if p.get("clones_per_month") else ""
                L.append(f"Monthly plan \"{p.get('name')}\": ${p.get('price_usd')} a month, {p.get('credits_per_month')} credits each month "
                         f"(unused monthly credits do not roll over), {p.get('voice_slots')} saved voices{extra}, {p.get('storage_gb')} GB of file storage, "
                         "files kept 30 days.")
        L.append(f"Without a subscription finished files are kept {PAYONCE_OUTPUT_HOURS} hours and the storage is {PAYONCE_STORAGE_GB} GB.")
    except Exception:
        pass
    try:
        eng = "inworld" if cfg.get("voiceEngine") == "inworld" else "elevenlabs"
        cpc = cfg.get("inworldCharsPerCredit" if eng == "inworld" else "charsPerCredit", 60)
        clone = cfg.get("inworldCloneCredits" if eng == "inworld" else "cloneCredits", 5)
        L.append(f"Short dub (video up to 30 seconds): Start (transcribe) = {cfg.get('transcribeCredits', 3)} credits. Generate Arabic Audio = "
                 f"1 credit per {cpc} characters of Arabic text (at least 1 credit) plus the small AI cost of translation, tashkeel and emotions "
                 f"(shown on the buttons). Merge into video = {cfg.get('mergeCredits', 1)} credit(s). Cloning a voice = {clone} credits. "
                 "The exact price of each button is printed on the button itself.")
        r = lipsync_rates(cfg.get("lipsyncCreditsPerSec", 10))
        L.append("Lip-sync video (alpha): credits per second of video: " + ", ".join(f"{k} = {v}" for k, v in r.items()) + ".")
    except Exception:
        pass
    try:
        p = _ld_pricing()
        L.append(f"Long dub: videos up to {p['max_min']:g} minutes; analysis {p['analysis_per_min']:g} credits per minute of video plus a flat {p['flat']} credits "
                 f"plus a speaker check of {p['speaker_check_flat']} credits flat and {p['speaker_check_per_min']:g} per minute (two extra checks of who says each line; returned if the check cannot run) "
                 f"(shown as the estimate before the user accepts); then the dub itself is priced on the lines and voices and shown before accepting; "
                 f"lip-sync for long dubs up to {p['lipsync_max_min']:g} minutes.")
    except Exception:
        pass
    try:
        cpc = float(assistant_service.SETTINGS.get("credits_per_cent") or 0)
        if cpc <= 0:
            L.append("Using this AI helper chat is free.")
        else:
            L.append(f"Using this AI helper chat costs credits: about {assistant_service.avg_usd() * cpc / 0.01:.2f} of a credit per question on average "
                     "(longer questions and longer answers cost a little more). The counter at the top of the chat shows what the current chat has cost; "
                     "it is billed in whole credits as the fractions add up. When the credits are gone the chat stops until the user adds credits.")
    except Exception:
        pass
    return "\n".join(L)


_assistant_acct_cache = {}


def _assistant_account_text(uid, job_id=""):
    """A plain-text summary of THIS user's account. Everything is looked up with this user's id only."""
    now = time.time()
    ck = (uid, job_id or "")
    c = _assistant_acct_cache.get(ck)
    if c and now - c[0] < 20:
        return c[1]
    L = []
    try:
        cr = get_credits(uid)
        L.append(f"Credits balance now: {cr if cr is not None else 'unknown'}.")
    except Exception:
        pass
    sub_active = False
    try:
        prof = _read_subscription_profile(uid) or {}
        st = prof.get("subscription_status") or "none"
        if st == "active":
            sub_active = True
            plan = _get_subscription_plan(prof.get("subscription_plan_key") or "") or {}
            end = str(prof.get("subscription_current_period_end") or "")[:10]
            L.append(f"Subscription: active, plan \"{plan.get('name')}\", renews/ends on {end or 'unknown'}"
                     + (", set to CANCEL at the end of the period" if prof.get("subscription_cancel_at_period_end") else "") + ".")
        else:
            L.append(f"Subscription: none active (status: {st}).")
    except Exception:
        pass
    try:
        import urllib.request as _ur
        url = f"{SUPABASE_URL}/rest/v1/credit_spends?uid=eq.{uid}&select=action,credits,job_id,created_at&order=created_at.desc&limit=12"
        hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        with _ur.urlopen(_ur.Request(url, headers=hdrs), timeout=8) as r:
            rows = json.load(r)
        if rows:
            L.append("Recent credit history (newest first; negative = given back):")
            for x in rows:
                L.append(f"- {str(x.get('created_at') or '')[:16].replace('T', ' ')} UTC: {x.get('action')} {x.get('credits')} credits"
                         + (f" (job {str(x.get('job_id'))[:8]})" if x.get("job_id") else ""))
    except Exception as ex:
        print("[assistant] history lookup failed:", ex)
    try:
        jobs = longdub_service.list_jobs_for_uid(uid)[:6]
        if jobs:
            L.append("Long-dub projects (newest first):")
            for j in jobs:
                v = longdub_service.public_view(j)
                s = f"- \"{v.get('name') or v.get('filename')}\": status {v.get('status')}"
                if v.get("stage"):
                    s += f", stage {v.get('stage')}"
                if v.get("percent") is not None and v.get("status") not in ("done", "error"):
                    s += f", {v.get('percent')}%"
                if v.get("message"):
                    s += f", message: {str(v.get('message'))[:160]}"
                if v.get("error"):
                    s += f", error: {str(v.get('error'))[:200]}"
                if v.get("duration"):
                    s += f", length {round(float(v['duration']))} s"
                L.append(s)
    except Exception as ex:
        print("[assistant] long-dub lookup failed:", ex)
    try:
        ids = _user_job_ids(uid)
        if ids is not None:
            files = []
            for jid in ids:
                for kind, suf in _MY_JOB_FILE_SUFFIXES.items():
                    p = OUTPUT_DIR / f"{jid}{suf}"
                    if p.exists():
                        age_h = (now - p.stat().st_mtime) / 3600
                        keep_h = CLEANUP_FINAL_OUTPUT_DAYS * 24 if sub_active else PAYONCE_OUTPUT_HOURS
                        files.append(f"- {kind} file of job {jid[:8]}, {_fmt_storage(p.stat().st_size)}, deleted automatically in about {max(0, round(keep_h - age_h))} hours")
            sm = _storage_summary(uid, ids)
            if sm:
                L.append(f"Saved files: {len(files)}; storage used {_fmt_storage(sm['used_bytes'])} of {_fmt_storage(sm['quota_bytes'])}"
                         + (" (FULL: delete finished files on the Account page before saving new ones)" if sm["full"] else "") + ".")
            L += files[:10]
    except Exception as ex:
        print("[assistant] files lookup failed:", ex)
    try:
        if job_id and _JOB_ID_RE.fullmatch(str(job_id)):
            uid_owner = _job_owner.get(job_id)
            ok = (str(uid_owner) == str(uid)) if uid_owner else _job_belongs_to_uid(uid, job_id)
            if ok:
                for label, key in (("Short dub on screen", job_id), ("Audio generation for it", f"generate_{job_id}")):
                    d = _public_progress(jobs_progress.get(key))
                    if isinstance(d, dict):
                        L.append(f"{label} (a snapshot taken just now; how long it has stood at this point is NOT known): status {d.get('status')}, {d.get('percent', d.get('progress', ''))}%, message: {str(d.get('message') or d.get('error') or '')[:200]}")
    except Exception as ex:
        print("[assistant] job lookup failed:", ex)
    text = "\n".join(L)[:5000]
    if len(_assistant_acct_cache) > 500:
        _assistant_acct_cache.clear()
    _assistant_acct_cache[ck] = (now, text)
    return text


class AssistantRequest(BaseModel):
    messages: list = []
    lang: str = ""
    page: str = ""
    job_id: str = ""


_assist_owed = {}                    # uid -> credits owed for chat (a fraction below 1), carried between messages
_assist_owed_lock = threading.Lock()
_ASSIST_OWED_FILE = Path(DATA_DIR) / "assistant_owed.json"
try:
    _assist_owed.update({str(k): float(v) for k, v in json.loads(_ASSIST_OWED_FILE.read_text(encoding="utf-8")).items()})
except Exception:
    pass


def _assist_owed_save():
    try:
        _ASSIST_OWED_FILE.write_text(json.dumps({k: round(v, 4) for k, v in _assist_owed.items() if v > 0}), encoding="utf-8")
    except Exception:
        pass


@app.post("/api/assistant")
def assistant_chat(req: AssistantRequest, request: Request):
    """Answer a signed-in question; owed whole credits use durable debit IDs."""
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"ok": False, "answer": "Please sign in to use the helper.", "support": False, "login": True}, status_code=401)
    paused = _billing_pause_response()
    if paused is not None:
        return paused
    if _rate_limited(request, "assistant", 20, 600):
        return JSONResponse({"ok": False, "answer": "You are sending messages too fast. Please wait a minute.", "support": False}, status_code=429)
    contents = assistant_service.clean_messages(req.messages)
    if not contents:
        return JSONResponse({"ok": False, "answer": "", "support": False}, status_code=400)
    from assistant_billing import Ledger, CheckpointProblem, account_lock, owed_value
    try:
        cpc = owed_value(assistant_service.SETTINGS.get("credits_per_cent") or 0)
        with account_lock(uid):
            bal = get_credits(uid)
            if cpc > 0 and not debit_confirmed(bal):
                raise CheckpointProblem("Your balance could not be checked. The helper is temporarily paused.")
            ledger = Ledger(DATA_DIR, uid)
            debit = lambda amount, operation_id: deduct_credits(uid, amount, "assistant", None, operation_id=operation_id)
            if cpc > 0 or ledger.state.get("pending"):
                bal, recovered, pending = ledger.settle(debit, bal)
                if recovered:
                    assistant_service.add_charged(recovered)
                if pending:
                    raise CheckpointProblem("The previous helper charge is awaiting confirmation. Please retry later.")
                # A replayed debit reports its historical balance, so refresh
                # before allowing a new paid helper request.
                bal = get_credits(uid)
                if not debit_confirmed(bal):
                    raise CheckpointProblem("Your balance could not be checked. The helper is temporarily paused.")
            if cpc > 0 and bal < 1:
                return JSONResponse({"ok": False, "nocredits": True, "billed": True, "credits": bal, "cost": 0,
                    "answer": "You have no credits left, so the helper is paused. Add credits and ask again.", "support": False},
                    headers={"Cache-Control": "no-store"})
            ledger.begin_question()
            acct = _assistant_account_text(uid, (req.job_id or "")[:40])
            out = assistant_service.handle(contents, _assistant_pricing_text(), acct, True, f"u:{uid}", uid,
                                           (req.lang or "")[:5], (req.page or "")[:60])
            usd = owed_value(out.pop("usd", 0) or 0)
            cost = usd * cpc / owed_value("0.01") if out.get("ok") else owed_value(0)
            ledger.finish_question(cost)
            charged = 0
            pending = False
            if cpc > 0 and cost > 0:
                bal, charged, pending = ledger.settle(debit, bal)
                if charged:
                    assistant_service.add_charged(charged)
            _assistant_acct_cache.clear()
            out["cost"] = round(float(cost), 4)
            out["billed"] = cpc > 0
            out["billing_pending"] = pending
            if debit_confirmed(bal):
                out["credits"] = bal
            return JSONResponse(out, headers={"Cache-Control": "no-store"})
    except CheckpointProblem as ex:
        return JSONResponse({"ok": False, "answer": str(ex), "support": True, "billing_pending": True},
                            status_code=503, headers={"Cache-Control": "no-store"})
    except Exception:
        return JSONResponse({"ok": False, "answer": "The helper request could not be confirmed. Please contact support before retrying.",
                             "support": True, "billing_pending": True}, status_code=503, headers={"Cache-Control": "no-store"})


@app.get("/api/assistant/credits")
def assistant_credits(request: Request):
    """The chat box's credit counter: the signed-in visitor's own balance (nothing for guests)."""
    uid = _current_uid(request)
    out = {"signed_in": bool(uid), "credits": None, "billed": float(assistant_service.SETTINGS.get("credits_per_cent") or 0) > 0}
    if uid:
        try:
            out["credits"] = get_credits(uid)
        except Exception:
            pass
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@app.get("/api/admin/assistant")
def admin_assistant(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"today": assistant_service.today(), "settings": assistant_service.SETTINGS, "log": assistant_service.recent_log(150)}


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
        return JSONResponse({"ok": False, "error": "We can't take messages through this form right now. Please email us at contact@lisanai.org."}, status_code=503)

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
            try:
                service_usage_monitor.record_resend_usage(_resend_quota_header(r))
            except Exception:
                pass
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



# Removed 2026-09-26: the temporary /api/admin/test_save diagnostic route
# that used to live here. Its own "Test 2: can we WRITE pricing_config"
# check POSTed a hardcoded body containing "packs": [] straight to the real
# pricing_config singleton row on every single GET of this route (no admin
# button, just loading the URL) -- so any time it was hit (e.g. while
# debugging something unrelated in the admin panel), it silently wiped the
# live one-time-pack list back to empty. This is almost certainly why packs
# stopped showing on the landing page: _save_pricing_config() itself was
# never the problem, this leftover diagnostic route was overwriting its
# result behind the scenes. Re-add your packs in the admin Pricing tab and
# hit Save once to restore them -- this route can no longer wipe them again.
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



# TEMPORARY DIAGNOSTIC v2 â€” thorough background audio lookup
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



@app.get("/dub-long-edit")
def longdub_edit_page():
    html = (BASE_DIR / "dub_long_edit.html").read_text(encoding="utf-8")
    if (_get_pricing_config().get("uiStyle") or "classic") == "new":
        html = html.replace("<body>", '<body class="ui-new">', 1)
    return HTMLResponse(html, headers=_NO_CACHE_HEADERS)


@app.get("/longdub_editor.css")
def longdub_editor_css():
    return FileResponse(BASE_DIR / "longdub_editor.css", media_type="text/css", headers=_NO_CACHE_HEADERS)


@app.get("/longdub_editor.js")
def longdub_editor_js():
    return FileResponse(BASE_DIR / "longdub_editor.js", media_type="application/javascript", headers=_NO_CACHE_HEADERS)


@app.post("/api/longdub/{job_id}/review")
async def longdub_review_page(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err: return err
    body = await request.json()
    correction = bool(body.get("correction"))
    with longdub_service._lock_for(job_id):
        rows = longdub_edits.rows(job) if correction else longdub_service.read_segments(job)
        page = int(body.get("page", -1))
        if page < 0 or page * 10 >= len(rows) or job.get("status") not in ("editing", "done"):
            return JSONResponse({"error": "Choose a valid group of lines."}, status_code=400)
        key = "correction_reviewed" if correction else "reviewed_batches"
        if body.get("reviewed", True):
            job.setdefault(key, {})[str(page)] = dub_review.batch_hash(rows[page*10:page*10+10])
        else:
            job.setdefault(key, {}).pop(str(page), None)
        longdub_service._save(job)
    return {"reviewed_batches": job[key], "batches": dub_review.batches(rows, job[key])}


@app.get("/api/longdub/{job_id}/corrections")
def longdub_corrections_view(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    return err if err else customer_payload(longdub_edits.view(job))


@app.put("/api/longdub/{job_id}/corrections")
def longdub_corrections_save(job_id: str, body: LongDubEdits, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err: return err
    try:
        with longdub_service._lock_for(job_id):
            longdub_edits.edit(job, body.edits)
        return customer_payload(longdub_edits.view(job))
    except (ValueError, TypeError, KeyError) as ex:
        return JSONResponse({"error": customer_message(str(ex))}, status_code=400)


class LongDubCorrectionSelection(BaseModel):
    selected: List[str] = []
    token: str = ""


class LongDubCorrectionAction(BaseModel):
    segment_id: str = ""
    position: int = -1


@app.post("/api/longdub/{job_id}/corrections/line/{operation}")
def longdub_correction_line(job_id: str, operation: str, body: LongDubCorrectionAction, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err: return err
    if operation not in ("insert", "split", "delete", "retranslate", "tashkeel"):
        return JSONResponse({"error": "Unknown line action."}, status_code=400)
    if _rate_limited(request, "correction_line", LIGHT_RATE_MAX, LIGHT_RATE_WINDOW_SEC):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    try:
        new_id = longdub_edits.line_operation(job, operation, body.segment_id, body.position)
        result = customer_payload(longdub_edits.view(job))
        result["new_id"] = new_id
        return result
    except (ValueError, TypeError, KeyError) as ex:
        return JSONResponse({"error": customer_message(str(ex))}, status_code=409)


@app.post("/api/longdub/{job_id}/corrections/player")
async def longdub_correction_player(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err: return err
    source = longdub_edits.player_source(job)
    if source is None:
        return JSONResponse({"error": "Choose the original file in the player, or attach it for audio style checks."}, status_code=409)
    path = longdub_service.preview_path(source)
    if not path.exists():
        # Preparing a read-only preview needs no change to the completed parent's state.
        preview_job = dict(source, status="editing")
        path, error = await asyncio.to_thread(longdub_service.ensure_preview, preview_job)
        if error:
            return JSONResponse({"error": customer_message(error[0])}, status_code=error[1])
    return {"url": f"/api/longdub/{source['id']}/media", "kind": "video" if source.get("has_video") else "audio"}


@app.post("/api/longdub/{job_id}/corrections/emotion")
async def longdub_correction_emotion(job_id: str, body: LongDubCorrectionAction, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err: return err
    if _rate_limited(request, "emotion_review", 20, 60):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    row = next((r for r in longdub_edits.rows(job) if r["segment_id"] == body.segment_id), None)
    source = longdub_edits.listening_source(job)
    audio = longdub_service._wd(source) / "vocals_mono.wav" if source else None
    if not row or audio is None or not audio.exists():
        return JSONResponse({"error": "Attach the original file first to check this line's style from audio."}, status_code=409)
    if row["end"] - row["start"] > 30:
        return JSONResponse({"error": "Split this line before checking its style."}, status_code=400)
    return _emotion_audio_review(job_id, audio, row["start"], row["end"], row.get("emotion"))


@app.post("/api/longdub/{job_id}/corrections/quote")
def longdub_corrections_quote(job_id: str, body: LongDubCorrectionSelection, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err: return err
    try:
        return customer_payload(longdub_edits.quote(job, body.selected))
    except (ValueError, TypeError) as ex:
        return JSONResponse({"error": customer_message(str(ex))}, status_code=409)


@app.post("/api/longdub/{job_id}/corrections/dub")
def longdub_corrections_dub(job_id: str, body: LongDubCorrectionSelection, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err: return err
    with longdub_service.account_lock(uid):
        blocked = _storage_block(uid, longdub_edits.output_budget(job))
        if blocked is not None: return blocked
        try:
            child = longdub_edits.start(job, body.selected, body.token)
            return customer_payload(longdub_service.public_view(child))
        except (ValueError, TypeError) as ex:
            return JSONResponse({"error": customer_message(str(ex))}, status_code=409)


@app.post("/api/longdub/{job_id}/corrections/finish")
def longdub_corrections_finish(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err: return err
    try:
        longdub_edits.finish(job)
        return {"ok": True}
    except ValueError as ex:
        return JSONResponse({"error": customer_message(str(ex))}, status_code=409)


@app.post("/api/longdub/{job_id}/corrections/restore")
def longdub_corrections_restore(job_id: str, body: LongDubReattach, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err: return err
    if job.get("status") != "done" or longdub_edits.active(job):
        return JSONResponse({"error": "Wait until the project finishes."}, status_code=409)
    ok, message = disk_guard.check(int(body.size or 0), disk_guard.WORK_LONG_GB, _is_final_output)
    if not ok: return JSONResponse({"error": customer_message(disk_guard.REFUSAL_MESSAGE)}, status_code=503)
    ok, child = longdub_service.redo_project(job, uid, for_edits=True)
    if not ok: return JSONResponse({"error": customer_message(child[0])}, status_code=child[1])
    child["restore_for_edits"] = job_id
    ok, error = longdub_service.restore_init(child, uid, body.filename, body.size)
    if not ok: return JSONResponse({"error": customer_message(error[0])}, status_code=error[1])
    job["edit_restore_id"] = child["id"]
    longdub_service._save(job); longdub_service._save(child)
    return customer_payload(longdub_service.public_view(child))




def _short_speech_spans(job_id, bg):
    rows = (jobs_progress.get(job_id) or {}).get("segments") or []
    return dub_background.speech_spans(bg.parent / "speech_spans.json", rows)


def _short_merge_price(req):
    merge = max(1, int(_get_pricing_config().get("mergeCredits", 1)))
    fee = max(0, int(_get_pricing_config().get("musicFillCredits", 10)))
    video = find_job_video(req.job_id)
    dub = OUTPUT_DIR / f"{req.job_id}_final_dubbed.mp3"
    if video is None or not dub.exists():
        raise ValueError("Upload the original video and generate its dubbed voices first.")
    bg = job_background_audio(req.job_id)
    repairs = 0
    music_kept, music_note = True, ""
    if bg is not None and req.keep_music:
        spans = _short_speech_spans(req.job_id, bg)
        muted = OUTPUT_DIR / f"{req.job_id}_price_muted.wav"
        clean = dub_background.mute(bg, bg.parent / "vocals.wav", muted, spans)
        try:
            repairs = dub_background.count_repairs(clean, spans, OUTPUT_DIR / f"{req.job_id}_price.pcm", original=bg) if clean == muted else 0
            if repairs and not FAL_API_KEY:
                raise ValueError("Music inpainting is temporarily unavailable.")
        except ValueError as ex:
            # The music under the speech cannot be rebuilt reliably (almost no clean background is left, or the
            # silent stretches are too long). The merge is still possible: the background stays silent while the
            # original speakers talk and is unchanged elsewhere. The quote tells the user, who must accept it.
            repairs, music_kept, music_note = 0, False, customer_message(str(ex))
            print(f"[merge-quote] {req.job_id}: music not preserved: {music_note}")
    return {"merge": merge, "music_each": fee, "music_repairs": repairs, "music_max": repairs * fee,
            "music_charged": 0, "max_total": merge + repairs * fee, "music_kept": music_kept, "music_note": music_note}


@app.post("/api/merge_video/quote")
def merge_video_quote(req: MergeRequest, request: Request):
    guard = _job_guard(request, req.job_id, allow_empty=False)
    if guard: return guard
    uid, error = _paid_uid(request)
    if error is not None: return error
    if not begin_operation(req.job_id):
        return JSONResponse({"error": "Wait for the current operation to finish."}, status_code=409)
    try:
        return _short_merge_price(req)
    except Exception as ex:
        return JSONResponse({"error": customer_message(str(ex))}, status_code=409)
    finally:
        finish_operation(req.job_id)


@app.post("/api/merge_video")
@resource_meter.metered("shortdub_merge", lambda req, *a, **k: getattr(req, "job_id", ""))
def merge_video(req: MergeRequest, request: Request):
    guard = _job_guard(request, req.job_id, allow_empty=False)
    if guard: return guard
    uid, error = _paid_uid(request)
    if error is not None: return error
    if not begin_operation(req.job_id):
        return JSONResponse({"error": "Wait for the current operation to finish."}, status_code=409)
    price = None
    try:
        price = _short_merge_price(req)
        if req.accepted_credits != price["max_total"]:
            return JSONResponse({"error": "The price changed. Check the new price and confirm again."}, status_code=409)
        _merge_say(req.job_id, 1, "Starting...")
        _result = _merge_video_run(req, request, price)
        _merge_say(req.job_id, 100, "Done", status="done" if isinstance(_result, dict) and _result.get("status") == "success" else "error")
        return _result
    except Exception as ex:
        _merge_say(req.job_id, 100, "Failed", status="error")
        charged = price.get("music_charged", 0) + price.get("merge_charged", 0) if price else 0
        if charged:
            _ld_refund(uid, charged, req.job_id)
        return JSONResponse({"error": customer_message(str(ex))}, status_code=503)
    finally:
        finish_operation(req.job_id)


@app.get("/dub_long_edit.js")
def longdub_edit_script():
    return FileResponse(BASE_DIR / "dub_long_edit.js", media_type="application/javascript", headers=_NO_CACHE_HEADERS)



class EmotionReviewRequest(_JobIdModel):
    job_id: str = ""
    segment_id: str = ""
    start: float = 0
    end: float = 0
    emotion: str = "neutral"


@app.post("/api/emotion/review")
def short_emotion_review(body: EmotionReviewRequest, request: Request):
    guard = _job_guard(request, body.job_id, allow_empty=False)
    if guard: return guard
    if _rate_limited(request, "emotion_review", 20, 60):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    source = UPLOAD_DIR / f"{body.job_id}_audio.wav"
    if not source.exists(): return JSONResponse({"error": "Attach the original audio first."}, status_code=409)
    duration = ffmpeg_utils.get_media_duration(source)
    if not math.isfinite(body.start + body.end) or body.start < 0 or body.end <= body.start or body.end > duration or body.end-body.start > 30:
        return JSONResponse({"error": "Choose a line of at most 30 seconds inside the original audio."}, status_code=400)
    return _emotion_audio_review(body.job_id, source, body.start, body.end, body.emotion)


def _emotion_audio_review(job_id, source, start, end, emotion):
    # A random local filename prevents two listeners replacing one another's sample.
    import uuid
    sample = OUTPUT_DIR / ("style_check_" + uuid.uuid4().hex + ".mp3")
    try:
        ffmpeg_utils.cut_audio_segment(str(source), start, end-start, sample)
        return emotion_review.inspect(job_id, sample, emotion, GEMINI_API_KEY)
    except Exception as ex:
        return JSONResponse({"error": str(ex)}, status_code=503)
    finally:
        sample.unlink(missing_ok=True)


@app.post("/api/longdub/{job_id}/emotion/review")
async def long_emotion_review(job_id: str, request: Request):
    uid, job, err = _ld_job(request, job_id)
    if err: return err
    if _rate_limited(request, "emotion_review", 20, 60):
        return JSONResponse({"error": customer_message(_RATE_LIMIT_MSG)}, status_code=429)
    body = await request.json()
    row = next((r for r in longdub_service.read_segments(job) if r["segment_id"] == body.get("segment_id")), None)
    source = longdub_service._wd(job) / "vocals_mono.wav"
    if job.get("status") != "editing" or not row or not source.exists():
        return JSONResponse({"error": "Attach the original file and open a valid line first."}, status_code=409)
    if row["end"] - row["start"] > 30:
        return JSONResponse({"error": "Split this line before checking its style."}, status_code=400)
    return _emotion_audio_review(job_id, source, row["start"], row["end"], row.get("emotion"))
