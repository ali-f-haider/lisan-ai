import re
from pathlib import Path

p = Path("main.py")
t = p.read_text(encoding="utf-8")

# 1. The DB fetch function
func = """def get_packs_from_db():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/pricing_config?select=packs&limit=1",
            headers={"apikey": SUPABASE_SERVICE_KEY})
        with urllib.request.urlopen(req, timeout=5) as r:
            rows = json.load(r)
        if rows and isinstance(rows[0].get("packs"), dict) and rows[0]["packs"]:
            return rows[0]["packs"]
    except Exception as e:
        print(f"[pricing] DB fetch failed: {e}")
    return None

"""

if "def get_packs_from_db" not in t:
    t = t.replace("CREDIT_PACKS = {", func + "CREDIT_PACKS = {", 1)
    print("1. Added get_packs_from_db()")

# 2. Update the packs endpoint (using regex to find it no matter the spacing)
old_endpoint = re.search(r'(@app\.get\("/api/billing/packs"\)\s*def billing_packs\(\):\s*)return \{"packs": CREDIT_PACKS\}', t)
if old_endpoint:
    t = t[:old_endpoint.start()] + old_endpoint.group(1) + 'db_packs = get_packs_from_db()\n    return {"packs": db_packs if db_packs else CREDIT_PACKS}' + t[old_endpoint.end():]
    print("2. Updated /api/billing/packs endpoint")
else:
    print("2. Packs endpoint already updated or not found")

# 3. Update the checkout endpoint
if "pack = CREDIT_PACKS.get(pack_key)" in t:
    t = t.replace("pack = CREDIT_PACKS.get(pack_key)", "pack = (get_packs_from_db() or CREDIT_PACKS).get(pack_key)")
    print("3. Updated Stripe checkout")
else:
    print("3. Checkout already updated or not found")

p.write_text(t, encoding="utf-8")
print("Saved main.py")