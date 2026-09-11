import re
from pathlib import Path

mp = Path("main.py"); mt = mp.read_text(encoding="utf-8")

ROUTES = '''@app.get("/privacy")
@app.get("/privacy.html")
def privacy_page():
    return FileResponse(BASE_DIR / "privacy.html")


@app.get("/terms")
@app.get("/terms.html")
def terms_page():
    return FileResponse(BASE_DIR / "terms.html")


'''
if '"/privacy"' not in mt:
    i = mt.find('@app.get("/help")')
    if i != -1:
        mt = mt[:i] + ROUTES + mt[i:]
        print("public privacy/terms routes inserted next to /help")
    else:
        mt += "\n" + ROUTES
        print("WARNING: /help route not found - routes appended at end")
    # whitelist in any public-path list/tuple that contains "/help"
    for pat in (r'(\[[^\]\n]*?"/help"[^\]\n]*?\])', r'(\([^\)\n]*?"/help"[^\)\n]*?\))'):
        m = re.search(pat, mt)
        if m:
            mt = mt[:m.end(1) - 1] + ', "/privacy", "/privacy.html", "/terms", "/terms.html"' + mt[m.end(1) - 1:]
            print("whitelisted in public-path list")
            break
    mp.write_text(mt, encoding="utf-8")
else:
    print("privacy/terms routes already present")

# create the pages if they don't exist yet
PRIV = Path("privacy.html"); TERM = Path("terms.html")
if not PRIV.exists():
    PRIV.write_text('''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Privacy Policy - Lisan AI</title><style>body{font-family:system-ui,sans-serif;max-width:800px;margin:40px auto;padding:20px;line-height:1.6;color:#1f2937}h1{color:#1a237e}a{color:#2563eb}</style></head><body><a href="/">&larr; Back to Home</a><h1>Privacy Policy</h1><p>We store only your account email, credit balance and purchase history (Supabase + Stripe). Uploaded media and generated files are processed temporarily and automatically deleted after ~6 hours or a server restart. We never see or store card details. We do not sell or share your content, and we do not use it to train models. Contact: ali_f_haider@hotmail.com</p></body></html>''', encoding="utf-8")
    print("privacy.html created")
if not TERM.exists():
    TERM.write_text('''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Terms of Service - Lisan AI</title><style>body{font-family:system-ui,sans-serif;max-width:800px;margin:40px auto;padding:20px;line-height:1.6;color:#1f2937}h1{color:#1a237e}a{color:#2563eb}</style></head><body><a href="/">&larr; Back to Home</a><h1>Terms of Service</h1><p>Lisan AI provides automated dubbing tools "as is". You are responsible for having the rights to any content you upload and to any voices you clone; cloning without the speaker's consent is prohibited and may lead to account suspension. Output quality depends on input audio; lip-sync is not offered. Credits: 100 = $1.00; unused credits refundable within 14 days minus Stripe processing/conversion fees (borne by the user); used credits non-refundable except verified processing errors. Media files are temporary and deleted automatically - download your results during your session.</p></body></html>''', encoding="utf-8")
    print("terms.html created")
print("DONE")