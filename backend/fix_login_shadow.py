import re
from pathlib import Path

mp = Path("main.py")
mt = mp.read_text(encoding="utf-8")
orig = mt

# Remove the contiguous inserted block: @app.get("/") + read_landing ... up to (but not including) the privacy route
pat = re.compile(
    r'@app\.get\("/"\)\s*\ndef read_landing\(\):.*?(?=@app\.get\("/privacy"\))',
    re.S)
if pat.search(mt):
    mt = pat.sub('', mt, count=1)
    print("Removed shadowing block (/, /login, /help). Original routes restored.")
else:
    # Fallback: remove each inserted function together with the decorator(s) directly above it,
    # processing in reverse order so decorators don't re-attach to the wrong function.
    for fname in ("read_help", "read_login", "read_landing"):
        p2 = re.compile(
            r'(?:@app\.get\("[^"]*"\)[ \t]*\n)+def ' + fname + r'\([^)]*\):[^\n]*\n(?:[ \t]+[^\n]*\n|\n)*',
            re.M)
        mt, n = p2.subn('', mt, count=1)
        if n:
            print(f"Removed inserted function: {fname}")

mp.write_text(mt, encoding="utf-8")

# Verification
login_routes = len(re.findall(r'@app\.get\("/login"\)', mt))
has_inject = "__SUPABASE_URL" in mt
privacy_ok = '@app.get("/privacy")' in mt
terms_ok = '@app.get("/terms")' in mt
print(f"login routes now: {login_routes} (must be 1)")
print(f"supabase injection present: {has_inject} (must be True)")
print(f"privacy route kept: {privacy_ok} | terms route kept: {terms_ok}")
if mt == orig:
    print("WARNING: nothing changed - paste me the first 40 lines of main.py")
else:
    print("main.py fixed.")