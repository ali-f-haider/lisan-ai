"""What a finished minute really costs customers, worked out from the credit history (credit_spends).

Two numbers come out of this module and nothing else:
  * per project (short dub): credits used / finished minutes, shown on the dubbing page (see job_usage.py);
  * across all projects: the TYPICAL credits for one finished minute over the last WINDOW_DAYS days, shown on the
    Pricing page. "Finished minute" counts the length of the finished dub once, but every credit spent on that project
    counts: transcription, voices, merging, lip-sync, long-dub fees, line corrections, minus refunds. So re-doing a
    line makes the number go UP, which is the point: it is the price of a minute the customer accepted.

The public figure is the MEDIAN project (half the projects cost less per minute, half more) with the middle half as the
range, so one huge or tiny project cannot move it. It is hidden until there are enough projects and enough different
people that it cannot point at one customer, and it contains no names, ids or dates, only totals.

Long dubs: the length of the video is not in the credit history, it is in the project file (job.json), and a line
correction is its own small job that points back at its project (`edit_of`). Projects whose file has been deleted
are left out (their length is unknown), so the figure describes projects that still exist.

Nothing here writes anything, charges anything or calls an AI service.
"""
import json
import math
import threading
import time
import urllib.parse
import urllib.request

from supabase_keys import sb_auth

WINDOW_DAYS = 90
MIN_PROJECTS = 20            # fewer projects than this: no public figure (too little data to mean anything)
MIN_PEOPLE = 5               # fewer different customers than this: no public figure (could point at one person)
MIN_SECONDS = 20.0           # a clip shorter than this is a test, its "per minute" price is not meaningful
CACHE_SECONDS = 6 * 3600
RETRY_SECONDS = 15 * 60      # after a failed refresh, wait this long before trying again
PAGE_SIZE = 1000
MAX_PAGES = 10               # 10,000 history rows at most per refresh
MAX_LONG_JOBS = 1500         # job files read per refresh


