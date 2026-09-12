# patch_save_btn_v2.py
# Corrected — matches the EXACT formatting in your admin.html (with semicolons).

from pathlib import Path

P = Path("admin.html")
if not P.exists():
    raise SystemExit("admin.html not found")

t = P.read_text(encoding="utf-8")
orig = t

# Your file has: <button class="btn" style="margin-top:12px;" onclick="addPack()">+ Add Pack</button>
# (note the semicolon after 12px)
OLD = '<button class="btn" style="margin-top:12px;" onclick="addPack()">+ Add Pack</button>'

NEW = (
    '<div style="margin-top:14px;display:flex;gap:10px;">'
    '<button class="btn" onclick="addPack()">+ Add Pack</button>'
    '<button class="btn" onclick="savePricing()" style="background:#2e7d32">💾 Save Packs</button>'
    '</div>'
)

if OLD in t:
    t = t.replace(OLD, NEW)
    print("OK: Replaced single button with two buttons (Add Pack + Save Packs).")
else:
    print("ERROR: Could not find the exact button string.")
    print("Looking for any addPack button...")
    if 'onclick="addPack()"' in t:
        # Fallback: find the button tag containing addPack() and replace the whole tag
        import re
        m = re.search(r'<button[^>]*onclick="addPack\(\)"[^>]*>.*?</button>', t)
        if m:
            print(f"  Found button at chars {m.start()}-{m.end()}:")
            print(f"  {m.group(0)}")
            t = t[:m.start()] + NEW + t[m.end():]
            print("OK: Replaced via regex fallback.")
        else:
            raise SystemExit("Could not find addPack button even with regex.")

# Also fix savePricing() to handle newly-added packs
OLD_LOGIC = "if (packs[i]) packs[i][f] = v;"
NEW_LOGIC = "if (!packs[i]) packs[i] = {name:\"\", credits:0, price_usd:0, bonus_pct:0, stripe_link:\"\"}; packs[i][f] = v;"
if OLD_LOGIC in t:
    t = t.replace(OLD_LOGIC, NEW_LOGIC)
    print("OK: Fixed savePricing() to handle newly-added packs.")
else:
    print("INFO: savePricing logic not patched (may already be fixed or not present).")

if t == orig:
    print("\nWARNING: No changes made.")
else:
    P.write_text(t, encoding="utf-8")
    print("\nDONE. admin.html updated.")