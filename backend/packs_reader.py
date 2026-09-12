from pathlib import Path

p = Path("main.py"); t = p.read_text(encoding="utf-8")

FUNC = '''def get_packs_from_db():
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
        print("[pricing] packs read failed:", e)
    return None

'''

if "def get_packs_from_db" not in t:
    if "CREDIT_PACKS = {" in t:
        t = t.replace("CREDIT_PACKS = {", FUNC + "CREDIT_PACKS = {", 1)
        print("added get_packs_from_db()")
    else:
        print("ANCHOR NOT FOUND: CREDIT_PACKS")

old = '@app.get("/api/billing/packs")\ndef billing_packs():\n    return {"packs": CREDIT_PACKS}'
new = '@app.get("/api/billing/packs")\ndef billing_packs():\n    return {"packs": get_packs_from_db() or CREDIT_PACKS}'
if old in t:
    t = t.replace(old, new, 1); print("packs endpoint reads DB first")
else:
    print("ENDPOINT PATTERN NOT FOUND - paste those 3 lines to me")

old2 = "    pack = CREDIT_PACKS.get(pack_key)"
new2 = "    pack = (get_packs_from_db() or CREDIT_PACKS).get(pack_key)"
if old2 in t:
    t = t.replace(old2, new2, 1); print("checkout charges the DB price (display = charge)")

p.write_text(t, encoding="utf-8")
print("saved")