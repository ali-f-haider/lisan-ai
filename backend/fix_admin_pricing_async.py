# fix_admin_pricing_async.py
# Fixes the admin_save_pricing route — it's `def` (sync) but uses `await` (illegal).
# Makes it `async def` so the await works.

from pathlib import Path

MP = Path("main.py")
mt = MP.read_text(encoding="utf-8")

OLD = '''@app.post("/api/admin/pricing")
def admin_save_pricing(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ok = _save_pricing_config(body)
    return {"ok": ok}'''

NEW = '''@app.post("/api/admin/pricing")
async def admin_save_pricing(request: Request):
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ok = _save_pricing_config(body)
    if not ok:
        return JSONResponse({"error": "Failed to save pricing — check server logs"}, status_code=500)
    return {"ok": True, "saved_at": int(__import__("time").time())}'''

if OLD in mt:
    mt = mt.replace(OLD, NEW)
    MP.write_text(mt, encoding="utf-8")
    print("OK: admin_save_pricing is now async + returns better error info.")
else:
    print("ERROR: exact match not found. May need manual fix.")
    print("Search for: def admin_save_pricing")