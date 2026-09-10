# split_app.py — run from the backend folder:  python split_app.py
from pathlib import Path

SRC = Path("app.js")
OUT = Path("js")
OUT.mkdir(exist_ok=True)

MARKERS = [
    ("01_core.js",          None),
    ("02_progress.js",      "// ===== SAFE PROGRESS MESSAGES"),
    ("03_reset.js",         "// ===== ADD-ON: yellow original-start markers"),
    ("04_billing.js",       "// ===== ADD-ON: download protection, safe reset, loaded-project media guard"),
    ("05_polish_volume.js", "// ===== POLISH PASS: favicon, meta, subtitles, empty states, mobile wraps"),
    ("06_voices_i18n.js",   "// ===== V3: custom voices, usage button, volume-table fixes"),
    ("07_cleanup.js",       "// ===== No clean button: voice cleanup is automatic only"),
]

text = SRC.read_text(encoding="utf-8")
pos = []
for name, marker in MARKERS:
    if marker is None:
        pos.append((name, 0))
    else:
        idx = text.find(marker)
        if idx == -1:
            raise SystemExit(f"MARKER NOT FOUND: {marker}")
        pos.append((name, idx))

for i in range(1, len(pos)):
    if pos[i][1] <= pos[i - 1][1]:
        raise SystemExit(f"Marker out of order: {pos[i][0]}")

total = 0
for i, (name, start) in enumerate(pos):
    end = pos[i + 1][1] if i + 1 < len(pos) else len(text)
    chunk = text[start:end]
    (OUT / name).write_text(chunk, encoding="utf-8")
    total += len(chunk.splitlines())
    print(f"{name}: {len(chunk.splitlines())} lines")

print("SOURCE lines:", len(text.splitlines()), "| SPLIT lines:", total)
if total != len(text.splitlines()):
    raise SystemExit("LINE COUNT MISMATCH — nothing was lost? check markers")
print("OK — app.js untouched, modules written to js/")