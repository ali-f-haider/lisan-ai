import re
from pathlib import Path

mp = Path("main.py"); mt = mp.read_text(encoding="utf-8")
changed = False

BLOCK = '''
# ---- DB-backed packs (admin-editable via pricing_config.packs), fallback + 60s cache ----
_PACKS_CACHE = {"ts": 0.0, "data": None}

def _norm_pack(v):
    if not isinstance(v, dict):
        return None
    amt = float(v.get("amount_usd") or v.get("amountUsd") or v.get("price") or v.get("usd") or 0)
    cr = int(v.get("credits") or v.get("credit") or 0)
    if amt > 0 and cr > 0:
        return {"amount_usd": round(amt, 2), "credits": cr}
    return None

def _load_packs_from_db():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/pricing_config?select=packs&limit=1",
            headers={"apikey": SUPABASE_SERVICE_KEY,
                     "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            rows = json.load(r)
        if not rows:
            return None
        packs = rows[0].get("packs")
        out = {}
        if isinstance(packs, dict):
            for k, v in packs.items():
                np = _norm_pack(v)
                if np:
                    out[k] = np
        elif isinstance(packs, list):
            for v in packs:
                np = _norm_pack(v)
                if np:
                    k = v.get("key") or v.get("id") or v.get("name")
                    if k:
                        out[str(k).lower()] = np
        return out or None
    except Exception as e:
        print("[pricing] packs DB read failed, using built-in defaults:", e)
        return None

def get_packs():
    now = _time.time()
    if _PACKS_CACHE["data"] is not None and (now - _PACKS_CACHE["ts"]) < 60:
        return _PACKS_CACHE["data"]
    data = _load_packs_from_db() or CREDIT_PACKS
    _PACKS_CACHE["ts"] = now
    _PACKS_CACHE["data"] = data
    return data
'''

if "def get_packs" not in mt:
    m = re.search(r'CREDIT_PACKS\s*=\s*\{.*?\n\}', mt, re.S)
    if m:
        mt = mt[:m.end()] + "\n" + BLOCK + mt[m.end():]
        changed = True
        print("inserted DB-backed get_packs() after CREDIT_PACKS")
    else:
        print("WARNING: CREDIT_PACKS block not found")
else:
    print("get_packs already present")

if re.search(r'return\s*\{\s*"packs"\s*:\s*CREDIT_PACKS\s*\}', mt):
    mt = re.sub(r'return\s*\{\s*"packs"\s*:\s*CREDIT_PACKS\s*\}', 'return {"packs": get_packs()}', mt)
    changed = True
    print("/api/billing/packs now serves DB packs")

if re.search(r'pack\s*=\s*CREDIT_PACKS\.get\(pack_key\)', mt):
    mt = re.sub(r'pack\s*=\s*CREDIT_PACKS\.get\(pack_key\)', 'pack = get_packs().get(pack_key)', mt)
    changed = True
    print("Stripe checkout now charges DB pack prices")

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