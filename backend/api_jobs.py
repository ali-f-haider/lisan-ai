"""Turns the website's long-dub job record into the public API objects (pure, no I/O).

Only an allow-list of fields is ever copied: no file names, text, speaker names, paths, provider ids or internal
messages reach an API customer. Unknown statuses and stages fall back to the nearest published value."""
from datetime import datetime, timezone

import api_errors

STATUSES = ("uploading", "estimated", "accepted", "analyzing", "editing", "confirmed", "dubbing", "payment_pending", "done", "failed")
STAGES = ("upload", "estimate", "queued", "extract", "plan", "separate", "speakers", "transcribe", "build", "translate",
          "review", "clone", "speak", "mix", "finish", "done", "failed")
_STAGE_ALIASES = {"lipsync": "mix"}
_STEP_SLOTS = {"estimate": ("fee",), "analysis": ("analysis",), "dub": ("dub", "music_fill")}
GONE = ("account_cleanup", "provider_cleanup", "cancelled", "expired")        # records that are being or have been deleted
RESULT_KINDS = {"mixed": None, "dialogue": "voices", "background": "effects"}   # API kind -> the website's separate-track name


def iso(ts):
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return "1970-01-01T00:00:00Z"


def is_gone(job):
    return not isinstance(job, dict) or job.get("status") in GONE or not job.get("id")


def status_stage(job):
    status = job.get("status")
    if status not in STATUSES:
        status = "dubbing" if job.get("stage") in ("clone", "speak", "mix", "finish") else "analyzing"
    stage = _STAGE_ALIASES.get(job.get("stage"), job.get("stage"))
    if stage not in STAGES:
        stage = {"uploading": "upload", "estimated": "estimate", "accepted": "queued", "editing": "review", "confirmed": "queued",
                 "done": "done", "failed": "failed"}.get(status, "plan")
    return status, stage


def _slot_totals(job, step):
    """(debited, refunded) for a step, from the website's own payment records."""
    ops = job.get("ops") if isinstance(job.get("ops"), dict) else {}
    debited = refunded = 0
    for slot, entry in ops.items():
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        base = "music_fill" if str(slot).startswith("music_fill:") else slot
        if base in _STEP_SLOTS.get(step, ()):
            debited += max(0, int(entry.get("amount", 0)))
            refunded += sum(max(0, int(v)) for v in (entry.get("refunds") or {}).values())
    return debited, refunded


def operation_view(job):
    api = job.get("api") if isinstance(job.get("api"), dict) else {}
    op = api.get("op")
    if not isinstance(op, dict):
        return None
    status = job.get("status")
    step = op.get("step")
    if status == "failed":
        state = "failed"
    elif step == "estimate":
        state = "succeeded" if status not in ("uploading",) else "accepted"
    elif step == "analysis":
        state = {"accepted": "accepted", "analyzing": "running"}.get(status, "succeeded")
    else:
        state = {"confirmed": "accepted", "dubbing": "running", "done": "succeeded"}.get(status, "accepted")
    debited, refunded = _slot_totals(job, step)
    return {"id": op["id"], "quote_id": op["quote_id"], "step": step, "state": state,
            "maximum_credits": int(op.get("maximum_credits", 0)), "credits_debited": debited, "credits_refunded": refunded}


def job_view(job, request_id, result_available):
    status, stage = status_stage(job)
    err = None
    if status == "failed":
        code = api_errors.from_app_error(job.get("error"), 500)
        err = api_errors.error_body(code, request_id)["error"]
    duration = job.get("duration")
    try:
        duration = round(float(duration), 3) if duration and float(duration) > 0 else None
    except (TypeError, ValueError):
        duration = None
    try:
        percent = max(0, min(100, int(job.get("percent") or 0)))
    except (TypeError, ValueError):
        percent = 0
    api = job.get("api") if isinstance(job.get("api"), dict) else {}
    return {"id": job["id"], "kind": "long", "status": status, "stage": stage, "percent": percent,
            "revision": int(api.get("revision", 0) or 0), "name": str(job.get("name") or "")[:80],
            "duration_seconds": duration, "media_present": bool(job.get("media_present", True)),
            "result_available": bool(result_available), "error": err, "created_at": iso(job.get("created")),
            "updated_at": iso(job.get("updated") or job.get("created")), "current_operation": operation_view(job)}


def upload_view(job):
    received = sorted({int(i) for i in (job.get("received") or []) if isinstance(i, int) and not isinstance(i, bool)})
    return {"job_id": job["id"], "size_bytes": int(job.get("size") or 0), "chunk_bytes": int(job.get("chunk_bytes") or 0),
            "total_chunks": int(job.get("total_chunks") or 0), "received": received, "received_count": len(received)}


def estimate_view(job):
    est = job.get("estimate")
    if not isinstance(est, dict):
        return None
    paid = job.get("paid") if isinstance(job.get("paid"), dict) else {}
    already = int(paid.get("fee", 0) or 0) + int(paid.get("analysis", 0) or 0) + int(paid.get("dub", 0) or 0)
    due = int(est["analysis"]) + int(est.get("flat", 0) or 0) + int(est.get("speaker_check", 0) or 0)
    return {"job_id": job["id"], "estimated_total_credits": int(est["total"]), "already_paid_credits": already,
            "analysis_due_credits": 0 if paid.get("analysis") else due,
            "exact_dub_price_ready": job.get("status") in ("editing", "confirmed", "dubbing", "done"),
            "notice": "This is an estimate of the whole job. Nothing beyond the estimate fee is charged until you accept a quote, "
                      "and the exact dubbing price is fixed after the analysis, when the text can be reviewed."}


def media_type(suffix):
    return {".mp4": "video/mp4", ".m4a": "audio/mp4", ".mp3": "audio/mpeg"}.get(str(suffix).lower(), "application/octet-stream")


def results_view(files, expires_at=None):
    """files: {kind: (size_bytes, suffix)} for the results that exist right now."""
    out = []
    for kind in ("mixed", "dialogue", "background"):
        if kind in files:
            size, suffix = files[kind]
            out.append({"kind": kind, "size_bytes": max(0, int(size)), "media_type": media_type(suffix),
                        "expires_at": iso(expires_at) if expires_at else None})
    return {"results": out}
