import re
from pathlib import Path

p = Path("login.html")
t = p.read_text(encoding="utf-8")

new_script = '''<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<script>
var sb = null;
function client(){ if(!sb && window.__SUPABASE_URL) sb = supabase.createClient(window.__SUPABASE_URL, window.__SUPABASE_KEY || ""); return sb; }
function msg(t, kind){ var m = document.getElementById("formMsg"); if(!m) return; m.textContent = t || ""; m.className = "msg " + (kind || ""); m.style.display = t ? "block" : "none"; }
function cleanUrl(){ try{ history.replaceState({}, document.title, location.pathname); }catch(e){} }

var _loginAttempted = false;
async function finishLogin(session){
  if (_loginAttempted) return;
  _loginAttempted = true;
  var token = session && session.access_token;
  if(!token){ msg("No access token found.", "err"); return false; }
  try{
    var r = await fetch("/api/login", { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ access_token: token }), credentials: 'include' });
    if(!r.ok){ var et = await r.text(); msg("Server error (" + r.status + "): " + et, "err"); try{ await client().auth.signOut(); }catch(e){} return false; }
    var d = await r.json();
    if(d.error){ msg("Login failed: " + d.error, "err"); try{ await client().auth.signOut(); }catch(e){} return false; }
    location.replace("/app");
    return true;
  }catch(e){ msg("Network error: " + e.message, "err"); return false; }
}

function isOAuthReturn(){ return /[?&]code=/.test(location.search) || /access_token=/.test(location.hash); }

// CALM START: do nothing on a normal visit. Only react to a real Google/OAuth callback.
window.addEventListener("DOMContentLoaded", async function(){
  if(!isOAuthReturn()) return;
  var c = client(); if(!c){ msg("Login system unavailable.", "err"); return; }
  var s = await c.auth.getSession();
  for(var i=0; i<6 && !(s.data && s.data.session); i++){ await new Promise(function(r){ setTimeout(r,400); }); s = await c.auth.getSession(); }
  cleanUrl();
  if(s.data && s.data.session){ await finishLogin(s.data.session); }
  else { msg("Could not complete social login. Please try again.", "err"); try{ await c.auth.signOut(); }catch(e){} }
});

async function doLogin(){
  var c = client(); if(!c){ msg("Login system unavailable.", "err"); return; }
  var res = await c.auth.signInWithPassword({ email: document.getElementById("email").value.trim(), password: document.getElementById("password").value });
  if(res.error){
    if(/confirm/i.test(res.error.message)){ msg("Your email is not confirmed yet. Check your inbox for the confirmation link, then log in.", "err"); }
    else if(/invalid login credentials/i.test(res.error.message)){ msg("Wrong password — or this account's email was never confirmed. Use “Forgot password?” to reset.", "err"); }
    else msg(res.error.message, "err");
    return;
  }
  _loginAttempted = false;
  await finishLogin(res.data.session);
}
async function doSignup(){
  var c = client(); if(!c){ msg("Login system unavailable.", "err"); return; }
  var res = await c.auth.signUp({ email: document.getElementById("email").value.trim(), password: document.getElementById("password").value });
  if(res.error){
    if(/already registered|already exists/i.test(res.error.message)){ msg("This email is already registered. Please log in instead.", "err"); return; }
    msg(res.error.message, "err"); return;
  }
  if(res.data.user && (!res.data.user.identities || res.data.user.identities.length === 0)){ msg("This email is already registered. Please log in instead.", "err"); return; }
  if(res.data.session){ _loginAttempted = false; await finishLogin(res.data.session); return; }
  msg("Account created! Check your email to confirm, then log in.", "ok");
}
async function doForgot(){
  var c = client(); if(!c){ msg("Login system unavailable.", "err"); return; }
  var email = document.getElementById("email").value.trim();
  if(!email){ msg("Type your email in the email field first.", "err"); return; }
  var res = await c.auth.resetPasswordForEmail(email, { redirectTo: location.origin + "/login" });
  if(res.error) msg(res.error.message, "err");
  else msg("Password-reset email sent. Open it, set a new password, then log in.", "ok");
}
async function doRecovery(){
  var c = client(); if(!c) return;
  var s0 = await c.auth.getSession();
  if(!(s0.data && s0.data.session)){ msg("Recovery session missing or expired — open the link in your reset email again.", "err"); return; }
  var res = await c.auth.updateUser({ password: document.getElementById("newPass").value });
  if(res.error){ msg(res.error.message, "err"); return; }
  msg("Password updated. Logging you in…", "ok");
  _loginAttempted = false;
  await finishLogin(res.data.session);
}
function socialLogin(p){
  var c = client(); if(!c){ msg("Login system unavailable.", "err"); return; }
  c.auth.signInWithOAuth({ provider: p, options: { redirectTo: location.origin + "/login" } });
}
</script>
'''

t = re.sub(r'<script src="https://cdn\.jsdelivr\.net/npm/@supabase/supabase-js@2"></script>.*?</script>\s*</body>', new_script + '\n</body>', t, flags=re.DOTALL)
# safety: remove Facebook button if any leftover
t = re.sub(r'<button class="social" id="fbBtn"[^>]*>.*?</button>\s*', '', t, flags=re.DOTALL)

p.write_text(t, encoding="utf-8")
print("✅ Login page is now calm: no auto-redirect on plain load.")