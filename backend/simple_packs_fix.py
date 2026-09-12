from pathlib import Path

p = Path("main.py")
t = p.read_text(encoding="utf-8")

# 1. Add the simple DB fetch function (mirrors get_credits)
fetch_func = '''
def get_packs_from_db():
    """Fetches pricing packs from Supabase. Mirrors get_credits()."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/pricing_config?select=packs&limit=1",
            headers={"apikey": SUPABASE_SERVICE_KEY}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            rows = json.load(r)
        if rows and isinstance(rows[0].get("packs"), dict):
            return rows[0]["packs"]
    except Exception as e:
        print(f"[pricing] DB fetch failed, using defaults: {e}")
    return None

'''

if "def get_packs_from_db" not in t:
    t = t.replace("CREDIT_PACKS = {", fetch_func + "CREDIT_PACKS = {", 1)

# 2. Update the endpoint to use it
old_endpoint = '''@app.get("/api/billing/packs")
def billing_packs():
    return {"packs": CREDIT_PACKS}'''

new_endpoint = '''@app.get("/api/billing/packs")
def billing_packs():
    db_packs = get_packs_from_db()
    return {"packs": db_packs if db_packs else CREDIT_PACKS}'''

if old_endpoint in t:
    t = t.replace(old_endpoint, new_endpoint, 1)
    p.write_text(t, encoding="utf-8")
    print("✅ SUCCESS: Added simple DB fetch for packs.")
else:
    print("❌ ERROR: Could not find the exact packs endpoint to replace.")