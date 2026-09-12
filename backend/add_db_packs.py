import re
from pathlib import Path

p = Path("main.py")
t = p.read_text(encoding="utf-8")

# 1. The DB fetch function (placed at the root level to avoid indentation errors)
func_code = """def get_packs_from_db():
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
    t = t.replace("CREDIT_PACKS = {", func_code + "CREDIT_PACKS = {", 1)
    print("1. Added get_packs_from_db()")

# 2. Update the packs endpoint
t = re.sub(r'return \{"packs": CREDIT_PACKS\}', 'return {"packs": get_packs_from_db() or CREDIT_PACKS}', t)
print("2. Updated billing_packs endpoint")

# 3. Update the checkout endpoint
t = re.sub(r'pack = CREDIT_PACKS\.get\(pack_key\)', 'pack = (get_packs_from_db() or CREDIT_PACKS).get(pack_key)', t)
print("3. Updated checkout endpoint")

p.write_text(t, encoding="utf-8")
print("Saved main.py")