"""One-time helper for the local test copy of Lisan AI.

Builds .env.local (the ONLY settings file run_local.bat uses) from your existing
.env, leaving out everything that could touch real customers or production:
Stripe, Resend (email), Cloudflare R2 (backups), Railway, Sentry, the site gate.

It prints the NAMES of the settings it copies, never their values.
Safe to run again: it never overwrites an existing .env.local.
"""
import pathlib
import sys

here = pathlib.Path(__file__).resolve().parent
src = here / ".env"
dst = here / ".env.local"

# Settings that must NOT be copied from production into the local copy.
SKIP_PREFIXES = ("STRIPE_", "RESEND_", "R2_", "RAILWAY_", "SENTRY", "SITE_GATE", "SITE_URL",
                 "DATA_DIR", "LOCAL_DEV", "ALLOW_LOCAL_EMAIL", "CONTACT_TO_EMAIL", "WHISPER_MODEL")

if dst.exists():
    print(".env.local already exists - nothing changed.")
    sys.exit(0)

kept, skipped = [], []
if src.exists():
    for raw in src.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key.upper().startswith(SKIP_PREFIXES):
            skipped.append(key)
        else:
            kept.append(line)
else:
    print("No .env found next to this file - creating .env.local from the template instead.")
    example = here / "env_local_example.txt"
    if example.exists():
        dst.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        print("Created .env.local. Open it and fill in your keys (copy them from the Railway Variables tab).")
        sys.exit(0)
    print("The template env_local_example.txt is missing too. Nothing created.")
    sys.exit(1)

footer = """
# ---- local-only settings (added by setup_local.py) ----
# Payments: paste a Stripe TEST key (Stripe dashboard > Test mode > Developers > API keys).
# Live keys (sk_live...) are ignored on purpose. Leave empty to test without payments.
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
# A smaller speech model keeps tests fast on a PC without a graphics card.
# Use large-v3 only when you want to test real transcription quality.
WHISPER_MODEL=small
# Emails stay OFF locally. To test emails once, add: ALLOW_LOCAL_EMAIL=1 and RESEND_API_KEY=...
"""
dst.write_text("# Local test settings - used ONLY by run_local.bat. Never commit this file.\n"
               + "\n".join(kept) + "\n" + footer, encoding="utf-8")
print(f"Created .env.local with {len(kept)} settings:")
print("  copied : " + (", ".join(k.split('=')[0] for k in kept) or "(none)"))
print("  skipped: " + (", ".join(skipped) or "(none)") + "   <- left out on purpose")
