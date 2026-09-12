from pathlib import Path

p = Path("main.py")
t = p.read_text(encoding="utf-8")
changed = False

# 1. Whitelist admin paths in the middleware
if '"/admin"' not in t and 'PUBLIC_PATHS = frozenset' in t:
    t = t.replace('"/api/billing/checkout"', '"/api/billing/checkout", "/admin", "/api/admin/login", "/api/admin/overview", "/api/admin/users", "/api/admin/pricing", "/api/admin/audit", "/api/admin/health"')
    changed = True
    print("✅ 1. Whitelisted admin paths")

# 2. Append admin routes to the end of the file
if "def admin_login" not in t:
    admin_code = '''

import hmac
import time as _admin_time
import secrets as _admin_secrets

_ADMIN_TOKENS = {}

class AdminLoginRequest(BaseModel):
    code: str = ""
    password: str = ""

@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest):
    code = req.code or req.password
    if not code or not APP_PASSWORD:
        return JSONResponse({"error": "admin access disabled"}, status_code=403)
    if not hmac.compare_digest(str(code), str(APP_PASSWORD)):
        return JSONResponse({"error": "invalid code"}, status_code=401)
    token = _admin_secrets.token_urlsafe(32)
    _ADMIN_TOKENS[token] = _admin_time.time()
    return {"token": token}

def _admin_check(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token or token not in _ADMIN_TOKENS:
        return False
    if _admin_time.time() - _ADMIN_TOKENS[token] > 14400:
        del _ADMIN_TOKENS[token]
        return False
    return True

@app.get("/api/admin/overview")
def admin_overview(request: Request):
    if not _admin_check(request): return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"status": "ok", "message": "Admin routes active"}

@app.get("/api/admin/users")
def admin_users(request: Request):
    if not _admin_check(request): return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"users": []}

@app.get("/api/admin/pricing")
def admin_pricing(request: Request):
    if not _admin_check(request): return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"pricePerMin": 150, "freeCredits": 150, "packs": []}

@app.get("/api/admin/audit")
def admin_audit(request: Request):
    if not _admin_check(request): return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"logs": []}

@app.get("/api/admin/health")
def admin_health(request: Request):
    if not _admin_check(request): return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"status": "healthy"}

@app.get("/admin")
def admin_page():
    return FileResponse(BASE_DIR / "admin.html")
'''
    t += admin_code
    changed = True
    print("✅ 2. Appended admin routes")

if changed:
    p.write_text(t, encoding="utf-8")
    print("💾 Saved main.py")
else:
    print("⚠️ No changes needed")