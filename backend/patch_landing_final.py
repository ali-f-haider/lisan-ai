import re
from pathlib import Path

p = Path("landing.html")
if not p.exists():
    print("ERROR: landing.html not found in current directory.")
    exit(1)

t = p.read_text(encoding="utf-8")
orig = t

# 1. Fix top bar: Ensure Help/FAQ and Log in are standard <a> links, NOT buttons.
# If they were accidentally wrapped in <button> tags, unwrap them.
t = re.sub(r'<button[^>]*>\s*(<a[^>]*href="/help"[^>]*>.*?</a>)\s*</button>', r'\1', t, flags=re.S | re.I)
t = re.sub(r'<button[^>]*>\s*(<a[^>]*href="/login"[^>]*>.*?</a>)\s*</button>', r'\1', t, flags=re.S | re.I)

# 2. Remove "Start free" from the top bar if it exists
t = re.sub(r'<a[^>]*href="/login"[^>]*>\s*Start free\s*</a>', '', t, flags=re.I)

# 3. Remove "Watch the 60-second demo" button
t = re.sub(r'<a[^>]*href="#demo"[^>]*>.*?Watch the 60-second demo.*?</a>', '', t, flags=re.I | re.S)

# 4. Center the "Start Dubbing Free" button (wrap in flex container if not already)
if '🚀 Start Dubbing Free' in t and 'justify-content:center' not in t.split('🚀 Start Dubbing Free')[0][-100:]:
    t = re.sub(r'(<a[^>]*href="/login"[^>]*>🚀 Start Dubbing Free</a>)', 
               r'<div style="display:flex;justify-content:center;margin:24px 0;">\1</div>', t)

# 5. Update Pricing text (No subscriptions, refundable)
t = t.replace(
    "Packs from $4.99 — secure checkout by Stripe.",
    "Credit packs from $4.99 — one-time purchase, no subscription, credits never expire. Unused credits are refundable minus payment-processing and conversion fees (borne by the user). Secure checkout by Stripe."
)

# 6. Update Refund Policy text in the footer
old_refund = "Credits are digital goods and are non-refundable once used. If a purchase fails to deliver credits, or a processing error consumes credits without producing a result, contact us within 14 days and we will restore the credits or refund the equivalent amount."
new_refund = "Unused credits are refundable on request within 14 days of purchase: we refund the pack price minus Stripe processing and currency-conversion costs, which are borne by the user. Used credits are non-refundable, except when a processing error consumed credits without producing a result — in that case we restore the credits or refund the equivalent amount. Contact us to request a refund."
if old_refund in t:
    t = t.replace(old_refund, new_refund)

# 7. Link the footer text to the new pages
t = t.replace(">Terms of Service<", '><a href="/terms.html" style="color:inherit;text-decoration:underline;">Terms of Service</a><')
t = t.replace(">Privacy Policy<", '><a href="/privacy.html" style="color:inherit;text-decoration:underline;">Privacy Policy</a><')

if t == orig:
    print("WARNING: landing.html unchanged. The exact strings were not found.")
else:
    p.write_text(t, encoding="utf-8")
    print("✅ landing.html successfully patched.")