"""Estimate of what Topview AI (the "API Router" reselling Wan 3.0 for
Lisan AI's lip-sync, see lipsync_service.py's _topview_wan3_lipsync) has
billed this month.

Unlike Alibaba direct (see alibaba_cost.py), Topview's own API hands back
the REAL dollar amount it charged for each job -- `data.charged_usd` in the
poll response, saved straight into lipsync_runs.charged_usd by
_update_lipsync_run(). So most of this module is just summing a number
Topview already gave us, not estimating one from a list price. The
fallback rate below is only used for a run with no recorded charged_usd
(an older row from before that column existed, or a run whose poll never
reached "completed" so the real charge was never captured).

Pure functions, no network -- same split as alibaba_cost.py: the caller
(main.py for the admin Lip-Sync tab, business_metrics.py for the Business
tab) reads the Supabase rows and passes them in, so both pages agree.
"""
NOT_BILLED = ("failed", "canceled", "cancelled", "timed_out", "unknown")

# Topview's listed Wan 3.0 rate, checked 2026-11-16 at platform.topview.ai/explore:
# $0.0485/sec (45% off the $0.0882/sec list price). Only a fallback guess for a
# run with no charged_usd on record -- a run that has one uses that instead.
FALLBACK_USD_PER_SEC = 0.0485
DEFAULT_CLIP_SEC = 10.0


def _day(ts):
    return (ts or "")[:10]


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def estimate(runs, start_day=None, end_day=None):
    """runs: [{job_id, resolution, status, created_at, charged_usd?}], one row
    per Topview request (lipsync_runs rows with provider == "topview").
    start_day / end_day (YYYY-MM-DD, inclusive) keep only that period."""

    def keep(d):
        return bool(d) and (not start_day or d >= start_day) and (not end_day or d <= end_day)

    runs = [r for r in (runs or []) if keep(_day(r.get("created_at")))]

    items = []
    for r in runs:
        if str(r.get("status") or "").lower() in NOT_BILLED:
            continue
        usd = _num(r.get("charged_usd"))
        items.append({"usd": usd if usd > 0 else None, "src": "charged" if usd > 0 else "assumed"})

    known = [i["usd"] for i in items if i["usd"]]
    avg = (sum(known) / len(known)) if known else (FALLBACK_USD_PER_SEC * DEFAULT_CLIP_SEC)

    total, assumed_usd = 0.0, 0.0
    counts = {"charged": 0, "assumed": 0}
    for i in items:
        usd = i["usd"] if i["usd"] is not None else avg
        total += usd
        counts[i["src"]] += 1
        if i["src"] == "assumed":
            assumed_usd += usd

    return {
        "estimated_usd": round(total, 2),
        "runs": len(items),
        "runs_from_charges": counts["charged"],
        "runs_assumed": counts["assumed"],
        "assumed_usd": round(assumed_usd, 2),
    }
