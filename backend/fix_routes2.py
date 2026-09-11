import re
from pathlib import Path
mp = Path("main.py"); mt = mp.read_text(encoding="utf-8")
ALL = [("/help","help.html","public_help"), ("/help.html","help.html","public_help_html"),
       ("/privacy","privacy.html","public_privacy"), ("/privacy.html","privacy.html","public_privacy_html"),
       ("/terms","terms.html","public_terms"), ("/terms.html","terms.html","public_terms_html")]
need = [x for x in ALL if ('@app.get("%s")' % x[0]) not in mt]
if need:
    block = ""
    for path, fn, name in need:
        block += '@app.get("%s")\ndef %s():\n    from fastapi.responses import FileResponse\n    import os\n    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "%s"))\n\n' % (path, name, fn)
    idx = mt.find("app = FastAPI(")
    if idx != -1:
        eol = mt.find("\n", idx)
        mt = mt[:eol+1] + "\n" + block + mt[eol+1:]
    else:
        mt = block + mt
    print("added missing routes:", [x[0] for x in need])
else:
    print("all public routes already present")
# whitelist them in any public-path list that contains "/login"
def wl(m):
    s = m.group(0)
    add = [x[0] for x in ALL if ('"%s"' % x[0]) not in s]
    return s[:-1] + "".join(', "%s"' % p for p in add) + s[-1:] if add else s
mt2 = re.sub(r'[\[\(][^\]\)]*"/login"[^\]\)]*[\]\)]', wl, mt)
if mt2 != mt:
    mt = mt2; print("whitelist updated")
if re.search(r'@app\.get\([^)]*"[^"]+"\s*,\s*"', mt):
    print("WARNING: a multi-path @app.get still exists - paste me that line")
mp.write_text(mt, encoding="utf-8")
print("DONE")