"""Business metrics for the admin dashboard (Business tab).

One call -- compute(ctx, days) -- reads the Supabase tables and, when it is
configured, Stripe, and returns one JSON-ready dict that the admin page turns
into tiles, charts and tables. Nothing here writes anything.

Where each number comes from (the admin page says the same in plain words):
  * money      -> Stripe charges (real gross, fees, refunds) when STRIPE_SECRET_KEY
                  is set; otherwise an ESTIMATE from credit_orders /
                  subscription_invoices priced with the current pack and tier prices
  * users      -> profiles (guests are accounts with is_guest = true)
  * usage      -> credit_spends (every charge the app made, by action)
  * long dubs  -> long_dub_events; lip-sync -> lipsync_runs
Costs are NOT stored anywhere, so the page estimates them from usage and a few
editable assumptions (see cost_inputs below).

Everything is best-effort: a table that cannot be read is reported in
"warnings" and its section is simply empty, it never fails the whole call.
"""
import datetime as _dt
import json
import time
import urllib.parse
import urllib.request

import alibaba_cost

PAGE = 1000
MAX_ROWS = 30000          # per table; "truncated" is reported when hit
STRIPE_MAX_CHARGES = 3000

# credit_spends.action -> feature group shown on the page
FEATURES = (
    ("voice", "Voice generation"),
    ("clone", "Voice cloning"),
    ("lipsync", "Lip-sync"),
    ("longdub", "Dub Long Video"),
    ("basic", "Transcribe & merge"),
)
ACTION_FEATURE = {
    "generate": "voice",
    "clone": "clone", "custom_voice": "clone",
    "lipsync": "lipsync",
    "long_dub_estimate": "longdub", "long_dub_analysis": "longdub", "long_dub_dub": "longdub",
    "transcribe": "basic", "merge": "basic",
}
JOB_ACTIONS = ("transcribe", "generate", "clone", "custom_voice", "merge", "lipsync",
               "long_dub_estimate", "long_dub_analysis", "long_dub_dub")

LONGDUB_CORE_STEPS = ("separation", "transcription", "speaker_detection", "translation", "speech_generation", "mix")


# ----------------------------------------------------------------- helpers

def _num(v, d=0.0):
    try:
        return float(v) if v is not None else float(d)
    except (TypeError, ValueError):
        return float(d)


def _day(ts):
    """'2026-10-01T12:00:00+00:00' -> '2026-10-01' ('' when missing)."""
    return str(ts or "")[:10]


def _date(day):
    return _dt.date.fromisoformat(day)


def _get(o, k, default=None):
    """Read a field from a dict or a Stripe object, whichever it is."""
    try:
        v = o[k]
    except Exception:
        v = getattr(o, k, default)
    return default if v is None else v


