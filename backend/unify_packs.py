import re
from pathlib import Path

p = Path("main.py")
t = p.read_text(encoding="utf-8")
original = t

m = re.search(r'@app\.get\("/api/billing/packs"\)\s*\ndef billing_packs\(\):', t)
if not m:
    print("❌ Could not find /api/billing/packs endpoint")
else:
    rest = t[m.end():]
    end_m = re.search(r'\n(?=@app\.|def |[A-Z_]+\s*=|----------)', rest)
    func_end = m.end() + end_m.start() if end_m else len(t)

    new_func = '''@app.get("/api/billing/packs")
def billing_packs():
    cfg = _get_pricing_config()
    return {"packs": get_packs(), "price_per_min": cfg.get("pricePerMin", 150)}
'''
    t = t[:m.start()] + new_func + t[func_end:]
    print("✅ Endpoint now uses get_packs() — same as checkout")

if t != original:
    try:
        compile(t, "main.py", "exec")
        p.write_text(t, encoding="utf-8")
        print("💾 Saved (syntax OK)")
    except SyntaxError as e:
        print(f"❌ SYNTAX ERROR: {e} — NOT saved")
else:
    print("No changes made")