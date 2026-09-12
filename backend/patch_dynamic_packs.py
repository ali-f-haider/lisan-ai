# patch_dynamic_packs.py
# Makes /api/billing/packs read from pricing_config table (single source of truth).
# Removes hardcoded pack prices from main.py.
# Buy modal keeps working — same response shape, just sourced from the DB now.

import re
from pathlib import Path

MP = Path("main.py")
if not MP.exists():
    raise SystemExit("main.py not found")

mt = MP.read_text(encoding="utf-8")

# Nuke any existing /api/billing/packs route(s)
pattern = re.compile(
    r'@app\.get\("/api/billing/packs"\).*?(?=\n@app\.|\ndef \w+\(|\Z)',
    re.DOTALL
)
mt_new, count = pattern.subn('', mt)
print(f"Removed {count} existing /api/billing/packs route(s).")

# Add the new route at end of file
NEW_ROUTE = '''

@app.get("/api/billing/packs")
def billing_packs_dynamic():
    """Returns credit packs from pricing_config (managed by admin panel).
    Maps the admin array structure into the keyed structure the buy modal expects."""
    cfg = _get_pricing_config()
    packs_array = cfg.get("packs", [])
    # Default fallback if DB is empty
    if not packs_array:
        packs_array = [
            {"name": "Starter", "credits": 1500, "price_usd": 15.0, "bonus_pct": 0, "stripe_link": ""},
            {"name": "Standard", "credits": 4000, "price_usd": 35.0, "bonus_pct": 14, "stripe_link": ""},
            {"name": "Pro", "credits": 10000, "price_usd": 75.0, "bonus_pct": 33, "stripe_link": ""},
            {"name": "Studio", "credits": 25000, "price_usd": 150.0, "bonus_pct": 66, "stripe_link": ""}
        ]
    # Map array → keyed dict (lowercase name as key)
    # The buy modal iterates ["starter","standard","pro","business"]
    # so we map by lowercased first word of the pack name
    keyed = {}
    for p in packs_array:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        key = name.lower().split()[0]
        # Keep backward-compat with old keys (business vs studio)
        if key in ("studio", "business"):
            key = "business"
        keyed[key] = {
            "credits": int(p.get("credits", 0)),
            "amount_usd": float(p.get("price_usd", 0)),
            "bonus_pct": int(p.get("bonus_pct", 0)),
            "stripe_link": p.get("stripe_link", "")
        }
    return {"packs": keyed, "price_per_min": cfg.get("pricePerMin", 150)}

'''

mt_new += NEW_ROUTE
MP.write_text(mt_new, encoding="utf-8")
print("OK: /api/billing/packs now reads from pricing_config table.")
print("    Admin panel is the single source of truth for prices.")