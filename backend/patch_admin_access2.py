import re
from pathlib import Path

mp = Path("main.py"); mt = mp.read_text(encoding="utf-8")

# 1) Add "/admin" INSIDE the PUBLIC_PATHS collection (block-scoped check this time)
m = re.search(r'PUBLIC_PATHS\s*=\s*frozenset\(\[(.*?)\]\)', mt, re.S) or \
    re.search(r'PUBLIC_PATHS\s*=\s*\[(.*?)\]', mt, re.S)
if m:
    block = m.group(1)
    if '"/admin"' not in block:
        mt = mt[:m.start(1)] + block.rstrip() + ', "/admin"' + mt[m.end(1):]
        print("added /admin to PUBLIC_PATHS")
    else:
        print("/admin already in PUBLIC_PATHS")
else:
    print("WARNING: PUBLIC_PATHS block not found - paste it to me")

# 2) Ensure middleware lets the self-protected admin API through
old_mw = 'if path in PUBLIC_PATHS or path.startswith("/api/auth/"):'
new_mw = 'if path in PUBLIC_PATHS or path.startswith("/api/auth/") or path.startswith("/api/admin/"):'
if old_mw in mt:
    mt = mt.replace(old_mw, new_mw, 1); print("middleware updated")
elif new_mw in mt:
    print("middleware already updated")
else:
    print("WARNING: middleware line not found - paste it to me")

mp.write_text(mt, encoding="utf-8")

m2 = re.search(r'PUBLIC_PATHS\s*=\s*frozenset\(\[.*?\]\)', mt, re.S) or re.search(r'PUBLIC_PATHS\s*=\s*\[.*?\]', mt, re.S)
if m2:
    print("PUBLIC_PATHS now contains /admin:", '"/admin"' in m2.group(0))
print("DONE")