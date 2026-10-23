"""What a project really cost the customer: the sum of its credit history (charges minus refunds).

The history rows are written by the same SQL that takes the credits (credit_spends: uid, action, credits, job_id;
a refund is a negative row), so this total cannot drift from what the Account page shows.
"""
import json
import urllib.parse
import urllib.request
from supabase_keys import sb_auth

LIMIT = 1000


def totals(rows):
    """(net credits spent, credits refunded) from history rows. Rows that are not whole numbers are ignored."""
    spent = refunded = 0
    for row in rows or []:
        value = row.get("credits") if isinstance(row, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
            continue
        value = int(value)
        if value >= 0:
            spent += value
        else:
            refunded += -value
    return max(0, spent - refunded), refunded


def for_job(uid, job_id, supabase_url, service_key, opener=urllib.request.urlopen):
    """{"credits_charged": net, "credits_refunded": n} or None when the history cannot be read."""
    if not uid or not job_id or not supabase_url or not service_key:
        return None
    base = (f"{supabase_url}/rest/v1/credit_spends?uid=eq.{urllib.parse.quote(str(uid), safe='')}"
            f"&job_id=eq.{urllib.parse.quote(str(job_id), safe='')}")
    headers = {"apikey": service_key, **sb_auth(service_key)}
    rows = None
    # The length of the finished dub is only in the history when the database has the generated_seconds column.
    # When it does not, ask again for the credits alone: the total must never depend on the extra column.
    for select in ("credits,action,generated_seconds", "credits"):
        request = urllib.request.Request(f"{base}&select={select}&limit={LIMIT}", headers=headers)
        try:
            with opener(request, timeout=10) as response:
                rows = json.load(response)
        except Exception:
            rows = None
        if isinstance(rows, list):
            break
    if not isinstance(rows, list):
        return None
    net, refunded = totals(rows)
    out = {"credits_charged": net, "credits_refunded": refunded}
    seconds = finished_seconds(rows)
    if seconds is not None and net > 0:
        out["finished_seconds"] = round(seconds, 1)
        out["credits_per_minute"] = round(net / (seconds / 60.0), 1)
    return out


MIN_SECONDS = 20.0      # a shorter clip is a test: "per minute" would mean nothing


def finished_seconds(rows):
    """Length of the finished dub in seconds, or None when the history does not say (or it is too short to price).

    Every time the voice is generated again the history gets a new row with the same minutes, so the longest
    one is taken, not the sum."""
    best = 0.0
    for row in rows or []:
        if not isinstance(row, dict) or row.get("action") != "generate":
            continue
        value = row.get("generated_seconds")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        value = float(value)
        if value == value and value != float("inf") and value > best:
            best = value
    return best if best >= MIN_SECONDS else None
