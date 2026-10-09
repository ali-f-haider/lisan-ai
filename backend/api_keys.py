"""API key primitives only. The adapter owns storage, TLS and authorization."""
import hashlib
import hmac
import math
import re
import secrets

SCOPES = frozenset({"read", "dub", "account"})
KEY_PATTERN = re.compile(r"lsn_live_[A-Za-z0-9_-]{32}", re.ASCII)


def generate_key():
    """The sole random operation. Show the returned secret exactly once."""
    return "lsn_live_" + secrets.token_urlsafe(24)


def dashboard_prefix(key):
    if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
        raise ValueError("Invalid key.")
    return key[len("lsn_live_"):len("lsn_live_") + 8]


def storage_hash(key, server_secret):
    if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
        raise ValueError("Invalid key.")
    if not isinstance(server_secret, bytes) or len(server_secret) < 32:
        raise ValueError("A server secret of at least 32 bytes is required.")
    return hmac.new(server_secret, key.encode("ascii"), hashlib.sha256).hexdigest()


def verify_key(key, stored_hash, server_secret):
    try:
        actual = storage_hash(key, server_secret)
    except ValueError:
        return False
    if not isinstance(stored_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", stored_hash):
        return False
    return hmac.compare_digest(actual, stored_hash)


def key_state(record, now):
    """Unknown/corrupt records fail closed. Unix seconds are supplied by caller."""
    if not isinstance(record, dict) or record.get("state") not in ("active", "revoked"):
        return "revoked"
    if record["state"] == "revoked":
        return "revoked"
    expiry = record.get("expires_at")
    if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now) or now < 0:
        return "expired"
    if expiry is not None:
        if (not isinstance(expiry, (int, float)) or isinstance(expiry, bool)
                or not math.isfinite(expiry) or expiry <= now):
            return "expired"
    return "active"


def has_scope(record, required, now):
    scopes = record.get("scopes") if isinstance(record, dict) else None
    return (isinstance(required, str) and required in SCOPES and key_state(record, now) == "active"
            and isinstance(scopes, list) and all(isinstance(s, str) and s in SCOPES for s in scopes)
            and required in scopes)


def parse_authorization(header):
    """Return a token or None. Only a single HTTP Bearer header is supported."""
    if not isinstance(header, str) or len(header) > 64:
        return None
    match = re.fullmatch(r"(?i:Bearer) (lsn_live_[A-Za-z0-9_-]{32})", header, re.ASCII)
    return match.group(1) if match else None
