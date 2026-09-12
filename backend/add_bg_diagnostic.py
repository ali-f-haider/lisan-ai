# add_bg_diagnostic.py
# Adds a read-only diagnostic endpoint to main.py.
# Does NOT modify any existing code. Only appends a new route.
# Safe to deploy — cannot break anything.

from pathlib import Path

MP = Path("main.py")
mt = MP.read_text(encoding="utf-8")

ROUTE_MARKER = '@app.get("/api/admin/diag_bg/{job_id}")'
if ROUTE_MARKER in mt:
    print("Diagnostic route already exists — skipping.")
    raise SystemExit(0)

DIAG_ROUTE = '''

# TEMPORARY DIAGNOSTIC — background audio lookup
@app.get("/api/admin/diag_bg/{job_id}")
async def diag_bg(job_id: str, request: Request):
    """Diagnostic: list separated folder contents for a job."""
    if not _admin_check(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    
    import os
    from media_paths import job_background_audio, UPLOAD_DIR
    
    result = {
        "job_id": job_id,
        "upload_dir": str(UPLOAD_DIR),
        "separated_folder_exists": False,
        "separated_files": [],
        "job_background_audio_result": None,
        "glob_results": {},
        "alternative_files": {},
    }
    
    # Check if the separated folder exists
    sep_folder = UPLOAD_DIR / f"{job_id}_separated"
    result["separated_folder_exists"] = sep_folder.exists()
    
    # List all files in the separated folder
    if sep_folder.exists():
        for root, dirs, files in os.walk(sep_folder):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, sep_folder)
                try:
                    size = os.path.getsize(full)
                except Exception:
                    size = -1
                result["separated_files"].append({"path": rel, "size": size})
    
    # Check what job_background_audio returns
    bg = job_background_audio(job_id)
    result["job_background_audio_result"] = str(bg) if bg else None
    
    # Check each glob pattern individually
    p1 = sorted(UPLOAD_DIR.glob(f"{job_id}_separated/**/no_vocals.wav"))
    result["glob_results"]["pattern_1_no_vocals"] = [str(p) for p in p1]
    
    # Look for alternative names Demucs might produce
    alt_names = [
        "no_vocals.wav", "accompaniment.wav", "other.wav",
        "background.wav", "bg.wav", "instrumental.wav",
        "vocals.wav", "drums.wav", "bass.wav"
    ]
    for name in alt_names:
        found = sorted(UPLOAD_DIR.glob(f"{job_id}_separated/**/{name}"))
        if found:
            result["alternative_files"][name] = [str(p) for p in found]
    
    return result

'''

mt += DIAG_ROUTE
MP.write_text(mt, encoding="utf-8")
print("OK: Diagnostic endpoint added at /api/admin/diag_bg/{job_id}")
print("This is READ-ONLY — it cannot break anything.")