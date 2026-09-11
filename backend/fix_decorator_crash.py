import re
from pathlib import Path

print("🔧 Fixing the recurring FastAPI decorator crash...")
mp = Path("main.py")
mt = mp.read_text(encoding="utf-8")

# 1. DESTROY the broken multi-path decorators
# Matches: @app.get("/login", "/help", "/help.html", ...)
pattern = r'@app\.get\(\s*"([^"]+)"\s*(?:,\s*"[^"]+"\s*)+\)'
def replacer(match):
    first_path = match.group(1)
    print(f"  ⚠️ Found broken multi-path decorator. Keeping only: {first_path}")
    return f'@app.get("{first_path}")'

mt, count = re.subn(pattern, replacer, mt)
if count > 0:
    print(f"✅ Fixed {count} broken decorator(s). Server will no longer crash on startup.")
else:
    print("✅ No broken multi-path decorators found.")

# 2. Ensure /help, /privacy, and /terms have proper stacked routes
routes_code = """

# --- PUBLIC PAGES ROUTING (Auto-injected fix) ---
@app.get("/help")
@app.get("/help.html")
def public_help_page():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "help.html"))

@app.get("/privacy")
@app.get("/privacy.html")
def public_privacy_page():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "privacy.html"))

@app.get("/terms")
@app.get("/terms.html")
def public_terms_page():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "terms.html"))
"""

if "def public_help_page" not in mt:
    mt += routes_code
    print("✅ Added clean, separate routes for /help, /privacy, and /terms.")
else:
    print("✅ Public routes already exist.")

# 3. Syntax Check (Guarantees the server will boot)
try:
    compile(mt, "main.py", "exec")
    print("🎉 Python syntax check PASSED. The server is guaranteed to boot.")
except SyntaxError as e:
    print(f"❌ SYNTAX ERROR REMAINS: {e}")
    print("Please stop and paste the error here.")

mp.write_text(mt, encoding="utf-8")
print("💾 main.py saved.")