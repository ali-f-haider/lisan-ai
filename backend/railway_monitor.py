"""Railway memory-usage monitoring.

Polls Railway's GraphQL metrics API on an interval and:
  (a) caches the latest reading in-process, for the admin dashboard to
      display instantly (no live Railway call on every page load), and
  (b) emails CONTACT_TO_EMAIL when usage crosses an alert threshold, so a
      stuck/leaking job is caught within ~20 minutes instead of waiting for
      Railway's own "abnormal usage" email days later.

Setup required (nothing here needs a code change to activate):
  - Generate a personal API token at https://railway.com/account/tokens
    and set it as RAILWAY_API_TOKEN in Railway's environment variables for
    this service. That's the only manual step.
  - RAILWAY_PROJECT_ID, RAILWAY_ENVIRONMENT_ID and RAILWAY_SERVICE_ID are
    auto-injected by Railway into every running service already -- nothing
    to configure there.
  - Alert emails reuse RESEND_API_KEY / CONTACT_TO_EMAIL, already set up
    for the contact form and the file-expiry notice.

Important caveat: the `metrics` query used here is NOT part of Railway's
official public API docs -- it was reverse-engineered by the community
(the schema below matches multiple independent third-party sources as of
Sept 2026, cross-checked against Railway's own documented GraphQL endpoint
URL and Bearer-token auth, but the query shape itself is not something
Railway publishes or promises to keep stable). Every call in this module
is wrapped so a network failure, an auth problem, or a Railway schema
change can only ever surface as {"ok": False, "error": ...} -- it is never
able to crash the app, block a dubbing job, or take down the poll loop.
If it silently reports "unavailable" after deploying this, check the
server logs for the exact "[railway-monitor]" error line.
"""
import json
import os
import threading
import time as _time
import urllib.request

from config import RESEND_API_KEY, CONTACT_TO_EMAIL

RAILWAY_API_TOKEN = os.environ.get("RAILWAY_API_TOKEN", "")
RAILWAY_ENVIRONMENT_ID = os.environ.get("RAILWAY_ENVIRONMENT_ID", "")
RAILWAY_SERVICE_ID = os.environ.get("RAILWAY_SERVICE_ID", "")

RAILWAY_GRAPHQL_URL = "https://backboard.railway.com/graphql/v2"

# How often to poll Railway for memory usage. 20 minutes sits in the
# 15-30 minute range Ali asked for.
MONITOR_INTERVAL_MIN = 20

# Alert when usage crosses this % of Railway's own reported memory limit
# for the service. Percent-of-limit rather than a fixed GB number, so this
# keeps working correctly if the Railway plan/instance size ever changes.
ALERT_PERCENT = 75
# Fallback fixed-GB threshold, only used on the rare reading where Railway
# reports usage but not a limit (so percent-of-limit can't be computed).
ALERT_FALLBACK_GB = 6.0

# Once alerted, don't send another email until usage has dropped back
# under the threshold (so one sustained plateau sends exactly one email,
# not one every 20 minutes) -- but re-send at most this often even if it
# never drops, so a permanently-stuck leak doesn't go silent forever.
ALERT_RENOTIFY_HOURS = 12

_MEMORY_QUERY = """
query metrics($environmentId: String!, $serviceId: String, $startDate: DateTime!, $measurements: [MetricMeasurement!]!, $sampleRateSeconds: Int) {
  metrics(environmentId: $environmentId, serviceId: $serviceId, startDate: $startDate, measurements: $measurements, sampleRateSeconds: $sampleRateSeconds) {
    measurement
    values { ts value }
  }
}
"""

# In-process cache of the last poll result -- what the admin dashboard
# actually reads from. Refreshed every MONITOR_INTERVAL_MIN.
_latest = {"ok": False, "error": "not polled yet", "used_gb": None, "limit_gb": None, "percent": None, "ts": None}
_lock = threading.Lock()
_alert_active = False   # True while usage is above threshold and we've already emailed for this rising edge
_last_alert_sent = 0.0


def _configured():
    return bool(RAILWAY_API_TOKEN and RAILWAY_ENVIRONMENT_ID)


