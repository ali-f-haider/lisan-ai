"""Real usage/quota monitoring for third-party services this app depends
on, surfaced in the admin dashboard's Health panel so Ali can see which
service needs a plan/credit top-up before a user hits an error.

Three different levels of "real" data turned out to be available when this
was researched (Sept 2026):

  - ElevenLabs exposes a real account-quota endpoint (character_count /
    character_limit via GET /v1/user/subscription) -- polled here on the
    same interval/caching/alert-email pattern as railway_monitor.py.

  - Resend has no "remaining quota" endpoint at all -- only a
    x-resend-monthly-quota response header that appears on an actual send.
    There's nothing to *poll* for that, so this module just remembers the
    last value seen whenever main.py sends an email via Resend (contact
    form, expiry notices), via record_resend_usage(). It's a "used so far,
    as of the last email we happened to send" number, not a live reading.

  - Alibaba/DashScope (the lip-sync provider, the biggest real cost) has no
    balance/usage API reachable with the DASHSCOPE_API_KEY this app has --
    that needs an entirely separate Alibaba Cloud AccessKey/BSS-API
    credential pair Ali hasn't set up. So there's nothing to poll here
    either; main.py's /api/admin/service_usage instead computes an
    *estimated* spend directly from this app's own credit_spends log
    (credits charged ÷ 40 credits/sec × $0.1153/sec -- see main.py's
    pricing comment for where that $/sec figure comes from). That's real
    money already spent, just not a live "balance remaining" figure the
    way ElevenLabs' is.

  - R2 storage usage is summed live from the bucket itself on every call
    (see r2_backup.get_storage_usage) -- not polled/cached here since a
    list_objects_v2 call is cheap, and there's no alert threshold to check
    it against anyway (Cloudflare's S3-compatible API doesn't expose the
    account's plan limit).

Every function here follows the same rule as railway_monitor.py: a
network failure, a missing key, or an unexpected response can only ever
surface as {"ok": False, "error": ...} (or a plain None) -- never able to
crash the app or block a real dubbing job.
"""
import json
import threading
import time as _time
import urllib.request

from config import ELEVENLABS_API_KEY, RESEND_API_KEY, CONTACT_TO_EMAIL
import eleven_service

# Same cadence as railway_monitor.py -- sits in the 15-30 minute range
# that's proven to be a reasonable check-in interval without hammering
# ElevenLabs' API.
MONITOR_INTERVAL_MIN = 20

# Alert when ElevenLabs character usage crosses this % of the account's
# character_limit for the current period. Matches railway_monitor.py's
# ALERT_PERCENT so both monitors mean the same thing by "high".
ALERT_PERCENT = 75

# Once alerted, don't send another email until usage drops back under the
# threshold -- but re-send at most this often even if it never drops, so a
# permanently-maxed-out quota doesn't go silent forever.
ALERT_RENOTIFY_HOURS = 12

_eleven_latest = {
    "ok": False, "error": "not polled yet", "character_count": None,
    "character_limit": None, "percent": None, "tier": None,
    "next_reset_unix": None, "voice_slots_used": None, "voice_limit": None,
    "voice_percent": None, "clone_ops_used": None, "clone_ops_limit": None,
    "clone_ops_percent": None, "ts": None,
}
_eleven_lock = threading.Lock()
_eleven_alert_active = False
_eleven_last_alert_sent = 0.0

# Resend's used-count is never "polled" -- only ever updated by main.py
# right after a real send, via record_resend_usage().
_resend_latest = {"used": None, "ts": None}
_resend_lock = threading.Lock()


def _configured():
    return bool(ELEVENLABS_API_KEY)


def fetch_eleven_usage():
    """One-shot live call to ElevenLabs' subscription endpoint. Never
    raises -- any failure comes back as {"ok": False, "error": ...}.

    Tracks THREE independent quotas -- see eleven_service.get_subscription_
    usage()'s docstring for the full explanation of why they're different
    numbers: character usage (the TTS budget), cloned-voice slots (a live
    snapshot, frees up on delete), and voice cloning credits (a monthly
    quota that does NOT free up on delete -- this is the one that actually
    answers "how many clones do I have left this month")."""
    if not _configured():
        return {"ok": False, "error": "ELEVENLABS_API_KEY not set"}
    data = eleven_service.get_subscription_usage(ELEVENLABS_API_KEY)
    if data.get("error"):
        return {"ok": False, "error": data["error"]}
    count = data.get("character_count")
    limit = data.get("character_limit")
    if count is None:
        return {"ok": False, "error": "unexpected response shape from ElevenLabs"}
    percent = round(count / limit * 100, 1) if limit else None
    voices_used = data.get("voice_slots_used")
    voice_limit = data.get("voice_limit")
    voice_percent = round(voices_used / voice_limit * 100, 1) if (voices_used is not None and voice_limit) else None
    clone_ops_used = data.get("voice_add_edit_counter")
    clone_ops_limit = data.get("max_voice_add_edits")
    clone_ops_percent = round(clone_ops_used / clone_ops_limit * 100, 1) if (clone_ops_used is not None and clone_ops_limit) else None
    return {
        "ok": True,
        "character_count": count,
        "character_limit": limit,
        "percent": percent,
        "tier": data.get("tier"),
        "next_reset_unix": data.get("next_reset_unix"),
        "voice_slots_used": voices_used,
        "voice_limit": voice_limit,
        "voice_percent": voice_percent,
        "clone_ops_used": clone_ops_used,
        "clone_ops_limit": clone_ops_limit,
        "clone_ops_percent": clone_ops_percent,
    }


