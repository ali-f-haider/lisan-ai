"""Lisan AI public API, version 1 -- phase 1: keys, limits, settings, balance.

Everything that touches the outside world (database, balance, plan gate, browser login) is passed in through
`Deps`, so this module is tested with plain fakes and main.py stays the only place that knows about Supabase.

Rules this module keeps:
  * ONE dependency (`principal`) authenticates every /v1 route: a single `Authorization: Bearer lsn_live_...`
    header. A key in the URL, a body, a cookie or a second header is never looked at.
  * Unknown, revoked, expired or malformed keys all get the same answer (invalid_key); nothing says which.
  * Errors are the closed codes of api_errors; no internal text ever leaves.
  * Browser-session routes (/api/api-keys...) manage keys; they never accept an API key.
  * The key secret is returned once, with Cache-Control: no-store, and is never logged or stored.
"""
import os
import re
import secrets
import threading
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import api_errors
import api_keys
import api_limits
import api_settings

TERMS_VERSION = "2026-10-1"
OWNER_SCOPES = ("read", "account")          # phase 1 keys: reading settings and the balance; "dub" arrives with the paid endpoints
_UUID = re.compile(r"[0-9a-fA-F-]{8,64}")
_LAST_USED_EVERY = 60.0
_PUBLIC_KEY_FIELDS = ("id", "name", "prefix", "scopes", "state", "daily_credit_cap", "created_at", "last_used_at", "revoked_at")


class ApiError(Exception):
    def __init__(self, code, headers=None):
        super().__init__(code)
        self.code = code if code in api_errors.ERRORS else "internal_error"
        self.headers = headers or {}


class Deps:
    """What the router needs from the app. main.py builds one; tests build fakes.
    select(table, query) -> list of dict (raises on failure)    insert(table, row) -> dict
    update(table, query, patch) -> None                          settings() -> dict (api_settings shape)
    balance(uid) -> {"credits","subscription_credits","permanent_credits"} | None                                   eligible(uid) -> bool
    owner_uid(request) -> uid | None                             pepper() -> (version:int, secret:bytes) | None
    now() -> unix seconds (float)"""
    def __init__(self, select, insert, update, settings, balance, eligible, owner_uid, pepper, now=time.time):
        self.select, self.insert, self.update = select, insert, update
        self.settings, self.balance, self.eligible = settings, balance, eligible
        self.owner_uid, self.pepper, self.now = owner_uid, pepper, now


def pepper_from_env(environ=None):
    """(version, secret bytes) from API_KEY_PEPPER / API_KEY_PEPPER_VERSION, or None when missing or too short."""
    env = environ if environ is not None else os.environ
    raw = env.get("API_KEY_PEPPER", "")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) < 32:
        return None
    try:
        version = int(env.get("API_KEY_PEPPER_VERSION", "1"))
    except ValueError:
        return None
    return (version, raw.encode("utf-8")) if 1 <= version <= 1000 else None


def _iso_to_unix(value):
    if not isinstance(value, str) or not value:
        return None
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _record_for_state(row):
    """api_keys row -> the plain dict api_keys.key_state / has_scope expect (fails closed on odd data)."""
    scopes = row.get("scopes")
    exp = row.get("expires_at")
    if exp is not None:
        exp = _iso_to_unix(exp) or 0           # an unreadable expiry counts as already expired
    return {"state": row.get("state"), "scopes": scopes if isinstance(scopes, list) else [], "expires_at": exp}


def _error_response(err, request_id):
    status, _retry, _msg = api_errors.ERRORS[err.code]
    headers = dict(err.headers)
    headers["X-Request-Id"] = request_id
    headers["Cache-Control"] = "no-store"
    return JSONResponse(api_errors.error_body(err.code, request_id), status_code=status, headers=headers)


def handle_api_error(request, exc):
    return _error_response(exc, getattr(request.state, "api_request_id", "unavailable"))


class _Limiter:
    """In-process token buckets, one per key. A restart gives a key a fresh bucket and several server copies
    would each keep their own: acceptable for a safety rate limit, NOT used for anything that moves money
    (daily caps and charges are stored in the database by the paid endpoints)."""
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {}

    def hit(self, key_id, now, config):
        with self._lock:
            sub = {key_id: self._state[key_id]} if key_id in self._state else {}
            allowed, retry, new = api_limits.rate_limit(key_id, now, config, sub)
            self._state[key_id] = new[key_id]
            if len(self._state) > 20000:                       # keep memory bounded: forget buckets idle for a minute
                for k in [k for k, v in self._state.items() if now - v["at"] > 60 and k != key_id]:
                    self._state.pop(k, None)
            return allowed, retry