def fetch_memory_usage():
    """One-shot call to Railway's metrics API for this service's current
    memory usage + limit. Never raises -- any failure (not configured,
    network, auth, unexpected shape) comes back as {"ok": False, "error":
    ...} so every caller can just check "ok" without a try/except of its
    own."""
    if not _configured():
        return {"ok": False, "error": "RAILWAY_API_TOKEN not set"}

    start_date = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(_time.time() - 600))
    variables = {
        "environmentId": RAILWAY_ENVIRONMENT_ID,
        "startDate": start_date,
        "measurements": ["MEMORY_USAGE_GB", "MEMORY_LIMIT_GB"],
        "sampleRateSeconds": 60,
    }
    if RAILWAY_SERVICE_ID:
        variables["serviceId"] = RAILWAY_SERVICE_ID

    payload = json.dumps({"query": _MEMORY_QUERY, "variables": variables}).encode("utf-8")
    try:
        req = urllib.request.Request(
            RAILWAY_GRAPHQL_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {RAILWAY_API_TOKEN}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.load(r)
    except Exception as ex:
        return {"ok": False, "error": f"request failed: {ex}"}

    if body.get("errors"):
        return {"ok": False, "error": f"Railway API error: {body['errors']}"}

    series = (body.get("data") or {}).get("metrics") or []
    used_gb = None
    limit_gb = None
    for s in series:
        vals = s.get("values") or []
        if not vals:
            continue
        last_value = vals[-1].get("value")
        if s.get("measurement") == "MEMORY_USAGE_GB":
            used_gb = last_value
        elif s.get("measurement") == "MEMORY_LIMIT_GB":
            limit_gb = last_value

    if used_gb is None:
        return {"ok": False, "error": "no MEMORY_USAGE_GB data in response"}

    percent = round(used_gb / limit_gb * 100, 1) if limit_gb else None
    return {
        "ok": True,
        "used_gb": round(used_gb, 3),
        "limit_gb": round(limit_gb, 3) if limit_gb else None,
        "percent": percent,
    }


def get_cached():
    """What the admin dashboard reads -- the last poll result, not a live
    Railway call. Keeps the admin page fast and avoids burning Railway's
    rate limit every time someone opens it."""
    with _lock:
        return dict(_latest)


def _send_alert_email(used_gb, limit_gb, percent):
    if not RESEND_API_KEY or not CONTACT_TO_EMAIL:
        return False
    if percent is not None:
        headline = f"{used_gb:.2f} GB ({percent:.0f}% of the {limit_gb:.2f} GB limit)"
    else:
        headline = f"{used_gb:.2f} GB"
    subject = f"Lisan AI: Railway memory usage is high -- {headline}"
    body_text = (
        "Hi,\n\n"
        f"Railway is reporting {headline} of memory in use on the Lisan AI "
        "backend service.\n\n"
        "This is an automatic check (polling every "
        f"{MONITOR_INTERVAL_MIN} minutes) meant to catch a stuck or "
        "leaking process before it runs up a large bill, or before "
        "Railway itself has to email you about abnormal usage.\n\n"
        "Check the admin dashboard's Health tab, or Railway's own metrics "
        "graph, and consider restarting the service if this doesn't come "
        "back down on its own.\n\n"
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
                "User-Agent": "LisanAI-Backend/1.0 (+https://lisanai.org)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 201)
    except Exception as ex:
        print(f"[railway-monitor] alert email failed: {ex}")
        return False


def _poll_once():
    global _alert_active, _last_alert_sent
    result = fetch_memory_usage()
    with _lock:
        _latest.clear()
        _latest.update(result)
        _latest["ts"] = _time.time()

    if not result.get("ok"):
        print(f"[railway-monitor] poll failed: {result.get('error')}")
        return

    used_gb = result["used_gb"]
    limit_gb = result.get("limit_gb")
    percent = result.get("percent")
    over_threshold = (percent is not None and percent >= ALERT_PERCENT) or \
                      (percent is None and used_gb >= ALERT_FALLBACK_GB)

    if over_threshold:
        now = _time.time()
        should_send = (not _alert_active) or (now - _last_alert_sent > ALERT_RENOTIFY_HOURS * 3600)
        if should_send and _send_alert_email(used_gb, limit_gb, percent):
            _last_alert_sent = now
        _alert_active = True
    else:
        _alert_active = False


def _worker():
    # Poll once shortly after startup (not just after the first full
    # interval) so the admin dashboard has real data within a minute of a
    # deploy instead of showing "not polled yet" for 20 minutes.
    _time.sleep(30)
    while True:
        try:
            _poll_once()
        except Exception as ex:
            # Must never let a bad response, a schema change, or a network
            # blip kill this thread -- if it dies, monitoring silently
            # stops until the next deploy restarts the process.
            print(f"[railway-monitor] worker error: {ex}")
        _time.sleep(MONITOR_INTERVAL_MIN * 60)


def start():
    """Called once at import time from main.py, same pattern as the other
    background workers (_cleanup_worker etc). Harmless no-op loop if
    RAILWAY_API_TOKEN isn't set yet -- every poll just returns the "not
    configured" error, so admin.html shows a clear message instead of the
    feature needing its own separate on/off switch."""
    threading.Thread(target=_worker, daemon=True).start()
