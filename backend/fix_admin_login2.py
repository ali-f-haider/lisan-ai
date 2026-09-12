import re
from pathlib import Path

# --- 1) main.py: admin login via Pydantic model (the proven /api/login pattern) ---
mp = Path("main.py"); mt = mp.read_text(encoding="utf-8")
NEW = '''class AdminLoginRequest(BaseModel):
    code: str = ""
    password: str = ""

@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest):
    code = req.code or req.password
    if not code or not APP_PASSWORD:
        return JSONResponse({"error": "admin access disabled"}, status_code=403)
    if not hmac.compare_digest(str(code), str(APP_PASSWORD)):
        return JSONResponse({"error": "invalid code"}, status_code=401)
    token = secrets.token_urlsafe(32)
    _ADMIN_TOKENS[token] = _time.time()
    return {"token": token}'''
pat = re.compile(r'@app\.post\("/api/admin/login"\)\n(?:async )?def admin_login\(.*?(?=\n@app\.)', re.S)
if pat.search(mt):
    mt = pat.sub(lambda m: NEW, mt, count=1)
    mp.write_text(mt, encoding="utf-8")
    print("main.py: admin_login now uses Pydantic body parsing (accepts code OR password)")
else:
    print("WARNING: admin_login not found in main.py")

# --- 2) login.html: empty email + access code -> old /api/login flow -> /app ---
lp = Path("login.html"); lt = lp.read_text(encoding="utf-8")
BR = ('  var _em = document.getElementById("email").value.trim();\n'
      '  var _pw = document.getElementById("password").value;\n'
      '  if (!_em && _pw) {\n'
      '    try {\n'
      '      var r0 = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: _pw }) });\n'
      '      var d0 = await r0.json();\n'
      '      if (d0 && d0.ok) { location.replace("/app"); return; }\n'
      '      msg("That access code is not valid.", "err"); return;\n'
      '    } catch (e) { msg("Network error: " + e.message, "err"); return; }\n'
      '  }\n')
if 'fetch("/api/login"' in lt:
    print("login.html: access-code branch already present")
else:
    # remove my old /admin shortcut lines if present, then insert the working branch
    lt = re.sub(r'  var _em = document\.getElementById\("email"\)\.value\.trim\(\);\n  var _pw = [^\n]*\n  if\(!_em && _pw\)\{ location\.replace\("/admin"\); return; \}\n', '', lt)
    if "async function doLogin(){" in lt:
        lt = lt.replace("async function doLogin(){\n", "async function doLogin(){\n" + BR, 1)
        lp.write_text(lt, encoding="utf-8")
        print("login.html: password-only login restored (goes to /app like the old version)")
    else:
        print("WARNING: doLogin not found in login.html")
print("DONE")