"""Closed, neutral public errors. Never echo upstream strings or exceptions."""
import re

# code: (HTTP status, retry with SAME idempotency key, public message)
ERRORS = {
    "invalid_request": (400, False, "Check the request fields and try again."),
    "invalid_key": (401, False, "Use an active API key in the Authorization header."),
    "scope_required": (403, False, "This key does not have permission for this action."),
    "plan_required": (403, False, "Your account plan does not include this action."),
    "consent_required": (403, False, "Accept the current terms and confirm your rights in your account."),
    "not_found": (404, False, "This resource could not be found."),
    "insufficient_credits": (402, False, "Your credit balance is too low for this action."),
    "idempotency_conflict": (409, False, "This request key was already used for different content."),
    "job_not_ready": (409, False, "This job is not ready for this action. Check its status."),
    "revision_conflict": (409, False, "This project changed. Reload it before editing."),
    "quote_changed": (409, False, "The price changed. Request a new quote before accepting."),
    "max_credits_exceeded": (409, False, "The price exceeds your credit limit. Nothing was charged."),
    "upload_incomplete": (409, True, "Upload the missing chunks before finishing."),
    "result_expired": (410, False, "This result is no longer stored. Start a new job."),
    "upload_too_large": (413, False, "This file exceeds the upload limit. Use a smaller file."),
    "unsupported_media": (415, False, "This file could not be read. Use a supported audio or video file."),
    "rate_limited": (429, True, "Too many requests. Wait before trying again."),
    "concurrency_limited": (429, True, "Wait for an active job to finish before starting another."),
    "daily_cap_exceeded": (429, True, "This key reached its daily credit limit. Check your account settings."),
    "storage_full": (409, False, "Your storage is full. Remove an old result before continuing."),
    "service_unavailable": (503, True, "This action is temporarily unavailable. Retry with the same request key."),
    "internal_error": (500, True, "This action could not be completed. Check the job before retrying."),
}


def error_body(code, request_id):
    code = code if isinstance(code, str) and code in ERRORS else "internal_error"
    safe_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else "unavailable"
    return {"error": {"code": code, "message": ERRORS[code][2], "request_id": safe_id}}


def from_app_error(message, status=500):
    """Known exact strings, then HTTP fallback; raw message is NEVER returned.

    Dynamic pricing/storage errors should be classified at their typed source
    by the adapter rather than by substring matching arbitrary upstream text.
    """
    known = {
        "not found": "not_found",
        "Please log in to continue.": "invalid_key",
        "Please log in with your account to continue.": "invalid_key",
        "This upload is already finished.": "job_not_ready",
        "This job is not waiting for approval.": "job_not_ready",
        "This job is not waiting for confirmation.": "job_not_ready",
        "The upload didn't finish. Please try again.": "upload_incomplete",
        "This file has no audio track to dub.": "unsupported_media",
        "This file couldn't be read as audio/video. Please try another file.": "unsupported_media",
        "Your payment could not be confirmed. Please retry.": "service_unavailable",
        "The price changed because the text was edited. Please check the new price and confirm again.": "quote_changed",
        "Please check and confirm the current price before starting. The price may have changed.": "quote_changed",
    }
    if isinstance(message, str) and message in known:
        return known[message]
    return {400: "invalid_request", 401: "invalid_key", 402: "insufficient_credits",
            403: "scope_required", 404: "not_found", 409: "job_not_ready", 410: "result_expired",
            413: "upload_too_large", 415: "unsupported_media", 429: "rate_limited",
            503: "service_unavailable"}.get(status, "internal_error")