class _Fetcher:
    def __init__(self, url, key):
        self.url = (url or "").rstrip("/")
        self.key = key or ""
        self.warnings = []
        self.truncated = []

    def rows(self, table, select="*", params="", order="created_at.desc", cap=MAX_ROWS, soft=False):
        if not self.url or not self.key:
            self.warnings.append(f"{table}: Supabase is not configured")
            return []
        out = []
        offset = 0
        ordered = bool(order)
        while True:
            q = f"{self.url}/rest/v1/{table}?select={select}{params}&limit={PAGE}&offset={offset}"
            if ordered:
                q += f"&order={order}"
            req = urllib.request.Request(q, headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"})
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    page = json.load(r) or []
            except Exception as e:
                if soft:                         # optional columns: the caller retries without them
                    return None
                if ordered and offset == 0:      # a table without that column: read it unordered
                    ordered = False
                    continue
                detail = str(e)
                try:
                    detail += " " + e.read().decode("utf-8", "replace")[:150]
                except Exception:
                    pass
                self.warnings.append(f"{table}: {detail[:240]}")
                return out
            out.extend(page)
            if len(page) < PAGE:
                return out
            offset += PAGE
            if len(out) >= cap:
                self.truncated.append(table)
                return out


# ------------------------------------------------------------------ Stripe

def _stripe_revenue(stripe_mod, key, since_ts, tier_prices, pack_prices):
    """Real money from Stripe charges since since_ts. Returns
    {"ok": True, "mode": "live"|"test", "rows": [{day, gross, fee, refunded, kind, currency}]}
    or {"ok": False, "error": ...}. A charge counts as a subscription payment when it belongs to
    an invoice, or (if Stripe no longer shows the invoice on the charge) when its amount equals a
    subscription tier price and no credit pack price."""
    if not stripe_mod or not key:
        return {"ok": False, "error": "Stripe is not configured"}
    try:
        stripe_mod.api_key = key
        rows = []
        it = stripe_mod.Charge.list(limit=100, created={"gte": int(since_ts)},
                                    expand=["data.balance_transaction"]).auto_paging_iter()
        for ch in it:
            if len(rows) >= STRIPE_MAX_CHARGES:
                break
            if not _get(ch, "paid", False) or str(_get(ch, "status", "")) != "succeeded":
                continue
            amount = _num(_get(ch, "amount", 0)) / 100.0
            refunded = _num(_get(ch, "amount_refunded", 0)) / 100.0
            bt = _get(ch, "balance_transaction", None)
            fee = _num(_get(bt, "fee", 0)) / 100.0 if bt is not None and not isinstance(bt, str) else 0.0
            created = int(_num(_get(ch, "created", 0)))
            day = _dt.datetime.utcfromtimestamp(created).strftime("%Y-%m-%d")
            invoice = _get(ch, "invoice", None)
            rounded = round(amount, 2)
            if invoice:
                kind = "subscription"
            elif rounded in tier_prices and rounded not in pack_prices:
                kind = "subscription"
            else:
                kind = "pack"
            rows.append({"day": day, "gross": amount, "fee": fee, "refunded": refunded, "kind": kind,
                         "currency": str(_get(ch, "currency", "usd")).lower(), "ts": created})
        mode = "test" if str(key).startswith(("sk_test", "rk_test")) else "live"
        return {"ok": True, "mode": mode, "rows": rows}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


# ----------------------------------------------------------------- compute

def _buckets(first_day, last_day):
    """Day keys from first to last; weekly (Monday) keys when the span is longer than 120 days."""
    a, b = _date(first_day), _date(last_day)
    span = (b - a).days + 1
    weekly = span > 120
    keys = []
    d = a - _dt.timedelta(days=a.weekday()) if weekly else a
    step = 7 if weekly else 1
    while d <= b:
        keys.append(d.isoformat())
        d += _dt.timedelta(days=step)

    def key_of(day):
        if not day:
            return None
        x = _date(day)
        if weekly:
            x = x - _dt.timedelta(days=x.weekday())
        return x.isoformat()
    return keys, key_of, ("week" if weekly else "day")


def compute(ctx, days=30):
    """ctx: sb_url, sb_key, stripe_key, stripe_mod, pricing (the admin pricing config dict), now (optional epoch)."""
    t0 = time.time()
    now = _dt.datetime.utcfromtimestamp(ctx.get("now") or time.time())
    today = now.date()
    days = int(days or 0)
    pricing = ctx.get("pricing") or {}
    f = _Fetcher(ctx.get("sb_url"), ctx.get("sb_key"))

    packs = [p for p in (pricing.get("packs") or []) if _num(p.get("credits")) > 0]
    plans = [p for p in (pricing.get("subscriptionPlans") or []) if _num(p.get("credits_per_month")) > 0]
    tier_prices = {round(_num(p.get("price_usd")), 2) for p in plans}
    tier_prices.add(round(_num(pricing.get("subscriptionPriceUsd"), 0), 2))
    tier_prices.discard(0.0)
    pack_prices = {round(_num(p.get("price_usd")), 2) for p in packs}

    # ---- period
    if days > 0:
        p_start = today - _dt.timedelta(days=days - 1)
        prev_start = p_start - _dt.timedelta(days=days)
        prev_end = p_start - _dt.timedelta(days=1)
        since = min(prev_start, today - _dt.timedelta(days=59))
    else:
        p_start = None
        prev_start = prev_end = None
        since = None

    since_iso = (since.isoformat() + "T00:00:00Z") if since else None
    gte = f"&created_at=gte.{urllib.parse.quote(since_iso)}" if since_iso else ""

    profiles = f.rows("profiles")
    orders = f.rows("credit_orders")
    invoices = f.rows("subscription_invoices")
    spends = f.rows("credit_spends", select="uid,action,credits,generated_seconds,created_at,job_id", params=gte)
    steps_in = ",".join(("upload_started", "terms_accepted", "dub_confirmed", "output_saved") + LONGDUB_CORE_STEPS)
    events = f.rows("long_dub_events", select="job_id,step,status,created_at", params=gte + f"&step=in.({steps_in})")
    lip_runs = f.rows("lipsync_runs", select="job_id,status,resolution,created_at,billed_seconds", params=gte, soft=True)
    if lip_runs is None:                      # the billed_seconds column has not been added yet
        lip_runs = f.rows("lipsync_runs", select="job_id,status,resolution,created_at", params=gte)

    if p_start is None:                       # all time: from the first thing we know about
        firsts = [_day(r.get("created_at")) for r in profiles + orders + invoices + spends if _day(r.get("created_at"))]
        first_day = min(firsts) if firsts else today.isoformat()
        p_start = _date(first_day)
    ps, pe = p_start.isoformat(), today.isoformat()
    pv_s = prev_start.isoformat() if prev_start else None
    pv_e = prev_end.isoformat() if prev_end else None
    in_p = lambda d: bool(d) and ps <= d <= pe
    in_pv = lambda d: bool(pv_s) and bool(d) and pv_s <= d <= pv_e

    chart_first = ps
    if (today - p_start).days + 1 > 365:      # all-time charts show the last year at most
        chart_first = (today - _dt.timedelta(days=364)).isoformat()
    bucket_keys, key_of, bucket_kind = _buckets(chart_first, pe)
    series_idx = {k: i for i, k in enumerate(bucket_keys)}

    def new_series(fields):
        return [dict({"d": k}, **{x: 0 for x in fields}) for k in bucket_keys]

    def add_to(series, day, field, value=1):
        k = key_of(day)
        if k in series_idx and day >= chart_first:
            series[series_idx[k]][field] += value

    # ---- products and prices
    def pack_for(credits):
        c = int(_num(credits))
        for p in packs:
            base = int(_num(p.get("credits")))
            bonus = int(round(base * (1 + _num(p.get("bonus_pct")) / 100.0)))
            if c in (base, bonus):
                return p
        return None

    legacy_plan = {"key": "", "name": pricing.get("subscriptionName") or "Subscription",
                   "price_usd": _num(pricing.get("subscriptionPriceUsd")),
                   "credits_per_month": int(_num(pricing.get("subscriptionCredits")))}

    def plan_for_key(key):
        for p in plans:
            if (p.get("key") or "") == key:
                return p
        return legacy_plan

    def plan_for_credits(credits):
        c = int(_num(credits))
        for p in plans + [legacy_plan]:
            if int(_num(p.get("credits_per_month"))) == c:
                return p
        return None

    def est_pack_price(credits):
        p = pack_for(credits)
        return _num(p.get("price_usd")) if p else int(_num(credits)) / 100.0

    def est_invoice_price(credits):
        p = plan_for_credits(credits)
        return _num(p.get("price_usd")) if p else int(_num(credits)) / 100.0

    # ---- Stripe (real money) or the estimate from the database
    stripe_since = int(_dt.datetime.combine(since or p_start, _dt.time()).replace(tzinfo=_dt.timezone.utc).timestamp()) if (since or p_start) else 0
    if days == 0:
        stripe_since = 0
    st = _stripe_revenue(ctx.get("stripe_mod"), ctx.get("stripe_key"), stripe_since, tier_prices, pack_prices)
    revenue_rows = []
    if st.get("ok"):
        source, mode = "stripe", st.get("mode")
        revenue_rows = st["rows"]
        currencies = {r["currency"] for r in revenue_rows}
    else:
        source, mode = "estimate", None
        currencies = {"usd"}
        for o in orders:
            d = _day(o.get("created_at"))
            if d:
                revenue_rows.append({"day": d, "gross": est_pack_price(o.get("credits")), "fee": 0.0, "refunded": 0.0, "kind": "pack"})
        for v in invoices:
            d = _day(v.get("created_at"))
            if d:
                revenue_rows.append({"day": d, "gross": est_invoice_price(v.get("credits")), "fee": 0.0, "refunded": 0.0, "kind": "subscription"})

    def rev_sum(pred):
        g = fee = rf = 0.0
        n = 0
        by_kind = {"pack": [0.0, 0], "subscription": [0.0, 0]}
        for r in revenue_rows:
            if pred(r["day"]):
                g += r["gross"]; fee += r["fee"]; rf += r["refunded"]; n += 1
                by_kind[r["kind"]][0] += r["gross"]; by_kind[r["kind"]][1] += 1
        return {"gross": round(g, 2), "fees": round(fee, 2), "refunds": round(rf, 2),
                "net": round(g - fee - rf, 2), "count": n,
                "packs": round(by_kind["pack"][0], 2), "packs_count": by_kind["pack"][1],
                "subs": round(by_kind["subscription"][0], 2), "subs_count": by_kind["subscription"][1]}

    rev_p = rev_sum(in_p)
    rev_pv = rev_sum(in_pv) if pv_s else None
    rev_series = new_series(("pack", "subscription"))
    for r in revenue_rows:
        add_to(rev_series, r["day"], "pack" if r["kind"] == "pack" else "subscription", round(r["gross"], 2))
    for row in rev_series:
        row["pack"] = round(row["pack"], 2); row["subscription"] = round(row["subscription"], 2)

    # ---- users
    by_id = {}
    for p in profiles:
        if p.get("id"):
            by_id[p["id"]] = p
    registered = [p for p in profiles if not p.get("is_guest")]
    guests = len(profiles) - len(registered)
    signups_series = new_series(("new", "active"))
    new_in_p = new_in_pv = 0
    for p in registered:
        d = _day(p.get("created_at"))
        if in_p(d):
            new_in_p += 1
        elif in_pv(d):
            new_in_pv += 1
        if d:
            add_to(signups_series, d, "new")

    # spends: split real charges from admin changes and refunds
    feat_series = new_series([k for k, _ in FEATURES])
    feat_totals_p = {k: 0 for k, _ in FEATURES}
    action_credits_p = {}
    action_count_p = {}
    spent_p = spent_pv = refunded_p = 0
    admin_in_p = 0
    seen_uid_day = set()
    active_p, active_pv = set(), set()
    active_7, active_30, active_today = set(), set(), set()
    d7 = (today - _dt.timedelta(days=6)).isoformat()
    d30 = (today - _dt.timedelta(days=29)).isoformat()
    short_secs_p = 0.0
    per_user_spent = {}
    last_active = {}
    activated_ever = set()
    jobs_by_day = new_series(("jobs",))
    for s in spends:
        act = s.get("action") or ""
        d = _day(s.get("created_at"))
        uid = s.get("uid") or ""
        cr = _num(s.get("credits"))
        if act == "admin_adjustment":
            if in_p(d):
                admin_in_p += cr
            continue
        if act == "long_dub_refund":
            if in_p(d):
                refunded_p += abs(cr)
            continue
        if act not in ACTION_FEATURE:
            continue
        feat = ACTION_FEATURE[act]
        if uid:
            activated_ever.add(uid)
            if d and d >= last_active.get(uid, ""):
                last_active[uid] = d
            if d >= d30:
                active_30.add(uid)
            if d >= d7:
                active_7.add(uid)
            if d == pe:
                active_today.add(uid)
        if in_p(d):
            spent_p += cr
            feat_totals_p[feat] += cr
            action_credits_p[act] = action_credits_p.get(act, 0) + cr
            action_count_p[act] = action_count_p.get(act, 0) + 1
            if uid:
                active_p.add(uid)
                per_user_spent[uid] = per_user_spent.get(uid, 0) + cr
            if act == "generate":
                short_secs_p += _num(s.get("generated_seconds"))
            add_to(feat_series, d, feat, cr)
            add_to(jobs_by_day, d, "jobs")
            if uid:
                seen_uid_day.add((key_of(d), uid))
        elif in_pv(d):
            spent_pv += cr
            if uid:
                active_pv.add(uid)
    for k, uid in seen_uid_day:                                # distinct active users per bucket
        if k in series_idx:
            signups_series[series_idx[k]]["active"] += 1

    # ---- customers (anyone who ever paid)
    first_purchase = {}
    purchased_credits_p = {}
    purchases = []
    for o in orders:
        uid = o.get("uid") or ""
        d = _day(o.get("created_at"))
        if uid and d and d < first_purchase.get(uid, "9999"):
            first_purchase[uid] = d
        if in_p(d) and uid:
            purchased_credits_p[uid] = purchased_credits_p.get(uid, 0) + int(_num(o.get("credits")))
        purchases.append({"ts": str(o.get("created_at") or ""), "uid": uid, "kind": "Credit pack",
                          "credits": int(_num(o.get("credits"))),
                          "name": (pack_for(o.get("credits")) or {}).get("name") or "Custom amount",
                          "usd": round(est_pack_price(o.get("credits")), 2)})
    for v in invoices:
        uid = v.get("uid") or ""
        d = _day(v.get("created_at"))
        if uid and d and d < first_purchase.get(uid, "9999"):
            first_purchase[uid] = d
        if in_p(d) and uid:
            purchased_credits_p[uid] = purchased_credits_p.get(uid, 0) + int(_num(v.get("credits")))
        pl = plan_for_credits(v.get("credits"))
        purchases.append({"ts": str(v.get("created_at") or ""), "uid": uid, "kind": "Subscription",
                          "credits": int(_num(v.get("credits"))), "name": (pl or {}).get("name") or "Subscription",
                          "usd": round(est_invoice_price(v.get("credits")), 2)})
    paying_total = len(first_purchase)
    new_paying_p = sum(1 for d in first_purchase.values() if in_p(d))
    new_paying_pv = sum(1 for d in first_purchase.values() if in_pv(d))
    paying_in_p = len(purchased_credits_p)

    # products sold in the period (list prices; Stripe gives the exact total above)
    product_rows = {}
    for pr in purchases:
        d = _day(pr["ts"])
        if not in_p(d):
            continue
        row = product_rows.setdefault((pr["kind"], pr["name"]), {"kind": pr["kind"], "name": pr["name"], "orders": 0, "credits": 0, "usd": 0.0})
        row["orders"] += 1; row["credits"] += pr["credits"]; row["usd"] += pr["usd"]
    products = sorted(product_rows.values(), key=lambda r: -r["usd"])
    for r in products:
        r["usd"] = round(r["usd"], 2)
    credits_sold_p = sum(r["credits"] for r in products)

    # ---- subscriptions (snapshot)
    active_subs = [p for p in profiles if (p.get("subscription_status") or "") == "active"]
    tier_counts = {}
    mrr = 0.0
    for p in active_subs:
        pl = plan_for_key(p.get("subscription_plan_key") or "")
        k = pl.get("key") or "legacy"
        row = tier_counts.setdefault(k, {"key": k, "name": pl.get("name") or "Subscription", "price": _num(pl.get("price_usd")), "count": 0})
        row["count"] += 1
        mrr += _num(pl.get("price_usd"))
    tiers = sorted(tier_counts.values(), key=lambda r: -r["count"] * r["price"])

    # ---- funnel for people who signed up in the period
    cohort = [p for p in registered if in_p(_day(p.get("created_at")))]
    cohort_ids = {p["id"] for p in cohort if p.get("id")}
    funnel = {
        "signed_up": len(cohort),
        "activated": len(cohort_ids & activated_ever),
        "purchased": len([u for u in cohort_ids if u in first_purchase]),
        "subscribed": len([p for p in cohort if (p.get("subscription_status") or "") == "active"]),
    }

    # ---- credits economy
    out_perm = sum(int(_num(p.get("credits"))) for p in profiles)
    out_sub = sum(int(_num(p.get("subscription_credits"))) for p in profiles)
    sold_all = sum(int(_num(o.get("credits"))) for o in orders)
    sub_granted_all = sum(int(_num(v.get("credits"))) for v in invoices)
    free_each = int(_num(pricing.get("freeCredits"), 0))
    avg_price = (rev_p["gross"] / credits_sold_p) if (credits_sold_p and rev_p["gross"]) else None
    if avg_price is None and packs:
        avg_price = sum(_num(p.get("price_usd")) / max(1, _num(p.get("credits"))) for p in packs) / len(packs)

    # ---- quality: long dub + lip-sync
    ld = {"uploads": set(), "accepted": set(), "dubbed": set(), "completed": set(), "failed": set()}
    for e in events:
        d = _day(e.get("created_at"))
        if not in_p(d):
            continue
        jid, step, status = e.get("job_id"), e.get("step"), e.get("status")
        if step == "upload_started":
            ld["uploads"].add(jid)
        elif step == "terms_accepted":
            ld["accepted"].add(jid)
        elif step == "dub_confirmed" and status == "ok":
            ld["dubbed"].add(jid)
        elif step == "output_saved" and status == "ok":
            ld["completed"].add(jid)
        if status == "failed" and step in LONGDUB_CORE_STEPS:
            ld["failed"].add(jid)
    lip = {"total": 0, "succeeded": 0, "failed": 0, "other": 0}
    for r in lip_runs:
        if not in_p(_day(r.get("created_at"))):
            continue
        lip["total"] += 1
        s = str(r.get("status") or "").lower()
        if s in ("succeeded", "success", "done"):
            lip["succeeded"] += 1
        elif s in ("failed", "timed_out", "canceled", "cancelled", "error"):
            lip["failed"] += 1
        else:
            lip["other"] += 1

    # ---- top customers
    uids = set(per_user_spent) | set(purchased_credits_p)
    top = []
    for uid in uids:
        p = by_id.get(uid) or {}
        top.append({"id": uid, "name": p.get("display_name") or "", "email": p.get("email") or "",
                    "purchased_credits": purchased_credits_p.get(uid, 0), "spent_credits": int(per_user_spent.get(uid, 0)),
                    "balance": int(_num(p.get("credits")) + _num(p.get("subscription_credits"))),
                    "subscriber": (p.get("subscription_status") or "") == "active",
                    "last_active": last_active.get(uid, "")})
    top.sort(key=lambda r: (-(r["purchased_credits"] * 2 + r["spent_credits"]), r["email"]))
    top = top[:10]

    purchases.sort(key=lambda r: r["ts"], reverse=True)
    recent_purchases = []
    for pr in purchases[:10]:
        p = by_id.get(pr["uid"]) or {}
        recent_purchases.append(dict(pr, who=p.get("display_name") or p.get("email") or (pr["uid"] or "")[:8]))

    recent_activity = []
    for s in sorted(spends, key=lambda r: str(r.get("created_at") or ""), reverse=True)[:15]:
        p = by_id.get(s.get("uid") or "") or {}
        recent_activity.append({"ts": str(s.get("created_at") or ""), "who": p.get("display_name") or p.get("email") or (s.get("uid") or "")[:8],
                                "action": s.get("action") or "", "credits": _num(s.get("credits"))})

    # ---- inputs for the client-side cost model
    engine = pricing.get("voiceEngine") or "elevenlabs"
    cpc = _num(pricing.get("inworldCharsPerCredit" if engine == "inworld" else "charsPerCredit"), 60) or 60
    iw_cpc = _num(pricing.get("inworldCharsPerCredit"), 60) or 60
    clone_c = _num(pricing.get("inworldCloneCredits"), 5)
    merge_c = max(1.0, _num(pricing.get("mergeCredits"), 1))
    ld_jobs = action_count_p.get("long_dub_dub", 0)
    ld_voice_credits = max(0.0, action_credits_p.get("long_dub_dub", 0) - ld_jobs * (2 * clone_c + merge_c))
    analysis_rate = _num(pricing.get("longDubAnalysisPerMin"), 2)
    flat = _num(pricing.get("longDubFlatCredits"), 10)
    analysis_rows = action_count_p.get("long_dub_analysis", 0)
    ld_minutes = max(0.0, action_credits_p.get("long_dub_analysis", 0) - analysis_rows * flat) / analysis_rate if analysis_rate > 0 else 0.0
    lip_rate = _num(pricing.get("lipsyncCreditsPerSec"), 40) or 40
    lip_est = alibaba_cost.estimate([r for r in spends if r.get("action") == "lipsync"], lip_runs, lip_rate,
                                    start_day=ps, end_day=pe, today=now.strftime("%Y-%m-%d"))
    cost_inputs = {
        "lipsync_usd": lip_est["estimated_usd"],
        "lipsync_usd_without_promo": lip_est["usd_without_promo"],
        "lipsync_runs": lip_est["runs"],
        "lipsync_runs_assumed": lip_est["runs_assumed"],
        "voice_chars": round(action_credits_p.get("generate", 0) * cpc + ld_voice_credits * iw_cpc),
        "lipsync_seconds": round(action_credits_p.get("lipsync", 0) / lip_rate, 1),
        "longdub_minutes": round(ld_minutes, 1),
        "longdub_jobs": ld_jobs,
        "shortdub_jobs": action_count_p.get("transcribe", 0),
        "stripe_fees_estimate": round(sum(0.029 * r["gross"] + 0.30 for r in revenue_rows if in_p(r["day"])), 2),
        "period_days": (today - p_start).days + 1,
    }

    mau, wau, dau = len(active_30), len(active_7), len(active_today)
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "took_ms": int((time.time() - t0) * 1000),
        "period": {"days": days, "start": ps, "end": pe, "prev_start": pv_s, "prev_end": pv_e,
                   "bucket": bucket_kind, "all_time": days == 0},
        "warnings": f.warnings, "truncated": f.truncated,
        "revenue": {"source": source, "mode": mode, "error": st.get("error") if not st.get("ok") else None,
                    "mixed_currency": len(currencies) > 1, "period": rev_p, "prev": rev_pv, "series": rev_series,
                    "products": products},
        "users": {"total": len(profiles), "registered": len(registered), "guests": guests,
                  "new": new_in_p, "prev_new": new_in_pv if pv_s else None,
                  "active": len(active_p), "prev_active": len(active_pv) if pv_s else None,
                  "dau": dau, "wau": wau, "mau": mau,
                  "paying_total": paying_total, "paying_in_period": paying_in_p,
                  "new_paying": new_paying_p, "prev_new_paying": new_paying_pv if pv_s else None,
                  "conversion_pct": round(100.0 * paying_total / len(registered), 1) if registered else 0.0,
                  "series": signups_series, "funnel": funnel},
        "subscriptions": {"active": len(active_subs), "mrr": round(mrr, 2), "tiers": tiers},
        "credits": {"outstanding_permanent": out_perm, "outstanding_subscription": out_sub,
                    "sold_period": credits_sold_p, "sold_all": sold_all, "subscription_granted_all": sub_granted_all,
                    "spent_period": int(spent_p), "prev_spent": int(spent_pv) if pv_s else None,
                    "refunded_period": int(refunded_p), "admin_net_period": int(admin_in_p),
                    "free_signup_each": free_each, "free_signup_period_est": free_each * new_in_p,
                    "avg_price_per_credit": round(avg_price, 5) if avg_price else None,
                    "liability_usd": round(out_perm * avg_price, 2) if avg_price else None,
                    "features": [{"key": k, "name": n, "credits": int(feat_totals_p[k])} for k, n in FEATURES],
                    "series": feat_series},
        "usage": {"short_dub_minutes": round(short_secs_p / 60.0, 1),
                  "counts": {a: action_count_p.get(a, 0) for a in JOB_ACTIONS},
                  "long_dub": {k: len(v) for k, v in ld.items()},
                  "lipsync": lip, "jobs_series": jobs_by_day},
        "top_customers": top,
        "recent_purchases": recent_purchases,
        "recent_activity": recent_activity,
        "cost_inputs": cost_inputs,
    }
