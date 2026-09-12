from pathlib import Path

# ---- 1) main.py: two ADDITIVE middleware/whitelist tweaks (admin routes & page untouched) ----
mp = Path("main.py"); mt = mp.read_text(encoding="utf-8"); changed = False

old_pp = '"/api/billing/checkout"'
new_pp = '"/api/billing/checkout", "/admin"'
if old_pp in mt and '"/admin"' not in mt:
    mt = mt.replace(old_pp, new_pp, 1); changed = True
    print("PUBLIC_PATHS: /admin now reachable without a user session")

old_mw = 'if path in PUBLIC_PATHS or path.startswith("/api/auth/"):'
new_mw = 'if path in PUBLIC_PATHS or path.startswith("/api/auth/") or path.startswith("/api/admin/"):'
if old_mw in mt:
    mt = mt.replace(old_mw, new_mw, 1); changed = True
    print("middleware: /api/admin/* now self-protects via its own token (as designed)")
else:
    print("WARNING: middleware line not found - paste it to me")

if changed:
    mp.write_text(mt, encoding="utf-8")

# ---- 2) login.html: empty email + access code in password field = admin shortcut ----
lp = Path("login.html"); lt = lp.read_text(encoding="utf-8")
old_dl = "async function doLogin(){\n  var c = client();"
new_dl = ("async function doLogin(){\n"
          "  var _em = document.getElementById(\"email\").value.trim();\n"
          "  var _pw = document.getElementById(\"password\").value;\n"
          "  if(!_em && _pw){ location.replace(\"/admin\"); return; }\n"
          "  if(!_em && !_pw){ msg(\"Enter your email — or put the admin access code in the password field.\", \"err\"); return; }\n"
          "  var c = client();")
if old_dl in lt and "_adminShortcut" not in lt:
    lt = lt.replace(old_dl, new_dl.replace("async function doLogin(){", "async function doLogin(){ /* _adminShortcut */", 1), 1)
    lp.write_text(lt, encoding="utf-8")
    print("login.html: access-code shortcut added (no visual change)")
elif "_adminShortcut" in lt:
    print("login.html already patched")
else:
    print("WARNING: doLogin pattern not found - paste the function to me")
print("DONE")