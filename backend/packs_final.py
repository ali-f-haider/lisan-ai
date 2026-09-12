import re
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
        if not rows:
            return None
        packs = rows[0].get("packs")
        items = []
        if isinstance(packs, dict):
            items = list(packs.items())
        elif isinstance(packs, list):
            for v in packs:
                if isinstance(v, dict):
                    k = v.get("key") or v.get("id") or v.get("name") or v.get("pack")
                    if k:
                        items.append((k, v))
        out = {}
        for k, v in items:
            if not isinstance(v, dict):
                continue
            amt = None
            for n in ("amount_usd", "amountUsd", "price_usd", "price", "usd"):
                if v.get(n) not in (None, ""):
                    amt = v.get(n); break
            if amt in (None, "") and v.get("amount_cents") not in (None, ""):
                amt = float(v["amount_cents"]) / 100.0
            cr = None
            for n in ("credits", "credit", "credit_count"):
                if v.get(n) not in (None, ""):
                    cr = v.get(n); break
            try:
                amt = float(amt); cr = int(cr)
            except (TypeError, ValueError):
                continue
            if amt > 0 and cr > 0:
                out[str(k).lower()] = {"amount_usd": round(amt, 2), "credits": cr}
        print(f"[pricing] packs read from DB: {out}")
        return out or None
    except Exception as e:
        print(f"[pricing] DB fetch failed: {e}")
        return None

'''

# insert or replace the reader function
if "def get_packs_from_db" in t:
    t = re.sub(r'def get_packs_from_db\(\):.*?(?=\n(?:def |@app\.|CREDIT_PACKS))', FUNC, t, flags=re.S)
    print("reader function updated")
elif "CREDIT_PACKS = {" in t:
    t = t.replace("CREDIT_PACKS = {", FUNC + "CREDIT_PACKS = {", 1)
    print("reader function inserted")
else:
    print("ANCHOR NOT FOUND")

# clean, simple endpoint (replaces any old/debug version)
t = re.sub(r'@app\.get\("/api/billing/packs"\)\ndef billing_packs\(\):.*?(?=\n@app\.)',
           '@app.get("/api/billing/packs")\ndef billing_packs():\n    return {"packs": get_packs_from_db() or CREDIT_PACKS}\n',
           t, flags=re.S)
print("endpoint set to DB-first")

# checkout charges the same numbers the user sees
t = re.sub(r'pack = CREDIT_PACKS\.get\(pack_key\)',
           'pack = (get_packs_from_db() or CREDIT_PACKS).get(pack_key)', t)
print("checkout aligned")

p.write_text(t, encoding="utf-8")
print("saved")