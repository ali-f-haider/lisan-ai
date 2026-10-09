"""Public API settings the owner can change from the admin page (pure logic, no I/O).

Every number the API needs to decide "may this call run" or "what does it cost" is a setting here, never a
constant in a route. The stored shape is one JSON object; `normalize` always returns a complete, safe object
(unknown or broken values fall back to the default, a bad value can never switch the API on by accident) and
`validate` is what the admin save uses (it refuses a bad value with a plain sentence instead of fixing it quietly).
"""
import math

ELIGIBILITY = ("website_plans", "everyone")

# name: (default, minimum, maximum, label)
NUMBERS = {
    "price_percent": (100, 50, 300, "Price (% of the website price)"),
    "requests_per_minute": (60, 1, 6000, "Requests per minute"),
    "burst": (10, 1, 1000, "Burst (requests at once)"),
    "concurrency_per_key": (1, 1, 20, "Active jobs per key"),
    "default_daily_credit_cap": (1000, 1, 1000000, "Suggested daily credit cap"),
    "max_daily_credit_cap": (20000, 1, 1000000, "Highest daily credit cap a key may have"),
    "keys_per_account": (5, 1, 50, "Keys per account"),
}

DEFAULTS = {
    "enabled": False,                  # the API is off until the owner switches it on
    "eligibility": "website_plans",    # who may use it: the same plans the website gates long dub with
    "jobs_visible": True,              # API jobs also appear in the customer's projects on the website
    "webhooks": False,                 # version 1 is polling only
    "price_percent": 100,
    "requests_per_minute": 60,
    "burst": 10,
    "concurrency_per_key": 1,
    "default_daily_credit_cap": 1000,
    "max_daily_credit_cap": 20000,
    "keys_per_account": 5,
}

EDITABLE = ("enabled", "eligibility", "jobs_visible", "price_percent", "requests_per_minute", "burst",
            "concurrency_per_key", "default_daily_credit_cap", "max_daily_credit_cap", "keys_per_account")


def _int(value):
    """A whole number from an int or a numeric string; None for anything else (bool, NaN, 1.5, text)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value == int(value) else None
    if isinstance(value, str):
        s = value.strip()
        if s.lstrip("-").isdigit() and len(s) < 12:
            return int(s)
    return None


def _flag(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def normalize(raw):
    """Complete settings from whatever is stored. Never raises; webhooks is always off in version 1."""
    out = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return out
    en = _flag(raw.get("enabled"))
    out["enabled"] = en if en is not None else DEFAULTS["enabled"]
    jv = _flag(raw.get("jobs_visible"))
    out["jobs_visible"] = jv if jv is not None else DEFAULTS["jobs_visible"]
    if raw.get("eligibility") in ELIGIBILITY:
        out["eligibility"] = raw["eligibility"]
    for name, (default, lo, hi, _label) in NUMBERS.items():
        n = _int(raw.get(name))
        out[name] = n if n is not None and lo <= n <= hi else default
    if out["default_daily_credit_cap"] > out["max_daily_credit_cap"]:
        out["default_daily_credit_cap"] = out["max_daily_credit_cap"]
    if out["burst"] > out["requests_per_minute"] * 10:      # a burst far above the sustained rate would be no limit at all
        out["burst"] = min(DEFAULTS["burst"], out["requests_per_minute"])
    out["webhooks"] = False
    return out


def validate(body):
    """(settings, None) when every editable field is acceptable, else (None, 'plain sentence naming the field')."""
    if not isinstance(body, dict):
        return None, "The API settings could not be read. Reload the page and save again."
    clean = {}
    for name in ("enabled", "jobs_visible"):
        v = _flag(body.get(name))
        if v is None:
            return None, "The setting '%s' must be on or off." % name.replace("_", " ")
        clean[name] = v
    if body.get("eligibility") not in ELIGIBILITY:
        return None, "Choose who may use the API: the same plans as the website, or everyone with an account."
    clean["eligibility"] = body["eligibility"]
    for name, (_d, lo, hi, label) in NUMBERS.items():
        n = _int(body.get(name))
        if n is None or not lo <= n <= hi:
            return None, "%s must be a whole number from %s to %s." % (label, lo, hi)
        clean[name] = n
    if clean["default_daily_credit_cap"] > clean["max_daily_credit_cap"]:
        return None, "The suggested daily credit cap cannot be higher than the highest cap a key may have."
    if clean["burst"] > clean["requests_per_minute"] * 10:
        return None, "Burst is far above requests per minute, which would be no limit at all. Lower the burst or raise the rate."
    clean["webhooks"] = False
    return clean, None


def limiter_config(settings):
    """The token-bucket config api_limits.rate_limit expects."""
    s = normalize(settings)
    return {"capacity": s["burst"], "refill_per_second": s["requests_per_minute"] / 60.0}


def apply_price(credits, settings):
    """The credits an API customer is quoted for work the website would charge `credits` for.
    The ONE place the price setting is applied: round up to a whole credit, never below 1 for paid work,
    and 100 % returns the website price unchanged."""
    s = normalize(settings)
    try:
        c = int(credits)
    except (TypeError, ValueError):
        raise ValueError("The credit amount is not a number.")
    if c < 0:
        raise ValueError("The credit amount cannot be negative.")
    if c == 0:
        return 0
    if s["price_percent"] == 100:
        return c
    return max(1, -(-c * s["price_percent"] // 100))


def daily_cap_for(requested, settings):
    """(cap, None) for a new key: the owner's chosen daily credit cap, which is required, within the allowed range."""
    s = normalize(settings)
    n = _int(requested)
    if n is None:
        return None, "Choose a daily credit cap for this key."
    if not 1 <= n <= s["max_daily_credit_cap"]:
        return None, "The daily credit cap must be a whole number from 1 to %d." % s["max_daily_credit_cap"]
    return n, None


def public_view(settings):
    """What any signed-in customer may see (no internals): the limits that apply to them."""
    s = normalize(settings)
    return {"enabled": s["enabled"], "requests_per_minute": s["requests_per_minute"], "burst": s["burst"],
            "concurrent_jobs_per_key": s["concurrency_per_key"], "suggested_daily_credit_cap": s["default_daily_credit_cap"],
            "max_daily_credit_cap": s["max_daily_credit_cap"], "price_percent_of_website": s["price_percent"],
            "webhooks": False}
