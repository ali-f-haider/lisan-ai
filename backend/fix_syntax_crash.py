import re
from pathlib import Path

print("🔍 Deep scanning main.py to fix the recurring deploy crash...")

mp = Path("main.py")
if not mp.exists():
    print("ERROR: main.py not found.")
    exit(1)

mt = mp.read_text(encoding="utf-8")
orig = mt

# 1. REVERT THE BAD INJECTION
# Strip the paths out if they appear after a keyword argument like status_code=200
bad_paths_str = ', "/help", "/help.html", "/privacy", "/privacy.html", "/terms", "/terms.html"'
mt = mt.replace('status_code=200' + bad_paths_str + ')', 'status_code=200)')
mt = mt.replace('status_code=302' + bad_paths_str + ')', 'status_code=302)')
mt = mt.replace('status_code=307' + bad_paths_str + ')', 'status_code=307)')

# Regex fallback to catch any variation of keyword argument followed by the injected paths
# Matches: `some_kwarg=123, "/help", "/help.html", ... )`
mt = re.sub(r'(\w+\s*=\s*[^,\)]+)(?:,\s*"/(?:help|privacy|terms)(?:\.html)?")+\s*\)', r'\1)', mt)
mt = re.sub(r'(\w+\s*=\s*[^,\}]+)(?:,\s*"/(?:help|privacy|terms)(?:\.html)?")+\s*\}', r'\1}', mt)

print("✅ Reverted accidental injections in function calls.")

# 2. CORRECTLY UPDATE THE WHITELIST
# Only inject into actual Python lists `[...]` that contain "/login"
def safe_list_inject(m):
    s = m.group(0)
    paths_to_add = ["/help", "/help.html", "/privacy", "/privacy.html", "/terms", "/terms.html"]
    for p in paths_to_add:
        if f'"{p}"' not in s and f"'{p}'" not in s:
            s = s[:-1] + f', "{p}"' + s[-1]
    return s

mt = re.sub(r'\[[^\[\]]*"/login"[^\[\]]*\]', safe_list_inject, mt)

# Safely inject into tuples that are assignments `= ( ... )` or checks `in ( ... )`
def safe_tuple_inject(m):
    s = m.group(0)
    paths_to_add = ["/help", "/help.html", "/privacy", "/privacy.html", "/terms", "/terms.html"]
    for p in paths_to_add:
        if f'"{p}"' not in s and f"'{p}'" not in s:
            s = s[:-1] + f', "{p}"' + s[-1]
    return s

mt = re.sub(r'(?<=[\=\s])\([^\(\)]*"/login"[^\(\)]*\)', safe_tuple_inject, mt)
mt = re.sub(r'(?<=in\s)\([^\(\)]*"/login"[^\(\)]*\)', safe_tuple_inject, mt)

print("✅ Safely updated public path whitelists.")

# 3. VERIFY NO SYNTAX ERRORS REMAIN
try:
    compile(mt, "main.py", "exec")
    print("🎉 Syntax check PASSED. The server is guaranteed to boot.")
except SyntaxError as e:
    print(f"❌ SYNTAX ERROR STILL EXISTS: {e}")
    print("Please paste the exact line number and code from the error above.")
    exit(1)

if mt == orig:
    print("⚠️ No changes were needed.")
else:
    mp.write_text(mt, encoding="utf-8")
    print("💾 main.py saved successfully.")