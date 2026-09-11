from pathlib import Path

p = Path("login.html")
t = p.read_text(encoding="utf-8")
changed = False

# 1) Point the token exchange at the REAL session endpoint
if 'fetch("/api/login"' in t:
    t = t.replace('fetch("/api/login"', 'fetch("/api/auth/session"')
    changed = True
    print("switched POST /api/login -> POST /api/auth/session")
elif "fetch('/api/login'" in t:
    t = t.replace("fetch('/api/login'", "fetch('/api/auth/session'")
    changed = True
    print("switched POST /api/login -> POST /api/auth/session (single quotes)")
else:
    print("WARNING: fetch(/api/login) not found - paste the finishLogin function")

# 2) Treat {"ok": false} as a failure too (the legacy route's silent refusal)
if "if(d.error){" in t and "d.ok === false" not in t:
    t = t.replace("if(d.error){", "if(d.error || d.ok === false){", 1)
    changed = True
    print("strengthened failure detection")

if changed:
    p.write_text(t, encoding="utf-8")
    print("login.html updated.")
else:
    print("NOTHING CHANGED - check warning above.")