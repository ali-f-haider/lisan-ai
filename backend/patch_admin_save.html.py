# patch_admin_save.html.py
# Adds a dedicated Save button to the Credit Packs card.
# Also fixes savePricing() so newly-added packs are saved correctly.

from pathlib import Path

P = Path("admin.html")
if not P.exists():
    raise SystemExit("admin.html not found")

t = P.read_text(encoding="utf-8")
orig = t

# Fix 1: Add a "Save Packs" button next to the "Add Pack" button
OLD_PACKS_BTN = '<button class="btn" style="margin-top:12px" onclick="addPack()">+ Add Pack</button>'
NEW_PACKS_BTN = (
    '<div style="margin-top:14px;display:flex;gap:10px;">'
    '<button class="btn" onclick="addPack()">+ Add Pack</button>'
    '<button class="btn" onclick="savePricing()" style="background:#2e7d32">💾 Save Packs</button>'
    '</div>'
)
if OLD_PACKS_BTN in t:
    t = t.replace(OLD_PACKS_BTN, NEW_PACKS_BTN)
    print("OK: Added 'Save Packs' button to Credit Packs card.")
else:
    print("INFO: Could not find the Add Pack button to replace.")

# Fix 2: Fix savePricing() to handle newly-added packs (the if (packs[i]) check was skipping them)
OLD_SAVE_LOGIC = (
    'document.querySelectorAll(\'#packsTable input\').forEach(inp => {\n'
    '    const i = parseInt(inp.dataset.idx);\n'
    '    const f = inp.dataset.field;\n'
    '    let v = inp.value;\n'
    '    if ([\'credits\',\'bonus_pct\'].includes(f)) v = parseInt(v) || 0;\n'
    '    if ([\'price_usd\'].includes(f)) v = parseFloat(v) || 0;\n'
    '    if (packs[i]) packs[i][f] = v;\n'
    '});'
)

NEW_SAVE_LOGIC = (
    'document.querySelectorAll(\'#packsTable input\').forEach(inp => {\n'
    '    const i = parseInt(inp.dataset.idx);\n'
    '    const f = inp.dataset.field;\n'
    '    let v = inp.value;\n'
    '    if ([\'credits\',\'bonus_pct\'].includes(f)) v = parseInt(v) || 0;\n'
    '    if ([\'price_usd\'].includes(f)) v = parseFloat(v) || 0;\n'
    '    if (!packs[i]) packs[i] = {name:"", credits:0, price_usd:0, bonus_pct:0, stripe_link:""};\n'
    '    packs[i][f] = v;\n'
    '});'
)

if OLD_SAVE_LOGIC in t:
    t = t.replace(OLD_SAVE_LOGIC, NEW_SAVE_LOGIC)
    print("OK: Fixed savePricing() to handle newly-added packs.")
else:
    print("INFO: Could not find the exact savePricing logic to patch.")
    print("      (May already be patched, or formatting differs.)")

# Fix 3: Clarify the existing "Save Pricing" button label
OLD_BTN_LABEL = '<button class="btn" onclick="savePricing()">💾 Save Pricing</button>'
NEW_BTN_LABEL = '<button class="btn" onclick="savePricing()">💾 Save Pricing Config</button>'
if OLD_BTN_LABEL in t:
    t = t.replace(OLD_BTN_LABEL, NEW_BTN_LABEL)
    print("OK: Renamed 'Save Pricing' → 'Save Pricing Config' for clarity.")

if t == orig:
    print("\nWARNING: No changes made. None of the patch targets matched.")
else:
    P.write_text(t, encoding="utf-8")
    print("\nDONE. admin.html updated.")
    print("Refresh /admin and you'll see the new Save Packs button.")