# repair.py — run:  python repair.py
import shutil, time
from pathlib import Path

MARKERS = ["STEP 5.5: VOLUME MATCH", "I18N v2", "V3: custom voices",
           "POLISH PASS", "No clean button", "SAFE PROGRESS MESSAGES"]

# 1) Find the COMPLETE app.js among the copies
cands = [Path("app.js"), Path("New files backup/app.js"), Path("../backend_Backup/app.js")]
best = None
for c in cands:
    if not c.exists():
        continue
    t = c.read_text(encoding="utf-8", errors="ignore")
    score = sum(1 for m in MARKERS if m in t)
    print(f"{c}: {score}/{len(MARKERS)} feature markers")
    if best is None or score > best[1]:
        best = (c, score, t)
if best is None or best[1] < len(MARKERS):
    raise SystemExit("No complete app.js found — the full version is missing everywhere.")
src, score, text = best
if src != Path("app.js"):
    shutil.copy(src, "app.js")
    print(f"Copied {src} -> app.js")

# 2) Regenerate modules (marker-tolerant) and VERIFY they equal app.js exactly
CHUNKS = [("01_core.js", None),
          ("02_progress.js", "// ===== SAFE PROGRESS MESSAGES"),
          ("03_reset.js", "// ===== ADD-ON: New-project reset"),
          ("04_billing.js", "// ===== ADD-ON: Buy credits modal"),
          ("05_volume.js", "// ===== STEP 5.5: VOLUME MATCH"),
          ("06_voices_i18n.js", "// ===== V3: custom voices")]
out = Path("js"); out.mkdir(exist_ok=True)
pos = []
for name, marker in CHUNKS:
    if marker is None:
        pos.append([name, 0])
    else:
        idx = text.find(marker)
        pos.append([name, idx if idx >= 0 else pos[-1][1]])
for i in range(1, len(pos)):
    if pos[i][1] < pos[i - 1][1]:
        pos[i][1] = pos[i - 1][1]
for i, (name, start) in enumerate(pos):
    end = pos[i + 1][1] if i + 1 < len(pos) else len(text)
    (out / name).write_text(text[start:end], encoding="utf-8")
concat = "".join((out / n).read_text(encoding="utf-8") for n, _ in CHUNKS)
if concat != text:
    raise SystemExit("Module concat mismatch — aborting.")
print("Modules regenerated and verified byte-identical to app.js.")

# 3) Fix index.html script tags (cache-busted)
V = str(int(time.time()))
tags = "\n".join(f'<script src="/js/{n}?v={V}"></script>' for n, _ in CHUNKS)
ip = Path("index.html")
h = ip.read_text(encoding="utf-8")
if "/js/01_core.js" in h and '<script src="/app.js"></script>' in h:
    h = h.replace('<script src="/app.js"></script>', "")
    print("Removed duplicate app.js tag from index.html.")
if "/js/01_core.js" not in h:
    if '<script src="/app.js"></script>' in h:
        h = h.replace('<script src="/app.js"></script>', tags)
    else:
        h = h.replace("</body>", tags + "\n</body>")
    ip.write_text(h, encoding="utf-8")
    print("index.html now loads the modules.")
else:
    print("index.html already loads modules.")

# 4) Append missing backend routes to main.py
mp = Path("main.py")
mt = mp.read_text(encoding="utf-8")
add = ""
if "/api/segment_audio/" not in mt:
    add += '''

@app.get("/api/segment_audio/{job_id}/{segment_id}")
def segment_audio(job_id: str, segment_id: str):
    if "/" in segment_id or ".." in segment_id:
        return JSONResponse({"error": "bad id"}, status_code=400)
    for ext, mt2 in ((".wav", "audio/wav"), (".mp3", "audio/mpeg")):
        p = OUTPUT_DIR / f"{segment_id}_stretched{ext}"
        if p.exists():
            fr = FileResponse(p, media_type=mt2)
            fr.headers["Cache-Control"] = "no-store"
            return fr
    return JSONResponse({"error": "not found"}, status_code=404)
'''
if "/api/cleanup_voices" not in mt:
    add += '''

@app.post("/api/cleanup_voices")
def cleanup_voices(payload: dict = {}):
    keep = payload.get("keep", []) or []
    return eleven_service.cleanup_cloned_voices(ELEVENLABS_API_KEY, keep)
'''
if "/api/upload_custom_voice" not in mt:
    add += '''

@app.post("/api/upload_custom_voice")
async def upload_custom_voice(request: Request, file: UploadFile = File(...), speaker: str = Form("Speaker 1"), job_id: str = Form("")):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "login required"}, status_code=401)
    nm = (file.filename or "").lower()
    if not nm.endswith((".mp3", ".wav")):
        return {"error": "Only MP3 or WAV files are allowed."}
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        return {"error": "File too large (max 10 MB / 20 seconds)."}
    import uuid as _u
    tmp = OUTPUT_DIR / f"custom_upload_{_u.uuid4().hex}.bin"
    tmp.write_bytes(data)
    try:
        res = eleven_service.add_custom_voice(job_id or "custom", speaker, tmp, ELEVENLABS_API_KEY)
    except Exception as e:
        res = f"ERROR: {e}"
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
    if isinstance(res, str) and res.startswith("ERROR"):
        return {"error": res}
    return {"status": "success", "voice_id": res}
'''
if "/api/account/summary" not in mt or '"\/account"' not in mt.replace("/account", "\/account"):
    add += '''

@app.get("/api/account/summary")
def account_summary(request: Request):
    uid = _current_uid(request)
    if not uid:
        return JSONResponse({"error": "login required"}, status_code=401)
    credits = get_credits(uid)

    def _rows(table):
        try:
            rq = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/{table}?uid=eq.{uid}&select=*&order=created_at.desc&limit=100",
                headers={"apikey": SUPABASE_SERVICE_KEY})
            with urllib.request.urlopen(rq, timeout=10) as r:
                return json.load(r)
        except Exception:
            return []
    return {"credits": credits if credits is not None else 100,
            "purchases": _rows("credit_orders"),
            "spends": _rows("credit_spends")}


@app.get("/account")
def account_page():
    return FileResponse(BASE_DIR / "account.html")
'''
if add:
    mp.write_text(mt + add, encoding="utf-8")
    print("Appended missing routes to main.py.")
