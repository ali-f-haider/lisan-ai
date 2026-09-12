# fix_save_pricing_v2.py
# Replaces the entire savePricing function with a robust version that:
# 1. Handles empty packs array (builds packs from input fields)
# 2. Shows a toast on every error
# 3. Reads current values fresh, doesn't trust cached state

from pathlib import Path
import re

P = Path("admin.html")
if not P.exists():
    raise SystemExit("admin.html not found")

t = P.read_text(encoding="utf-8")

# Find the entire savePricing function and replace it
# Pattern: from "async function savePricing() {" to the next "}" at column 0
pattern = re.compile(
    r'async function savePricing\(\)\s*\{[^}]*\}',
    re.DOTALL
)

NEW_FUNC = '''async function savePricing() {
  // Build packs fresh from the input fields (don't trust cached state)
  const packs = [];
  const rows = document.querySelectorAll('#packsTable tr');
  rows.forEach((tr) => {
    const inputs = tr.querySelectorAll('input');
    if (inputs.length < 5) return;
    const pack = {
      name: inputs[0].value || '',
      credits: parseInt(inputs[1].value) || 0,
      price_usd: parseFloat(inputs[2].value) || 0,
      bonus_pct: parseInt(inputs[3].value) || 0,
      stripe_link: inputs[4].value || ''
    };
    if (pack.name || pack.credits || pack.price_usd) {
      packs.push(pack);
    }
  });

  // Read the pricing config inputs
  const update = {
    pricePerMin: parseInt(document.getElementById('pricePerMin')?.value || '150'),
    freeCredits: parseInt(document.getElementById('freeCredits')?.value || '150'),
    minReserve: parseInt(document.getElementById('minReserve')?.value || '150'),
    maxVideoMin: parseInt(document.getElementById('maxVideoMin')?.value || '60'),
    markup: parseFloat(document.getElementById('markup')?.value || '4.0'),
    packs: packs
  };

  console.log('Saving pricing:', update);

  try {
    const res = await api('/api/admin/pricing', {
      method: 'POST',
      body: JSON.stringify(update)
    });
    console.log('Save response:', res);
    if (res.ok) {
      toast('Pricing saved (' + packs.length + ' packs)');
      // Reload to verify
      setTimeout(() => loadPricing(), 500);
    } else {
      toast('Failed: ' + (res.data.error || 'unknown error'), true);
    }
  } catch (e) {
    console.error('Save error:', e);
    toast('Error: ' + e.message, true);
  }
}'''

new_t, n = pattern.subn(NEW_FUNC, t)
if n == 0:
    print("ERROR: Could not find savePricing function to replace.")
    print("Check that admin.html contains 'async function savePricing() {'")
    raise SystemExit(1)

P.write_text(new_t, encoding="utf-8")
print(f"OK: Replaced savePricing function ({n} match).")
print("New version:")
print("- Builds packs fresh from inputs (handles empty DB)")
print("- Shows toast on every error")
print("- Logs to console for debugging")
print("- Reloads pricing after save to verify")