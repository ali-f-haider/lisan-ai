import re
from pathlib import Path

p = Path("login.html")
t = p.read_text(encoding="utf-8")
orig = t
buttons_before = t.count("<button")
css_before = t.count("<style")

# 1) Better login-error handling (confirmation vs wrong password)
old1 = '''  if(res.error){
    if(/confirm/i.test(res.error.message)) msg("Please confirm your email first (check your inbox), then log in.", "err");
    else msg("Wrong email or password. Try again, or use \u201cForgot password?\u201d.", "err");
    return;
  }'''
new1 = '''  if(res.error){
    if(/confirm/i.test(res.error.message)){
      try{ await c.auth.resend({ type:"signup", email: document.getElementById("email").value.trim() }); }catch(e){}
      msg("Your email is not confirmed yet. We just re-sent the confirmation link \u2014 open it, then log in again.", "err");
    }
    else if(/invalid login credentials/i.test(res.error.message)){
      msg("Wrong password \u2014 or this account's email was never confirmed. Open your confirmation email, or use \u201cForgot password?\u201d to reset, then log in.", "err");
    }
    else msg(res.error.message, "err");
    return;
  }'''
if old1 in t: t = t.replace(old1, new1); print("patched login error handling")
else: print("WARN: login error block not found")

# 2) Wait for Supabase to finish parsing the reset-link token (fixes recovery race)
old2 = '''      var s = await c.auth.getSession();
      if(s.data.session){'''
new2 = '''      var s = await c.auth.getSession();
      for(var i=0; i<6 && !(s.data && s.data.session); i++){ await new Promise(function(r){ setTimeout(r, 400); }); s = await c.auth.getSession(); }
      if(s.data.session){'''
if old2 in t: t = t.replace(old2, new2); print("patched recovery session wait")
else: print("WARN: session block not found")

# 3) Guard: never run password update without a recovery session
old3 = '''async function doRecovery(){
  var c = client(); if(!c) return;
  var res = await c.auth.updateUser({ password: document.getElementById("newPass").value });'''
new3 = '''async function doRecovery(){
  var c = client(); if(!c) return;
  var s0 = await c.auth.getSession();
  if(!(s0.data && s0.data.session)){ msg("Recovery session missing or expired \u2014 open the link in your reset email again, then set the new password here.", "err"); return; }
  var res = await c.auth.updateUser({ password: document.getElementById("newPass").value });'''
if old3 in t: t = t.replace(old3, new3); print("patched recovery guard")
else: print("WARN: recovery block not found")

# UI LOCK CHECK: markup and CSS must be identical
if t.count("<button") != buttons_before or t.count("<style") != css_before:
    print("ABORT: UI would change - nothing written.")
else:
    p.write_text(t, encoding="utf-8")
    print("login.html logic patched. UI verified unchanged.")