else:
    print("main.py already has all routes.")

# 5) Create account.html if missing
ap = Path("account.html")
if not ap.exists():
    ap.write_text('''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lisan AI — My Usage</title>
<link rel="icon" type="image/png" href="/logo.png">
<link rel="stylesheet" href="/styles.css"></head>
<body>
<div id="userBar">
  <div style="display:flex;align-items:center;gap:10px;">
    <img src="/logo.png" style="height:30px;" alt="">
    <strong style="color:#1a237e;">Lisan AI — My Usage</strong></div>
  <div style="display:flex;gap:8px;align-items:center;">
    <span class="credits-badge" style="display:inline-block;">💰 <span id="creditsNum">0</span> credits</span>
    <button class="btn-logout" onclick="location.href='/app'">← Back to App</button>
    <button class="btn-logout" onclick="fetch('/api/logout',{method:'POST'}).then(function(){location.href='/login'})">Log Out</button>
  </div></div>
<div class="card"><h3>💰 Current balance</h3>
<p id="balanceLine" style="font-size:24px;font-weight:800;color:#059669;margin:6px 0;">—</p>
<p class="note">100 credits = $1.00 · Transcribe 3 · Generate ≈ characters/60 · Merge 1.</p></div>
<div class="card"><h3>🛒 Credit purchases</h3>
<table id="purchasesTable"><thead><tr><th>Date</th><th>Credits</th><th>Stripe session</th></tr></thead><tbody></tbody></table></div>
<div class="card"><h3>📉 Credit consumption</h3>
<table id="spendsTable"><thead><tr><th>Date</th><th>Action</th><th>Job</th><th>Credits</th></tr></thead><tbody></tbody></table></div>
<script>
fetch("/api/user/info").then(function(r){return r.json();}).then(function(d){
  document.getElementById("creditsNum").textContent = d.credits;
  document.getElementById("balanceLine").textContent = d.credits + " credits";
}).catch(function(){});
fetch("/api/account/summary").then(function(r){return r.json();}).then(function(d){
  var pt = document.querySelector("#purchasesTable tbody");
  (d.purchases || []).forEach(function(p){
    var tr = document.createElement("tr");
    tr.innerHTML = "<td>" + (p.created_at||"").slice(0,16).replace("T"," ") + "</td><td style='color:#059669;font-weight:700;'>+" + p.credits + "</td><td style='font-size:11px;color:#6b7280;'>" + (p.session_id||"").slice(0,24) + "…</td>";
    pt.appendChild(tr);});
  if (!(d.purchases || []).length) pt.innerHTML = '<tr><td colspan="3" style="color:#6b7280;">No purchases yet.</td></tr>';
  var st = document.querySelector("#spendsTable tbody");
  (d.spends || []).forEach(function(s){
    var tr = document.createElement("tr");
    tr.innerHTML = "<td>" + (s.created_at||"").slice(0,16).replace("T"," ") + "</td><td>" + (s.action||"deduction") + "</td><td style='font-size:11px;color:#6b7280;'>" + (s.job_id||"").slice(0,8) + "</td><td style='color:#dc2626;font-weight:700;'>−" + s.credits + "</td>";
    st.appendChild(tr);});
  if (!(d.spends || []).length) st.innerHTML = '<tr><td colspan="4" style="color:#6b7280;">No consumption recorded yet.</td></tr>';
}).catch(function(){});
</script></body></html>
''', encoding="utf-8")
    print("Created account.html.")
else:
    print("account.html already exists.")

print("\nDONE. Now run:")
print("  python -m py_compile main.py")
print("  git add app.js js index.html main.py account.html repair.py")
print('  git commit -m "Restore all features: sync app.js, regenerate modules, add missing routes"')
print("  git push")