def build(deps):
    """(api_router, owner_router, limiter). Include both routers in the app and register handle_api_error."""
    limiter = _Limiter()
    last_used = {}
    api = APIRouter(prefix="/v1")
    owner = APIRouter(prefix="/api/api-keys")

    def _new_request_id():
        return "req_" + secrets.token_hex(8)

    def authenticate(request, scope):
        request.state.api_request_id = _new_request_id()
        s = api_settings.normalize(_safe(deps.settings, {}))
        if not s["enabled"]:
            raise ApiError("service_unavailable")
        token = api_keys.parse_authorization(request.headers.get("authorization"))
        if token is None:
            raise ApiError("invalid_key")
        pep = deps.pepper()
        if pep is None:
            raise ApiError("service_unavailable")
        version, secret = pep
        digest = api_keys.storage_hash(token, secret)
        try:
            rows = deps.select("api_keys", "key_hash=eq.%s&pepper_version=eq.%d&select=*&limit=1" % (digest, version))
        except Exception:
            raise ApiError("service_unavailable")
        row = rows[0] if isinstance(rows, list) and rows else None
        if not row or not api_keys.verify_key(token, row.get("key_hash"), secret):
            raise ApiError("invalid_key")
        now = deps.now()
        rec = _record_for_state(row)
        if api_keys.key_state(rec, now) != "active":
            raise ApiError("invalid_key")
        if not api_keys.has_scope(rec, scope, now):
            raise ApiError("scope_required")
        key_id, uid = str(row.get("id")), str(row.get("uid"))
        allowed, retry = limiter.hit(key_id, now, api_settings.limiter_config(s))
        if not allowed:
            raise ApiError("rate_limited", {"Retry-After": str(int(retry))})
        if now - last_used.get(key_id, 0) > _LAST_USED_EVERY:
            last_used[key_id] = now
            _safe(lambda: deps.update("api_keys", "id=eq.%s" % key_id, {"last_used_at": _utc(now)}), None)
        if s["eligibility"] != "everyone" and not _safe(lambda: deps.eligible(uid), False):
            raise ApiError("plan_required")
        request.state.api_principal = {"uid": uid, "key_id": key_id, "scopes": list(rec["scopes"]),
                                       "daily_credit_cap": row.get("daily_credit_cap")}
        return request.state.api_principal

    def principal(scope):
        def dep(request: Request):
            p = authenticate(request, scope)
            request.state.api_settings = api_settings.normalize(_safe(deps.settings, {}))
            return p
        return dep

    # ---- /v1: what a key can do in phase 1 ---------------------------------------------------------
    def _ok(request, body, status=200):
        return JSONResponse(body, status_code=status, headers={"X-Request-Id": request.state.api_request_id, "Cache-Control": "no-store"})

    # Phase 1 serves exactly one contract operation: GET /v1/account/balance (docs/api/openapi.yaml, schema Balance).
    # /v1/settings needs the price list and media limits, so it arrives with the paid endpoints.
    @api.get("/account/balance")
    def v1_balance(request: Request, p=Depends(principal("account"))):
        bal = _safe(lambda: deps.balance(p["uid"]), None)
        if not _valid_balance(bal):
            raise ApiError("service_unavailable")           # an unreadable or inconsistent balance is never reported as 0
        return _ok(request, {"credits": bal["credits"], "subscription_credits": bal["subscription_credits"],
                             "permanent_credits": bal["permanent_credits"]})

    # ---- /api/api-keys: the account owner manages keys with the normal website login ---------------
    def _owner(request):
        uid = deps.owner_uid(request)
        if not uid:
            return None, JSONResponse({"error": "Please log in to continue."}, status_code=401)
        return str(uid), None

    def _json_err(msg, status):
        return JSONResponse({"error": msg}, status_code=status, headers={"Cache-Control": "no-store"})

    @owner.get("")
    def keys_list(request: Request):
        uid, err = _owner(request)
        if err:
            return err
        s = api_settings.normalize(_safe(deps.settings, {}))
        try:
            rows = deps.select("api_keys", "uid=eq.%s&select=id,name,prefix,scopes,state,daily_credit_cap,created_at,last_used_at,revoked_at&order=created_at.desc&limit=100" % uid)
            acc = deps.select("api_owner_acceptances", "uid=eq.%s&terms_version=eq.%s&select=accepted_at&limit=1" % (uid, TERMS_VERSION))
        except Exception:
            return _json_err("We couldn't load your API keys just now. Please try again in a moment.", 503)
        allowed = s["eligibility"] == "everyone" or bool(_safe(lambda: deps.eligible(uid), False))
        return JSONResponse({"enabled": s["enabled"], "allowed": allowed, "terms_version": TERMS_VERSION,
                             "terms_accepted": bool(acc), "limits": api_settings.public_view(s),
                             "keys": [{k: r.get(k) for k in _PUBLIC_KEY_FIELDS} for r in rows]}, headers={"Cache-Control": "no-store"})

    @owner.post("")
    async def keys_create(request: Request):
        uid, err = _owner(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict):
            return _json_err("The request could not be read. Reload the page and try again.", 400)
        s = api_settings.normalize(_safe(deps.settings, {}))
        if not s["enabled"]:
            return _json_err("The API is not open yet.", 403)
        if s["eligibility"] != "everyone" and not _safe(lambda: deps.eligible(uid), False):
            return _json_err("The API is available with the same plans as long dubbing. Choose a plan to create a key.", 403)
        pep = deps.pepper()
        if pep is None:
            return _json_err("Keys cannot be created right now. Please try again later.", 503)
        name = body.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            return _json_err("Give the key a name of up to 60 characters.", 400)
        cap, cap_err = api_settings.daily_cap_for(body.get("daily_credit_cap"), s)
        if cap_err:
            return _json_err(cap_err, 400)
        try:
            existing = deps.select("api_keys", "uid=eq.%s&state=eq.active&select=id&limit=200" % uid)
            acc = deps.select("api_owner_acceptances", "uid=eq.%s&terms_version=eq.%s&select=id&limit=1" % (uid, TERMS_VERSION))
        except Exception:
            return _json_err("We couldn't check your account just now. Please try again in a moment.", 503)
        if len(existing) >= s["keys_per_account"]:
            return _json_err("You have reached the limit of %d active keys. Revoke one to create another." % s["keys_per_account"], 409)
        if not acc:
            if body.get("accept_terms") is not True or body.get("confirm_rights") is not True:
                return _json_err("Accept the API terms and confirm you have the right to send the files you dub.", 403)
            try:
                deps.insert("api_owner_acceptances", {"uid": uid, "terms_version": TERMS_VERSION, "rights_confirmed": True})
            except Exception:
                return _json_err("We couldn't save your acceptance. Please try again.", 503)
        version, secret = pep
        key = api_keys.generate_key()
        row = {"uid": uid, "name": name.strip(), "prefix": api_keys.dashboard_prefix(key), "pepper_version": version,
               "key_hash": api_keys.storage_hash(key, secret), "scopes": list(OWNER_SCOPES), "state": "active",
               "daily_credit_cap": cap}
        try:
            saved = deps.insert("api_keys", row)
        except Exception:
            return _json_err("We couldn't create the key. Please try again.", 503)
        return JSONResponse({"key": key, "id": (saved or {}).get("id"), "name": row["name"], "prefix": row["prefix"],
                             "scopes": row["scopes"], "daily_credit_cap": cap,
                             "notice": "Copy this key now. For your security it is shown only once."},
                            status_code=201, headers={"Cache-Control": "no-store"})

    @owner.post("/{key_id}/revoke")
    def keys_revoke(key_id: str, request: Request):
        uid, err = _owner(request)
        if err:
            return err
        if not _UUID.fullmatch(key_id or ""):
            return _json_err("We couldn't find that key.", 404)
        try:
            rows = deps.select("api_keys", "id=eq.%s&uid=eq.%s&select=id,state&limit=1" % (key_id, uid))
        except Exception:
            return _json_err("We couldn't reach your keys just now. Please try again.", 503)
        if not rows:
            return _json_err("We couldn't find that key.", 404)
        if rows[0].get("state") != "revoked":
            try:
                deps.update("api_keys", "id=eq.%s&uid=eq.%s" % (key_id, uid), {"state": "revoked", "revoked_at": _utc(deps.now())})
            except Exception:
                return _json_err("We couldn't revoke the key. Please try again.", 503)
        return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})

    return api, owner, limiter


def _valid_balance(b):
    if not isinstance(b, dict):
        return False
    vals = [b.get(k) for k in ("credits", "subscription_credits", "permanent_credits")]
    if any(type(v) is not int or v < 0 or v > 2147483647 for v in vals):
        return False
    return vals[0] == vals[1] + vals[2]


def _utc(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default
