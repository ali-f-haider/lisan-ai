# fix_all_admin_bodies.py
# Fixes the body-reading bug in ALL admin POST endpoints (not just login).
# Qwen found the bug in admin_login; same bug exists in adjust_credits and save_pricing.

from pathlib import Path
import re

MP = Path("main.py")
if not MP.exists():
    raise SystemExit("main.py not found")

mt = MP.read_text(encoding="utf-8")

# Fix 1: admin_login
old_login = '''@app.post("/api/admin/login")
def admin_login(request: Request):
    """Admin login — verifies code matches APP_PASSWORD env var."""
    try:
        body = json.loads(request._body.decode("utf-8")) if hasattr(request, "_body") else {}
    except Exception:
        body = {}
    code = body.get("code", "") if isinstance(body, dict) else ""'''

new_login = '''@app.post("/api/admin/login")
async def admin_login(request: Request):
    """Admin login — verifies code matches APP_PASSWORD env var."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    code = body.get("code", "") if isinstance(body, dict) else ""'''

# Fix 2: admin_adjust_credits
old_adjust = '''@app.post("/api/admin/adjust_credits")
def admin_adjust_credits(request: Request):
    """Manually grant or deduct credits. Logged to audit."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = json.loads(request._body.decode("utf-8")) if hasattr(request, "_body") else {}
    except Exception:
        body = {}'''

new_adjust = '''@app.post("/api/admin/adjust_credits")
async def admin_adjust_credits(request: Request):
    """Manually grant or deduct credits. Logged to audit."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}'''

# Fix 3: admin_save_pricing
old_pricing = '''@app.post("/api/admin/pricing")
def admin_save_pricing(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = json.loads(request._body.decode("utf-8")) if hasattr(request, "_body") else {}
    except Exception:
        body = {}'''

new_pricing = '''@app.post("/api/admin/pricing")
async def admin_save_pricing(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}'''

# Apply
patches = [
    (old_login, new_login, "admin_login"),
    (old_adjust, new_adjust, "admin_adjust_credits"),
    (old_pricing, new_pricing, "admin_save_pricing"),
]

applied = 0
for old, new, name in patches:
    if old in mt:
        mt = mt.replace(old, new)
        print(f"OK: {name} fixed (async + await request.json())")
        applied += 1
    elif "async def " + name in mt or (name.replace("_", "") and f"async def {name}" in mt):
        print(f"SKIP: {name} already async")
    else:
        print(f"WARNING: {name} exact match not found — may need manual fix")

MP.write_text(mt, encoding="utf-8")
print(f"\n{applied}/3 routes fixed. Run: python -m py_compile main.py")