import re
from pathlib import Path

mp = Path("main.py")
mt = mp.read_text(encoding="utf-8")

# Replace the diagnostic route with the production route
pattern = re.compile(r'@app\.get\("/api/account/summary"\).*?(?=@app\.get|@app\.post|def \w+\(|$)', re.DOTALL)

PROD_ROUTE = '''
@app.get("/api/account/summary")
def account_summary(request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "login required"}, status_code=401)
        
    import urllib.request as _ur
    
    def _fetch_and_filter(table):
        url = f"{SUPABASE_URL}/rest/v1/{table}?select=*&order=created_at.desc&limit=500"
        hdrs = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        try:
            req = _ur.Request(url, headers=hdrs)
            with _ur.urlopen(req, timeout=10) as r:
                all_rows = json.load(r)
            if isinstance(all_rows, list):
                return [x for x in all_rows if str(x.get("uid")) == str(uid)]
        except Exception as e:
            print(f"[account_summary] Error fetching {table}: {e}")
        return []

    spends = _fetch_and_filter("credit_spends")
    orders = _fetch_and_filter("credit_orders")
        
    return {
        "credits": get_credits(uid) or 0,
        "purchases": orders,
        "spends": spends
    }

'''

mt_new, count = pattern.subn(PROD_ROUTE, mt)
if count > 0:
    print(f"Replaced diagnostic route with production route.")
else:
    print("WARNING: Route not found to replace.")

mp.write_text(mt_new, encoding="utf-8")
print("✅ main.py updated. Usage page will now show all rows.")