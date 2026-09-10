# split_app.py — corrected markers matching actual app.js
from pathlib import Path

SRC = Path("app.js")
OUT = Path("js"); OUT.mkdir(exist_ok=True)

CHUNKS = [
    ("01_core.js",        None),
    ("02_progress.js",    "// ===== SAFE PROGRESS MESSAGES"),
    ("03_reset.js",       "// ===== ADD-ON: New-project reset"),
    ("04_billing.js",     "// ===== ADD-ON: Buy credits modal"),
    ("05_volume.js",      "// ===== STEP 5.5: VOLUME MATCH & PER-LINE MIX"),
    ("06_voices_i18n.js", "// ===== V3: custom voices"),
]

text = SRC.read_text(encoding="utf-8")
pos = []
for name, marker in CHUNKS:
    if marker is None:
        pos.append((name, 0))
    else:
        idx = text.find(marker)
        if idx == -1:
            raise SystemExit(f"MARKER NOT FOUND: {marker}")
        pos.append((name, idx))

for i in range(1, len(pos)):
    if pos[i][1] <= pos[i-1][1]:
        raise SystemExit(f"Marker out of order: {pos[i][0]}")

total = 0
for i, (name, start) in enumerate(pos):
    end = pos[i+1][1] if i+1 < len(pos) else len(text)
    chunk = text[start:end]
    (OUT / name).write_text(chunk, encoding="utf-8")
    total += len(chunk.splitlines())
    print(f"{name}: {len(chunk.splitlines())} lines")

src_lines = len(text.splitlines())
print(f"SOURCE: {src_lines} lines | SPLIT: {total} lines")
if total != src_lines:
    raise SystemExit("LINE COUNT MISMATCH")
print("OK")