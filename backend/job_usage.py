"""What a project really cost the customer: the sum of its credit history (charges minus refunds).

The history rows are written by the same SQL that takes the credits (credit_spends: uid, action, credits, job_id;
a refund is a negative row), so this total cannot drift from what the Account page shows.
"""
import json
import urllib.parse
import urllib.request

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
    url = (f"{supabase_url}/rest/v1/credit_spends?uid=eq.{urllib.parse.quote(str(uid), safe='')}"
           f"&job_id=eq.{urllib.parse.quote(str(job_id), safe='')}&select=credits&limit={LIMIT}")
    request = urllib.request.Request(url, headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"})
    try:
        with opener(request, timeout=10) as response:
            rows = json.load(response)
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    net, refunded = totals(rows)
    return {"credits_charged": net, "credits_refunded": refunded}
