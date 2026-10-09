"""Deterministic decisions; an atomic durable claim is still required."""
import hashlib
import json
import math
import re

TTL_SECONDS = 24 * 60 * 60


def valid_key(key):
    return isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key, re.ASCII) is not None


def fingerprint(method, path, body):
    """JSON only, UTF-8, sorted keys, no NaN; query arguments belong in body.

    The adapter rejects duplicate JSON keys before calling this function.
    Whitespace/key order do not matter; array order and numeric spelling do.
    """
    if (not isinstance(method, str) or method.upper() not in ("POST", "PUT", "PATCH", "DELETE")
            or not isinstance(path, str) or not path.startswith("/v1/")
            or any(c in path for c in ("?", "#", "\\", "\n", "\r")) or len(path) > 512):
        raise ValueError("Invalid request target.")
    def check(value, depth=0):
        if depth > 20:
            raise ValueError("Request nesting is too deep.")
        if type(value) is dict:
            if any(type(k) is not str for k in value):
                raise ValueError("JSON keys must be strings.")
            for item in value.values():
                check(item, depth + 1)
        elif type(value) is list:
            for item in value:
                check(item, depth + 1)
        elif type(value) not in (str, int, float, bool, type(None)):
            raise ValueError("The request must contain JSON values only.")
    check(body)
    try:
        canonical = json.dumps([method.upper(), path, body], ensure_ascii=False,
                               sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("The request must contain valid JSON.") from exc
    return hashlib.sha256(canonical).hexdigest()


def decide(key, request_fingerprint, record, now):
    """new / conflict / replay. Replay in-flight work; never start it twice.

    Namespace records by account AND API key id AND Idempotency-Key. Completed
    records expire at 24h; business-operation receipts must outlive this TTL.
    """
    if (not valid_key(key) or not isinstance(request_fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", request_fingerprint)
            or type(now) not in (int, float) or not math.isfinite(now) or now < 0):
        raise ValueError("Invalid idempotency input.")
    if record is None:
        return "new"
    if (not isinstance(record, dict) or record.get("key") != key
            or record.get("state") not in ("in_progress", "complete")
            or type(record.get("created_at")) not in (int, float)
            or not math.isfinite(record["created_at"]) or record["created_at"] < 0
            or not isinstance(record.get("fingerprint"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["fingerprint"])):
        return "conflict"
    if now < record["created_at"]:
        return "conflict"
    if record["state"] == "complete" and now - record["created_at"] >= TTL_SECONDS:
        return "new"
    return "replay" if record["fingerprint"] == request_fingerprint else "conflict"
