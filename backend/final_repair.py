import re
from pathlib import Path

# 1. Fix main.py - Nuke the old summary route and add a diagnostic one
mp = Path("main.py")
mt = mp.read_text(encoding="utf-8")

# Find and remove any existing /api/account/summary route
pattern = re.compile(r'@app\.get\("/api/account/summary"\).*?(?=@app\.get|@app\.post|def \w+\(|$)', re.DOTALL)
mt_new, count = pattern.subn('', mt)
if count > 0:
    print(f"Removed {count} old summary route(s).")
else:
    print("No old summary route found to remove.")

# Add the new diagnostic route at the end of the file
DIAG_ROUTE = '''

@app.get("/api/account/summary")
def account_summary_diag(request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "login required"}, status_code=401)
        
    import urllib.request as _ur
    url = f"{SUPABASE_URL}/rest/v1/credit_spends?select=*&order=created_at.desc&limit=5"
    hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    
    raw_data = []
    err = None
    status_code = None
    try:
        req = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=10) as r:
            status_code = r.status
            raw_data = json.load(r)
    except Exception as e:
        err = str(e)
        
    return {
        "uid_used": str(uid),
        "supabase_status": status_code,
        "raw_count": len(raw_data) if isinstance(raw_data, list) else "not a list",
        "error": err,
        "first_row": raw_data[0] if raw_data else None,
        "credits": get_credits(uid) or 0
    }
'''
mt_new += DIAG_ROUTE
mp.write_text(mt_new, encoding="utf-8")
print("✅ Added diagnostic summary route.")

# 2. Fix eleven_service.py - Safe overlap fix
ep = Path("eleven_service.py")
et = ep.read_text(encoding="utf-8")

def fix_overlap(code, list_name):
    pattern = re.compile(r'^([ \t]*)allowed_end = ' + list_name + r'\[i \+ 1\]\["start"\] - 0\.005 if i \+ 1 < len\(' + list_name + r'\) else final_duration', re.MULTILINE)
    
    def replacer(match):
        indent = match.group(1)
        return (
            f'{indent}# PATCHED: global overlap check\n'
            f'{indent}allowed_end = final_duration\n'
            f'{indent}for _j in range(len({list_name})):\n'
            f'{indent}    if _j == i: continue\n'
            f'{indent}    _other_start = {list_name}[_j]["start"]\n'
            f'{indent}    if _other_start > item["start"] and _other_start - 0.005 < allowed_end:\n'
            f'{indent}        allowed_end = _other_start - 0.005'
        )
    
    new_code, count = pattern.subn(replacer, code)
    return new_code, count

et, c1 = fix_overlap(et, "generated_files")
et, c2 = fix_overlap(et, "items")
print(f"✅ Patched {c1} generated_files, {c2} items in eleven_service.py")
ep.write_text(et, encoding="utf-8")

print("\nDONE. Deploy this and visit /api/account/summary to see the exact Supabase error.")