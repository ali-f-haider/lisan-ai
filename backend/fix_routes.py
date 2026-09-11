from pathlib import Path
import re

p = Path("main.py")
t = p.read_text(encoding="utf-8")

# 1. Try Regex match for the broken multi-path decorator
bad_dec_regex = re.compile(r'^[ \t]*@app\.get\([^)]*"/help"[^)]*"/privacy"[^)]*\)[ \t]*\n?', re.MULTILINE)

if bad_dec_regex.search(t):
    replacement = '''@app.get("/privacy")
@app.get("/privacy.html")
def privacy_page():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "privacy.html"))

@app.get("/terms")
@app.get("/terms.html")
def terms_page():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "terms.html"))

@app.get("/help")
@app.get("/help.html")
'''
    t = bad_dec_regex.sub(replacement, t)
    p.write_text(t, encoding="utf-8")
    print("✅ Fixed FastAPI decorator crash and separated privacy/terms routes.")

# 2. Fallback exact string match
elif '@app.get("/help", "/privacy", "/privacy.html", "/terms", "/terms.html")' in t:
    replacement = '''@app.get("/privacy")
@app.get("/privacy.html")
def privacy_page():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "privacy.html"))

@app.get("/terms")
@app.get("/terms.html")
def terms_page():
    from fastapi.responses import FileResponse
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "terms.html"))

@app.get("/help")'''
    t = t.replace('@app.get("/help", "/privacy", "/privacy.html", "/terms", "/terms.html")', replacement)
    p.write_text(t, encoding="utf-8")
    print("✅ Fixed FastAPI decorator crash (exact match).")
else:
    print("⚠️ Could not find the broken line automatically.")
    print("Please open main.py, go to line 641, and manually change:")
    print('   @app.get("/help", "/privacy", "/privacy.html", "/terms", "/terms.html")')
    print("   to stacked decorators:")
    print('   @app.get("/help")')
    print('   @app.get("/privacy")')
    # ... etc