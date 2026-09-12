import re
from pathlib import Path

p = Path("admin.html")
t = p.read_text(encoding="utf-8")

# 1) What name(s) do the buttons call?
onclick_names = set(re.findall(r'onclick="([A-Za-z_$][\w$]*)\s*\(', t))
print("onclick handlers found:", onclick_names or "none")

# 2) Find the JS function that posts to /api/admin/login
i = t.find("admin/login")
login_fn = None
if i != -1:
    before = t[:i]
    m = None
    for mm in re.finditer(r'(?:function\s+([A-Za-z_$][\w$]*)\s*\()|(?:(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=)', before):
        m = mm
    if m:
        login_fn = m.group(1) or m.group(2)
print("function that posts to /api/admin/login:", login_fn)

if not login_fn:
    print("\n!! Could not find the login JS. Context around admin/login:")
    print(t[max(0, i - 800): i + 300])
else:
    missing = []
    for n in onclick_names:
        if n == login_fn:
            continue
        if ("function " + n) not in t and ("function " + n + "(") not in t and (n + " =") not in t:
            missing.append(n)
    if not missing:
        print("Names already match — no fix needed.")
    else:
        lines = "".join(
            f"if (typeof window.{n} !== 'function' && typeof window.{login_fn} === 'function') window.{n} = window.{login_fn};\n"
            for n in missing
        )
        block = "<script>\n// button-handler alias\n" + lines + "</script>\n</body>"
        t = t.replace("</body>", block, 1)
        p.write_text(t, encoding="utf-8")
        print(f"Injected alias: {missing} -> {login_fn}")