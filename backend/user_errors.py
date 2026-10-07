"""Customer-safe error messages.

Anything a customer can read (an error returned to the browser, a progress line, a warning, an email) must
be written for the customer: no vendor names, no HTTP codes, no file paths, no settings names, no raw
error text. The real detail goes to the server log (print), tagged with a short reference so it can be
found again from a customer's report.

friendly_error(e, where) turns any exception into such a sentence.
UserError is for messages that were already written for the customer: it is shown as it is.
"""
import re
import uuid


class UserError(Exception):
    """A message written for the customer. friendly_error() shows it unchanged."""


GENERIC = "Something went wrong on our side. Please try again in a moment."

# (words to look for in the lower-cased raw text) -> sentence for the customer. First match wins.
_RULES = [
    (("voice_add_edit_limit_reached", "monthly limit of voice", "voice_limit_reached", "voice limit"),
     "Voice cloning is temporarily unavailable. Please try again later, or choose a studio voice for this speaker in Step 4."),
    (("voice_not_found", "voice was not found", "voice not found"),
     "The voice for this speaker is no longer available. Please clone it again in Step 3.5 or choose a studio voice in Step 4."),
    (("quota_exceeded", "quota exceeded", "resource_exhausted", "resource exhausted", "too many requests", "rate limit",
      "http error 429", "status_code: 429", "http 429", " 429:", "overloaded", "http error 503", "http 503", "service unavailable"),
     "Our service is very busy right now. Please try again in a minute."),
    (("timed out", "timeout", "time out"),
     "This is taking longer than expected. Please try again."),
    (("name or service not known", "name resolution", "urlopen error", "connection refused", "connection reset",
      "connection aborted", "max retries exceeded", "network is unreachable", "sslerror", "ssl:", "ssl handshake", "gaierror", "eof occurred",
      "remote end closed", "connection error"),
     "We couldn't reach the service just now. Please try again in a moment."),
    (("invalid data found", "moov atom not found", "no such file", "could not find codec", "conversion failed",
      "error while decoding", "ffmpeg version", "ffprobe", "output file", "stream map", "filtergraph"),
     "We couldn't process this file. Please try another video or audio file (MP4, MOV, MP3 or WAV)."),
    (("out of memory", "cuda out of memory", "memoryerror", "cannot allocate memory"),
     "This file is too demanding for us to process right now. Please try a shorter clip."),
]


def friendly_error(e, where=""):
    """A sentence that is safe to show to the customer. Logs the real error with a short reference."""
    if isinstance(e, UserError):
        return str(e)
    raw = str(e) if not isinstance(e, str) else e
    ref = uuid.uuid4().hex[:8]
    try:
        print(f"[error {ref}] {where} {type(e).__name__}: {raw[:2000]}")
    except Exception:
        pass
    low = raw.lower()
    for keys, msg in _RULES:
        if any(k in low for k in keys):
            return msg
    return f"{GENERIC} If it keeps happening, contact support and mention reference {ref}."


def clean_prefix(text):
    """Strips the internal 'ERROR:' marker that some helpers put in front of a message."""
    t = str(text or "").strip()
    if t.upper().startswith("ERROR:"):
        t = t[6:].strip()
    return t


# Diagnostics only: never change the customer's transcript or project name.
_PROVIDER_DIAGNOSTIC = re.compile(
    r"\b(?:gemini|elevenlabs|inworld|openai|whisper|demucs|pyannote|fal(?:\.ai|[- ]ai)?|"
    r"stable[- ]audio|claude|anthropic|gpt(?:[- ][0-9.]+)?|hugging[- ]face|replicate|"
    r"deepseek|minimax|fish[- ]audio|veed|qwen|dashscope|alibaba|wanx|wan)\b|"
    r"api[ _-]?key|\btokens?\b|\bquota\b|HTTP\s*(?:Error\s*)?\d{3}|Traceback", re.I)
_DIAGNOSTIC_FIELDS = frozenset(("error", "errors", "message", "status_text", "detail", "reason", "why", "note",
                               "music_note", "warning", "warnings", "cloned_voices"))


def customer_message(text):
    """Keep useful validation messages; hide raw service diagnostics and log them."""
    if isinstance(text, str) and _PROVIDER_DIAGNOSTIC.search(text):
        prefix = "ERROR: " if text.lstrip().upper().startswith("ERROR:") else ""
        return prefix + friendly_error(text, "customer diagnostic")
    return text


def customer_payload(value, diagnostic=False):
    """Return a copy with diagnostic fields safe for a customer response."""
    if isinstance(value, dict):
        return {key: customer_payload(item, diagnostic or key in _DIAGNOSTIC_FIELDS)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [customer_payload(item, diagnostic) for item in value]
    return customer_message(value) if diagnostic else value