def get_eleven_cached():
    """What the admin dashboard reads -- the last poll result, not a live
    ElevenLabs call every time someone opens the page."""
    with _eleven_lock:
        return dict(_eleven_latest)


def record_resend_usage(used_count):
    """Called by main.py right after a successful Resend send, with the
    integer parsed from the x-resend-monthly-quota response header.
    Best-effort by design: main.py wraps this call so a problem here can
    never break the actual email send."""
    if used_count is None:
        return
    with _resend_lock:
        _resend_latest["used"] = used_count
        _resend_latest["ts"] = _time.time()


def get_resend_cached():
    with _resend_lock:
        return dict(_resend_latest)


def _send_eleven_alert_email(result):
    if not RESEND_API_KEY or not CONTACT_TO_EMAIL:
        return False
    tier = result.get("tier") or "unknown"
    lines = []
    percent = result.get("percent")
    if percent is not None and percent >= ALERT_PERCENT:
        lines.append(
            f"- Character quota: {result['character_count']:,} of "
            f"{result['character_limit']:,} used ({percent:.0f}%)."
        )
    voice_percent = result.get("voice_percent")
    if voice_percent is not None and voice_percent >= ALERT_PERCENT:
        lines.append(
            f"- Cloned voice slots: {result['voice_slots_used']} of "
            f"{result['voice_limit']} used ({voice_percent:.0f}%). Running "
            "out blocks cloning any NEW speaker's voice, even with plenty "
            "of character quota left -- existing dubbing jobs aren't "
            "affected until a job needs a voice that hasn't been cloned yet."
        )
    clone_ops_percent = result.get("clone_ops_percent")
    if clone_ops_percent is not None and clone_ops_percent >= ALERT_PERCENT:
        lines.append(
            f"- Voice cloning credits (monthly quota): {result['clone_ops_used']} of "
            f"{result['clone_ops_limit']} used ({clone_ops_percent:.0f}%). "
            "This is the one that matters most -- unlike voice slots above, "
            "it does NOT free up when a cloned voice is deleted, only on "
            "your next billing-cycle reset. Once it hits the limit, no new "
            "speaker can be cloned at all until the reset, regardless of "
            "how much character quota or how many free voice slots remain."
        )
    if not lines:
        return False
    subject = f"Lisan AI: ElevenLabs usage is high ({tier} tier)"
    body_text = (
        "Hi,\n\n"
        f"Your ElevenLabs account ({tier} tier) is running high on:\n\n"
        + "\n".join(lines) +
        "\n\nConsider upgrading the ElevenLabs plan (or, for character "
        "quota, waiting for the next reset) before this starts failing "
        "for users.\n\n"
        "This is an automatic check (polling every "
        f"{MONITOR_INTERVAL_MIN} minutes) -- see the admin dashboard's "
        "Health tab for the live numbers.\n\n"
        "-- Lisan AI monitoring"
    )
    payload = json.dumps({
        "from": "Lisan AI <noreply@lisanai.org>",
        "to": [CONTACT_TO_EMAIL],
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
                # Same Cloudflare-bot-block fix as railway_monitor.py / main.py.
                "User-Agent": "LisanAI-Backend/1.0 (+https://lisanai.org)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            try:
                val = r.headers.get("x-resend-monthly-quota")
                record_resend_usage(int(val) if val is not None else None)
            except Exception:
                pass
            return r.status in (200, 201)
    except Exception as ex:
        print(f"[service-usage-monitor] alert email failed: {ex}")
        return False


def _poll_once():
    global _eleven_alert_active, _eleven_last_alert_sent
    result = fetch_eleven_usage()
    with _eleven_lock:
        _eleven_latest.clear()
        _eleven_latest.update(result)
        _eleven_latest["ts"] = _time.time()

    if not result.get("ok"):
        print(f"[service-usage-monitor] ElevenLabs poll failed: {result.get('error')}")
        return

    percent = result.get("percent")
    voice_percent = result.get("voice_percent")
    clone_ops_percent = result.get("clone_ops_percent")
    over_threshold = (percent is not None and percent >= ALERT_PERCENT) or \
                      (voice_percent is not None and voice_percent >= ALERT_PERCENT) or \
                      (clone_ops_percent is not None and clone_ops_percent >= ALERT_PERCENT)

    if over_threshold:
        now = _time.time()
        should_send = (not _eleven_alert_active) or (now - _eleven_last_alert_sent > ALERT_RENOTIFY_HOURS * 3600)
        if should_send and _send_eleven_alert_email(result):
            _eleven_last_alert_sent = now
        _eleven_alert_active = True
    else:
        _eleven_alert_active = False


def _worker():
    # Poll once shortly after startup (not just after the first full
    # interval) so the admin dashboard has real data within a minute of a
    # deploy instead of showing "not polled yet" for 20 minutes.
    _time.sleep(30)
    while True:
        try:
            _poll_once()
        except Exception as ex:
            # Must never let a bad response or network blip kill this
            # thread -- if it dies, monitoring silently stops until the
            # next deploy restarts the process.
            print(f"[service-usage-monitor] worker error: {ex}")
        _time.sleep(MONITOR_INTERVAL_MIN * 60)


def start():
    """Called once at import time from main.py, same pattern as
    railway_monitor.start(). Harmless no-op loop if ELEVENLABS_API_KEY
    isn't set (shouldn't happen in practice, since the app needs it to
    function at all) -- every poll just returns the "not configured"
    error, so admin.html shows a clear message instead of this feature
    needing its own separate on/off switch."""
    threading.Thread(target=_worker, daemon=True).start()
