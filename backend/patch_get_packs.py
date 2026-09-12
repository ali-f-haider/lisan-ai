# patch_get_packs.py
# Replaces the body of get_packs() so it reads from pricing_config (admin-managed).
# Keeps the same return shape so /api/billing/checkout keeps working.
#
# Before: get_packs() returned a hardcoded dict
# After:  get_packs() calls _get_pricing_config() and maps the array to keyed dict

import re
from pathlib import Path

MP = Path("main.py")
if not MP.exists():
    raise SystemExit("main.py not found")

mt = MP.read_text(encoding="utf-8")

# Find the get_packs function and replace its body
# Match: def get_packs(...) ... up to next def or @app line
pattern = re.compile(
    r'(def get_packs\([^)]*\)[^:]*:\s*\n)'  # function signature line
    r'((?:[ \t]+[^\n]*\n)*)',                 # indented body
    re.MULTILINE
)

m = pattern.search(mt)
if not m:
    print("ERROR: could not find def get_packs() in main.py")
    raise SystemExit(1)

print("Found get_packs() at char", m.start())
print("Original body (first 200 chars):", m.group(2)[:200].replace('\n', '\\n'))

NEW_BODY = '''    """Returns credit packs keyed by pack_key (starter, standard, pro, business).
    Reads from pricing_config table — admin panel is the single source of truth."""
    cfg = _get_pricing_config()
    packs_array = cfg.get("packs", [])
    # Fallback defaults if DB is empty
    if not packs_array:
        packs_array = [
            {"name": "Starter", "credits": 1500, "price_usd": 15.0, "bonus_pct": 0, "stripe_link": ""},
            {"name": "Standard", "credits": 4000, "price_usd": 35.0, "bonus_pct": 14, "stripe_link": ""},
            {"name": "Pro", "credits": 10000, "price_usd": 75.0, "bonus_pct": 33, "stripe_link": ""},
            {"name": "Studio", "credits": 25000, "price_usd": 150.0, "bonus_pct": 66, "stripe_link": ""}
        ]
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
    return keyed
'''

# Replace just the body (keep the signature)
new_mt = mt[:m.start()] + m.group(1) + NEW_BODY + mt[m.end():]

# Idempotency check
if "_get_pricing_config" in m.group(2):
    print("SKIP: get_packs() already reads from _get_pricing_config — no change needed.")
    raise SystemExit(0)

MP.write_text(new_mt, encoding="utf-8")
print("OK: get_packs() now reads from pricing_config table.")
print("    /api/billing/checkout will use admin-managed prices.")