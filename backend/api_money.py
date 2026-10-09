"""Quotes and approvals for the paid steps of the public API (pure logic, no I/O).

The numbers come from the website's own calculators (estimate, analysis amount, exact dub price, music repair
maximum); this module only turns them into a quote, decides whether an approval may go ahead, and checks the key's
daily credit cap. It never computes a price itself.

Long dub steps: estimate (the fee charged when the upload is finished), analysis (charged when the estimate is
accepted) and dub (the exact price, charged when the reviewed text is approved; the dubbing includes the clones and
the final assembly). `quoted_credits` is the WHOLE maximum of the step: fixed + additional_max (the optional music
repair, charged only for repairs that really happen).
"""
import hashlib
import json
from datetime import datetime, timezone

QUOTE_TTL = 600                       # seconds a quote stays valid (estimated product default, not measured)
LONG_STEPS = ("estimate", "analysis", "dub")

NOTICES = {
    "estimate": "This is the fee for producing the estimate. Nothing else is charged by this step.",
    "analysis": "This starts the analysis of your file: listening, finding the speakers and translating. The exact price of the dubbing is fixed later, after you can review the text.",
    "dub": "This is the exact price of the dubbing, including the voices, the final assembly and the sound mix. Music repair, if any, is charged only for repairs that really happen, up to the maximum shown.",
}


def _credits(value):
    if type(value) is not int or value < 0 or value > 2147483647:
        raise ValueError("A credit amount is not valid.")
    return value


def numbers_estimate(fee, paid_fee):
    """The fee for producing the estimate; free when it was already paid (a retry after a failed finish)."""
    fee, paid = _credits(int(fee)), _credits(int(paid_fee))
    fixed = 0 if paid else fee
    return {"fixed": fixed, "additional_max": 0, "required_balance": fee, "already_paid": paid,
            "breakdown": [("estimate_fee", fixed)] if fixed else []}


def numbers_analysis(est, paid):
    """The analysis amount the website charges when an estimate is accepted (analysis + flat fee + speaker check)."""
    need = _credits(int(est["analysis"]) + int(est.get("flat", 0) or 0) + int(est.get("speaker_check", 0) or 0))
    paid_fee, paid_analysis = _credits(int(paid.get("fee", 0))), _credits(int(paid.get("analysis", 0)))
    fixed = 0 if paid_analysis else need
    parts = [("analysis", int(est["analysis"])), ("processing", int(est.get("flat", 0) or 0)), ("speaker_check", int(est.get("speaker_check", 0) or 0))]
    return {"fixed": fixed, "additional_max": 0,
            "required_balance": _credits(max(0, int(est["total"]) - paid_fee)),   # the website asks the balance to cover the whole estimate
            "already_paid": paid_fee + paid_analysis,
            "breakdown": [(c, v) for c, v in parts if v and fixed]}


def numbers_dub(price, music_max, keep_music):
    """The exact dub price from the reviewed text, plus the music repair maximum (0 when the music is not kept)."""
    fixed = _credits(int(price["due"]))
    extra = _credits(int(music_max)) if keep_music else 0
    parts = [("voice", int(price["voice"])), ("clones", int(price["clones"])), ("merge", int(price["merge"]))]
    lip = price.get("lipsync")
    if lip:
        parts.append(("lipsync", int(lip["credits"])))
    if extra:
        parts.append(("music_repair_max", extra))
    return {"fixed": fixed, "additional_max": extra, "required_balance": fixed + extra,
            "already_paid": _credits(int(price.get("already_paid", 0))),
            "breakdown": [(c, v) for c, v in parts if v]}


def price_version(cfg, percent):
    """A short fingerprint of the price list in force (the scaled list and the percentage). A quote made under one
    version is refused under another, so a price change is never applied silently."""
    rows = sorted((k, v) for k, v in dict(cfg or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool))
    raw = json.dumps([rows, int(percent)], separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def build_quote(quote_id, job_id, step, numbers, revision, now, pversion, terms_version,
                keep_music=True, tracks=True, speaker_ids=None):
    if step not in LONG_STEPS:
        raise ValueError("Unknown step.")
    fixed, extra = _credits(numbers["fixed"]), _credits(numbers["additional_max"])
    return {"id": quote_id, "job_id": job_id, "step": step, "quoted_credits": fixed + extra, "fixed_credits": fixed,
            "additional_max_credits": extra, "required_balance": _credits(numbers["required_balance"]),
            "already_paid_credits": _credits(numbers["already_paid"]), "revision": int(revision),
            "expires_at": iso(now + QUOTE_TTL), "price_version": pversion, "keep_music": bool(keep_music),
            "tracks": bool(tracks), "speaker_ids": list(speaker_ids or []),
            "breakdown": [{"code": c, "credits": _credits(v)} for c, v in numbers["breakdown"]],
            "notice": NOTICES[step], "terms_version": terms_version}


def check_accept(stored, body, live, live_pversion, live_revision, expires_ts, now):
    """None when the approval may go ahead, else the closed error code. Order matters (see the API README):
    a live price above the customer's ceiling is max_credits_exceeded; any other change is quote_changed."""
    if not isinstance(stored, dict):
        return "not_found"
    if now >= expires_ts:
        return "quote_changed"
    if body.get("terms_version") != stored.get("terms_version"):
        return "consent_required"
    if body.get("quoted_credits") != stored.get("quoted_credits"):
        return "quote_changed"
    ceiling = body.get("max_credits")
    live_total = int(live["fixed"]) + int(live["additional_max"])
    if type(ceiling) is not int or live_total > ceiling or int(stored["quoted_credits"]) > ceiling:
        return "max_credits_exceeded"
    if (live["fixed"] != stored.get("fixed_credits") or live["additional_max"] != stored.get("additional_max_credits")
            or live_pversion != stored.get("price_version") or int(live_revision) != int(stored.get("revision"))):
        return "quote_changed"
    return None


def cap_check(spent_by_others, reserve, cap):
    """True when `reserve` more credits fit under the key's daily cap. `spent_by_others` already counts every
    reservation of the day except the one being made (a retry of the same step is not counted twice)."""
    for v in (spent_by_others, reserve, cap):
        if type(v) is not int or v < 0:
            return False
    return spent_by_others + reserve <= cap


def selection_key(keep_music, tracks, speaker_ids):
    """Identifies WHICH variant of a step was approved, so the same step cannot be paid twice with a different choice."""
    raw = json.dumps([bool(keep_music), bool(tracks), sorted(speaker_ids or [])], separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]