def _whole(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return int(value) if value == int(value) else None


def _seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value > 0 else None


def build_projects(rows, long_meta=None):
    """One entry per project: {"uid", "credits", "seconds"} (only projects whose length is known and long enough).

    rows       credit history rows (uid, action, credits, job_id, generated_seconds)
    long_meta  {job_id: {"duration": seconds, "edit_of": parent job id or None}} for long dubs and their corrections
    """
    long_meta = long_meta or {}
    projects = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        job_id = row.get("job_id")
        credits = _whole(row.get("credits"))
        if not job_id or credits is None:
            continue
        meta = long_meta.get(job_id) or {}
        key = meta.get("edit_of") or job_id          # a correction belongs to the project it corrects
        p = projects.setdefault(key, {"uid": row.get("uid") or "", "net": 0, "short_seconds": 0.0})
        p["net"] += credits
        if not p["uid"] and row.get("uid"):
            p["uid"] = row.get("uid")
        if row.get("action") == "generate":
            secs = _seconds(row.get("generated_seconds"))
            if secs:
                p["short_seconds"] = max(p["short_seconds"], secs)     # a redo makes the same minutes again: count them once
    out = []
    for key, p in projects.items():
        meta = long_meta.get(key) or {}
        seconds = _seconds(meta.get("duration")) or (p["short_seconds"] or None)
        if seconds is None or seconds < MIN_SECONDS or p["net"] <= 0:
            continue
        out.append({"uid": p["uid"], "credits": p["net"], "seconds": seconds})
    return out


def _percentile(sorted_values, q):
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def summarize(projects, min_projects=MIN_PROJECTS, min_people=MIN_PEOPLE):
    """{"projects", "people", "median", "low", "high"} (credits per finished minute) or None when too little data."""
    per_minute = sorted(p["credits"] / (p["seconds"] / 60.0) for p in projects or [])
    people = {p["uid"] for p in projects or [] if p.get("uid")}
    if len(per_minute) < min_projects or len(people) < min_people:
        return None
    return {
        "projects": len(per_minute),
        "people": len(people),
        "median": round(_percentile(per_minute, 0.5), 1),
        "low": round(_percentile(per_minute, 0.25), 1),
        "high": round(_percentile(per_minute, 0.75), 1),
    }


def usd_per_credit_range(packs, plans):
    """(cheapest, dearest) dollars per credit across the credit packs and monthly plans, or None."""
    rates = []
    for pack in packs or []:
        try:
            credits = float(pack.get("credits")) * (1 + float(pack.get("bonus_pct") or 0) / 100.0)
            price = float(pack.get("price_usd"))
        except (TypeError, ValueError, AttributeError):
            continue
        if credits > 0 and price > 0:
            rates.append(price / credits)
    for plan in plans or []:
        try:
            credits = float(plan.get("credits_per_month"))
            price = float(plan.get("price_usd"))
        except (TypeError, ValueError, AttributeError):
            continue
        if credits > 0 and price > 0:
            rates.append(price / credits)
    return (min(rates), max(rates)) if rates else None


def public_figure(summary, packs, plans):
    """What the Pricing page may show. Credits always; dollars only when the price table gives a rate."""
    if not summary:
        return {"ok": False}
    out = {"ok": True, "days": WINDOW_DAYS, **summary}
    rate = usd_per_credit_range(packs, plans)
    if rate:
        out["usd_low"] = round(summary["median"] * rate[0], 2)
        out["usd_high"] = round(summary["median"] * rate[1], 2)
    return out


# ---------------------------------------------------------------- reading the history

def fetch_rows(supabase_url, service_key, since_iso, opener=urllib.request.urlopen):
    """Credit history rows since `since_iso`, newest first, or None when the history cannot be read."""
    if not supabase_url or not service_key:
        return None
    rows = []
    for page in range(MAX_PAGES):
        url = (f"{supabase_url}/rest/v1/credit_spends?created_at=gte.{urllib.parse.quote(since_iso, safe='')}"
               f"&job_id=not.is.null&select=uid,action,credits,job_id,generated_seconds"
               f"&order=created_at.desc&limit={PAGE_SIZE}&offset={page * PAGE_SIZE}")
        request = urllib.request.Request(url, headers={"apikey": service_key, **sb_auth(service_key)})
        try:
            with opener(request, timeout=15) as response:
                batch = json.load(response)
        except Exception:
            return None
        if not isinstance(batch, list):
            return None
        rows.extend(r for r in batch if isinstance(r, dict))
        if len(batch) < PAGE_SIZE:
            break
    return rows


def long_job_ids(rows):
    """Job ids that belong to the long-dub tool (their length lives in the project file), newest first, capped."""
    seen, out = set(), []
    for row in rows or []:
        action = str(row.get("action") or "")
        job_id = row.get("job_id")
        if action.startswith("long_dub") and job_id and job_id not in seen:
            seen.add(job_id)
            out.append(job_id)
            if len(out) >= MAX_LONG_JOBS:
                break
    return out


def compute(supabase_url, service_key, read_meta, now=None, opener=urllib.request.urlopen):
    """The summary for the last WINDOW_DAYS days, or None when there is not enough data. Raises when the history
    cannot be read.

    read_meta(job_id) -> {"duration": seconds, "edit_of": parent id or None} or None: reads the project file and
    forgets it again, so nothing is kept in memory."""
    now = time.time() if now is None else now
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - WINDOW_DAYS * 86400))
    rows = fetch_rows(supabase_url, service_key, since, opener)
    if rows is None:
        raise RuntimeError("the credit history could not be read")      # lets cached() try again soon instead of hiding the figure for hours
    meta = {}
    for job_id in long_job_ids(rows):
        try:
            m = read_meta(job_id)
        except Exception:
            m = None
        if isinstance(m, dict):
            meta[job_id] = {"duration": m.get("duration"), "edit_of": m.get("edit_of")}
    return summarize(build_projects(rows, meta))


# ---------------------------------------------------------------- the cached figure

_lock = threading.Lock()
_state = {"value": None, "at": 0.0, "failed_at": 0.0, "running": False}


def cached(refresh, now=None, start_thread=True):
    """The last computed summary (or None). Starts a refresh in the background when it is stale, so a visitor
    never waits for the database. `refresh()` returns the summary or None."""
    now = time.time() if now is None else now
    with _lock:
        value = _state["value"]
        stale = now - _state["at"] > CACHE_SECONDS
        recently_failed = now - _state["failed_at"] < RETRY_SECONDS
        if not stale or _state["running"] or recently_failed:
            return value
        _state["running"] = True

    def work():
        ok, result = False, None
        try:
            result = refresh()
            ok = True
        except Exception:
            ok = False
        finally:
            with _lock:
                if ok:
                    _state["value"], _state["at"] = result, now
                else:
                    _state["failed_at"] = now
                _state["running"] = False

    if start_thread:
        threading.Thread(target=work, daemon=True, name="cost-per-minute").start()
    else:
        work()
    return value


def reset_for_tests():
    with _lock:
        _state.update({"value": None, "at": 0.0, "failed_at": 0.0, "running": False})
