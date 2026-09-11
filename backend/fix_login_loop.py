import re
from pathlib import Path

p = Path("login.html")
if not p.exists():
    print("ERROR: login.html not found.")
    exit(1)

t = p.read_text(encoding="utf-8")

new_script = '''<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<script>
var sb = null;
function client(){ if(!sb && window.__SUPABASE_URL) sb = supabase.createClient(window.__SUPABASE_URL, window.__SUPABASE_KEY || ""); return sb; }
function msg(t, kind){ var m = document.getElementById("formMsg"); if(!m) return; m.textContent = t || ""; m.className = "msg " + (kind || ""); m.style.display = t ? "block" : "none"; }

var _loginAttempted = false;
async function finishLogin(session){
  if (_loginAttempted) return; // BREAK THE INFINITE LOOP
  _loginAttempted = true;
  
  var token = session && session.access_token;
  if(!token) { msg("No access token found.", "err"); return false; }
  
  try{
    var r = await fetch("/api/login", { 
      method:"POST", 
      headers:{"Content-Type":"application/json"}, 
      body: JSON.stringify({ access_token: token }),
      credentials: 'include' // Force browser to handle cookies properly
    });
    
    if (!r.ok) {
      var errText = await r.text();
      msg("Server error (" + r.status + "): " + errText, "err");
      try { await client().auth.signOut(); } catch(e){}
      return false;
    }
    
    var d = await r.json();
    if(d.error){ 
      msg("Login failed: " + d.error, "err"); 
      try { await client().auth.signOut(); } catch(e){}
      return false; 
    }
    
    // Success! Redirect to app
    location.replace("/app"); 
    return true;
  }catch(e){ 
    msg("Network error: " + e.message, "err"); 
    return false;
  }
}

(function() {
  var c = client();
  if (c) {
    c.auth.onAuthStateChange(async (event, session) => {
      if ((event === "SIGNED_IN" || event === "INITIAL_SESSION" || event === "TOKEN_REFRESHED") && session) {
        await finishLogin(session);
      }
    });
  }
})();

async function doLogin(){
  var c = client(); if(!c){ msg("Login system unavailable.", "err"); return; }
  var res = await c.auth.signInWithPassword({ email: document.getElementById("email").value.trim(), password: document.getElementById("password").value });
  if(res.error){
    if(/confirm/i.test(res.error.message)){
      try{ await c.auth.resend({ type:"signup", email: document.getElementById("email").value.trim() }); }catch(e){}
      msg("Your email is not confirmed yet. We just re-sent the confirmation link — open it, then log in again.", "err");
    }
    else if(/invalid login credentials/i.test(res.error.message)){
      msg("Wrong password — or this account's email was never confirmed. Open your confirmation email, or use “Forgot password?” to reset, then log in.", "err");
    }
    else msg(res.error.message, "err");
    return;
  }
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
  if(res.data.session){ await finishLogin(res.data.session); return; }
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
  await finishLogin(res.data.session);
}

function socialLogin(p){
  var c = client(); if(!c){ msg("Login system unavailable.", "err"); return; }
  c.auth.signInWithOAuth({ provider: p, options: { redirectTo: location.origin + "/login" } });
}
</script>
'''

# Replace the entire script block safely
t = re.sub(r'<script src="https://cdn\.jsdelivr\.net/npm/@supabase/supabase-js@2"></script>.*?</script>\s*</body>', new_script + '\n</body>', t, flags=re.DOTALL)

p.write_text(t, encoding="utf-8")
print("✅ Replaced script block with loop-breaking OAuth handler.")