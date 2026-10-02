"""Estimate of what Alibaba Model Studio (Wan 3.0 lip-sync) has billed.

Pure functions, no network: the caller reads the Supabase rows and passes them in
(main.py for the Lip-Sync card, business_metrics.py for the Business tab), so both
pages show the same number.

Prices come from Ali's own Alibaba bill (consumedetailbill CSV, 1-2 Oct 2026):
  * Alibaba bills "video_duration" seconds, list price per billed second:
      480P $0.041256, 720P $0.082513 (exactly 1 : 2). 1080P has not appeared on a
      bill yet; 1 : 2 : 4 is assumed ($0.165026).
  * Promotion "Limited-Time Offer: 30% Off Wan3.0-Video": 2026-08-23 -> 2026-11-01 (UTC).
  * Each second of the customer's clip is billed about twice (the reference video that
    goes in plus the video that comes out). If the page and the real bill ever drift
    apart, BILLED_PER_CLIP_SEC is the number to adjust.

Where the billed seconds of one run come from, best source first:
  1. "bill"     lipsync_runs.billed_seconds, written from Alibaba's own task result
  2. "charged"  the customer charge for that job: credits / credits-per-second at the
                run's resolution = clip seconds, x BILLED_PER_CLIP_SEC
  3. "assumed"  a run nobody was charged for (an admin test, a free retry) and with no
                billed_seconds: the average of the known runs (30 s if none are known)
Runs Alibaba did not bill (failed / canceled / timed out) are not counted.
"""
PRICE_PER_BILLED_SEC = {"480P": 0.041256, "720P": 0.082513, "1080P": 0.165026}
FACTOR = {"480P": 0.5, "720P": 1.0, "1080P": 2.0}        # same as config.LIPSYNC_RES_FACTOR
PROMO_START = "2026-08-23"
PROMO_END = "2026-11-01"
PROMO_DISCOUNT = 0.30
BILLED_PER_CLIP_SEC = 2.0
DEFAULT_BILLED_SEC = 30.0
NOT_BILLED = ("failed", "canceled", "cancelled", "timed_out", "unknown")


def _res(v):
    s = str(v or "").strip().upper().replace(" ", "")
    if s.isdigit():
        s += "P"
    return s if s in PRICE_PER_BILLED_SEC else "720P"


def _day(ts):
    return (ts or "")[:10]


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def estimate(spends, runs, base_credits_per_sec, start_day=None, end_day=None, today=None):
    """spends: [{credits, created_at, job_id}] lip-sync charges; runs: [{job_id,
    resolution, status, created_at, billed_seconds?}] one per Alibaba task.
    start_day / end_day (YYYY-MM-DD, inclusive) keep only that period."""
    base = _num(base_credits_per_sec, 40) or 40.0

    def keep(d):
        return bool(d) and (not start_day or d >= start_day) and (not end_day or d <= end_day)

    runs = [r for r in (runs or []) if keep(_day(r.get("created_at")))]
    spends = [s for s in (spends or []) if keep(_day(s.get("created_at"))) and _num(s.get("credits")) > 0]

    res_by_job = {}
    for r in runs:
        if r.get("job_id") and r.get("resolution"):
            res_by_job[r["job_id"]] = _res(r["resolution"])
    charges = {}                                   # job_id -> [credits of each charge]
    for s in spends:
        charges.setdefault(s.get("job_id") or "", []).append(_num(s.get("credits")))

    def clip_seconds(job):
        cs = charges.get(job)
        if not cs:
            return None
        rate = base * FACTOR[res_by_job.get(job, "720P")]
        return (sum(cs) / len(cs)) / rate if rate > 0 else None

    items = []                                     # one entry per run Alibaba billed
    runs_jobs = set()
    for r in runs:
        job = r.get("job_id")
        runs_jobs.add(job)
        if str(r.get("status") or "").lower() in NOT_BILLED:
            continue
        res = _res(r.get("resolution"))
        bs = _num(r.get("billed_seconds"))
        if bs > 0:
            src = "bill"
        else:
            cs = clip_seconds(job)
            bs, src = (cs * BILLED_PER_CLIP_SEC, "charged") if cs else (None, "assumed")
        items.append({"res": res, "billed": bs, "src": src, "day": _day(r.get("created_at"))})
    for job, cs_list in charges.items():           # a charge with no run record at all
        if job in runs_jobs:
            continue
        for c in cs_list:
            items.append({"res": "720P", "billed": c / base * BILLED_PER_CLIP_SEC, "src": "charged", "day": ""})
    known = [i["billed"] for i in items if i["billed"]]
    avg = (sum(known) / len(known)) if known else DEFAULT_BILLED_SEC
    for i in items:
        if i["billed"] is None:
            i["billed"] = avg

    by_res, total, list_total, unch_usd = {}, 0.0, 0.0, 0.0
    counts = {"bill": 0, "charged": 0, "assumed": 0}
    for i in items:
        lst = i["billed"] * PRICE_PER_BILLED_SEC[i["res"]]
        promo = bool(i["day"]) and PROMO_START <= i["day"] < PROMO_END
        usd = lst * (1 - PROMO_DISCOUNT) if promo else lst
        g = by_res.setdefault(i["res"], {"runs": 0, "billed_seconds": 0.0, "clip_seconds": 0.0, "usd": 0.0})
        g["runs"] += 1
        g["billed_seconds"] += i["billed"]
        g["clip_seconds"] += i["billed"] / BILLED_PER_CLIP_SEC
        g["usd"] += usd
        total += usd
        list_total += lst
        counts[i["src"]] += 1
        if i["src"] == "assumed":
            unch_usd += usd
    for g in by_res.values():
        g["billed_seconds"] = round(g["billed_seconds"], 1)
        g["clip_seconds"] = round(g["clip_seconds"], 1)
        g["usd"] = round(g["usd"], 2)
    today = today or ""
    return {
        "estimated_usd": round(total, 2),
        "usd_without_promo": round(list_total, 2),
        "by_resolution": by_res,
        "runs": len(items),
        "runs_from_bill": counts["bill"],
        "runs_from_charges": counts["charged"],
        "runs_assumed": counts["assumed"],
        "assumed_usd": round(unch_usd, 2),
        "estimated_seconds": round(sum(g["clip_seconds"] for g in by_res.values()), 1),
        "promo_ends": PROMO_END,
        "promo_active_now": bool(today) and PROMO_START <= today < PROMO_END,
        "billed_per_clip_sec": BILLED_PER_CLIP_SEC,
    }


def billed_seconds_from_usage(usage):
    """Billed seconds out of the `usage` object of a finished DashScope task.
    Wan video tasks report input_video_duration + output_video_duration (or one
    `duration` / `video_duration`); returns None if none of them is there."""
    if not isinstance(usage, dict):
        return None
    inp = _num(usage.get("input_video_duration"))
    out = _num(usage.get("output_video_duration"))
    if inp > 0 or out > 0:
        return round(inp + out, 2)
    for k in ("video_duration", "duration"):
        v = _num(usage.get(k))
        if v > 0:
            return round(v, 2)
    return None
