import re
from pathlib import Path

mp = Path("main.py"); mt = mp.read_text(encoding="utf-8")
changed = False

BLOCK = '''
# ---- DB-backed packs (admin-editable via pricing_config.packs), 60s cache ----
_PACKS_STATE = {"ts": 0.0, "data": None, "source": "defaults"}

def _norm_pack(v):
    if not isinstance(v, dict):
        return None
    amt = v.get("amount_usd") or v.get("amountUsd") or v.get("price_usd") or v.get("price") or v.get("usd")
    cents = v.get("amount_cents") or v.get("cents")
    if amt in (None, "", 0) and cents:
        amt = float(cents) / 100.0
    cr = v.get("credits") or v.get("credit") or v.get("credit_count")
    try:
        amt = float(amt or 0); cr = int(cr or 0)
    except Exception:
        return None
    if amt > 0 and cr > 0:
        return {"amount_usd": round(amt, 2), "credits": cr}
    return None

def _load_packs_from_db():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("[pricing] missing SUPABASE_URL/SERVICE_KEY - using defaults")
        return None
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/pricing_config?select=packs&limit=1",
            headers={"apikey": SUPABASE_SERVICE_KEY,
                     "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            rows = json.load(r)
        if not rows:
            print("[pricing] pricing_config has NO rows yet - save once in Admin > Pricing")
            return None
        packs = rows[0].get("packs")
        out = {}
        if isinstance(packs, dict):
            for k, v in packs.items():
                np = _norm_pack(v)
                if np: out[k] = np
        elif isinstance(packs, list):
            for v in packs:
                np = _norm_pack(v)
                if np:
                    k = v.get("key") or v.get("id") or v.get("name") or v.get("pack")
                    if k: out[str(k).lower()] = np
        if out:
            return out
        print("[pricing] pricing_config.packs empty/unparsable:", str(packs)[:200])
        return None
    except Exception as e:
        print("[pricing] packs DB read failed:", e)
        return None

def get_packs():
    now = _time.time()
    if _PACKS_STATE["data"] is not None and (now - _PACKS_STATE["ts"]) < 60:
        return _PACKS_STATE["data"]
    db = _load_packs_from_db()
    if db:
        _PACKS_STATE["data"], _PACKS_STATE["source"] = db, "db"
    else:
        _PACKS_STATE["data"], _PACKS_STATE["source"] = CREDIT_PACKS, "defaults"
    _PACKS_STATE["ts"] = now
    return _PACKS_STATE["data"]
'''

if "def get_packs" not in mt:
    m = re.search(r'CREDIT_PACKS\s*=\s*\{.*?\n\}', mt, re.S)
    if m:
        mt = mt[:m.end()] + "\n" + BLOCK + mt[m.end():]
        changed = True
        print("inserted get_packs() (DB-backed) after CREDIT_PACKS")
    else:
        print("WARNING: CREDIT_PACKS block not found")
else:
    print("get_packs already present")

if re.search(r'return\s*\{\s*"packs"\s*:\s*CREDIT_PACKS\s*\}', mt):
    mt = re.sub(r'return\s*\{\s*"packs"\s*:\s*CREDIT_PACKS\s*\}',
                'return {"packs": get_packs(), "source": _PACKS_STATE["source"]}', mt)
    changed = True
    print("packs endpoint now DB-backed + reports source")
elif re.search(r'return\s*\{\s*"packs"\s*:\s*get_packs\(\)\s*\}', mt):
    mt = re.sub(r'return\s*\{\s*"packs"\s*:\s*get_packs\(\)\s*\}',
                'return {"packs": get_packs(), "source": _PACKS_STATE["source"]}', mt)
    changed = True
    print("packs endpoint now reports source")

if re.search(r'pack\s*=\s*CREDIT_PACKS\.get\(pack_key\)', mt):
    mt = re.sub(r'pack\s*=\s*CREDIT_PACKS\.get\(pack_key\)', 'pack = get_packs().get(pack_key)', mt)
    changed = True
    print("checkout now charges DB prices")

if changed:
    try:
        compile(mt, "main.py", "exec")
        print("syntax check PASSED")
        mp.write_text(mt, encoding="utf-8")
        print("main.py written")
    except SyntaxError as e:
        print("SYNTAX ERROR - nothing written:", e)
else:
    print("nothing changed")