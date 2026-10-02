# Lisan AI - local test copy

Run the site on your own PC (http://localhost:8000), change things, and push to
Railway only when you are happy.

## Start
1. One-time installs: Python 3.12 (tick "Add python.exe to PATH") and ffmpeg
   (PowerShell: `winget install Gyan.FFmpeg`).
2. Double-click `backend\run_local.bat`.
   - First run: it builds `.env.local` from your `.env`, then asks you to check it and start again.
   - Second run: it installs the packages (10-20 min, once) and opens the site.
3. An orange strip at the bottom of every page says LOCAL TEST COPY. Admin: http://localhost:8000/admin
4. Edit code, save, the server restarts by itself. Close the window to stop.

## One-time Supabase step
Authentication > URL Configuration > Redirect URLs: add `http://localhost:8000/login`
(without it, Google sign-in and password-reset emails send you to the real site).

## What the local copy switches OFF (on purpose)
Emails, Cloudflare R2 backups, Sentry, Railway/usage monitors, the site gate, Google Analytics,
and any LIVE Stripe key. Payments work only with a Stripe TEST key (`sk_test_...`) in `.env.local`.

## What it still uses FOR REAL
- The live Supabase database: accounts, credits, pricing. Use your own test accounts only.
  Do not press Save in the admin Pricing/Settings while running locally: it changes live pricing.
- Real AI services (ElevenLabs/Inworld, Gemini, Alibaba lip-sync): every test costs real money.
  Test with short clips.

## Shipping to Railway
Commit and push as usual. `.env.local`, `.venv` and `.local_data` are git-ignored.
