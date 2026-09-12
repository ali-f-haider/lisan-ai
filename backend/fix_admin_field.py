from pathlib import Path
p = Path("admin.html"); t = p.read_text(encoding="utf-8")
if "JSON.stringify({ code })" in t:
    t = t.replace("JSON.stringify({ code })", "JSON.stringify({ password: code })")
    p.write_text(t, encoding="utf-8")
    print("✅ Fixed admin login field name (code -> password)")
else:
    print("Already fixed or pattern not found.")