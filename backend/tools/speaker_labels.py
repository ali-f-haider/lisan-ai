"""Create a small CSV labelling sheet and convert completed names to reference JSON."""
import argparse
import csv
import json
from pathlib import Path

try:
    from .speaker_eval import document, rows_fingerprint
except ImportError:
    from speaker_eval import document, rows_fingerprint

FIELDS = ("line_number", "segment_id", "start", "end", "text", "detected_speaker", "speaker", "notes", "source_fingerprint")


def make_sheet(export, path, *, limit=30, start=1):
    rows = document(export).get("rows", [])
    if not rows:
        raise ValueError("The export must contain editor lines.")
    ids = [row.get("segment_id") for row in rows]
    if any(not isinstance(sid, str) or not sid for sid in ids) or len(set(ids)) != len(ids):
        raise ValueError("The export must contain unique, non-empty line ids.")
    if limit < 1 or start < 1 or start > len(rows):
        raise ValueError("Choose a positive line count and a starting line within the export.")
    fingerprint = rows_fingerprint(rows)
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for i, row in enumerate(rows[start - 1:start - 1 + limit], start):
            # Leading apostrophe prevents accidental spreadsheet formulas in display-only cells.
            def display(value):
                value = str(value or "")
                return "'" + value if value.startswith(("=", "+", "-", "@")) else value
            writer.writerow({"line_number": i, "segment_id": row["segment_id"], "start": row["start"], "end": row["end"],
                             "text": display(row.get("text")), "detected_speaker": display(row.get("speaker")),
                             "speaker": "", "notes": "", "source_fingerprint": fingerprint})


def read_sheet(export, path):
    rows = document(export).get("rows", [])
    fingerprint = rows_fingerprint(rows)
    by_id = {r["segment_id"]: (i, r) for i, r in enumerate(rows, 1)}
    if len(by_id) != len(rows):
        raise ValueError("The export contains duplicate line ids.")
    labelled, seen = [], set()
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(FIELDS).issubset(reader.fieldnames or ()):
            raise ValueError("Use the sheet created by this tool and keep its column headings.")
        for line in reader:
            sid = line["segment_id"]
            if sid not in by_id or sid in seen or line["source_fingerprint"] != fingerprint:
                raise ValueError("This sheet is duplicated or belongs to a different export. Create a new sheet.")
            seen.add(sid)
            i, row = by_id[sid]
            if line["line_number"] != str(i) or float(line["start"]) != float(row["start"]) or float(line["end"]) != float(row["end"]):
                raise ValueError("Keep the line numbers, ids and times unchanged; fill only speaker and notes.")
            speaker = (line["speaker"] or "").strip()
            if speaker and speaker != "?":
                labelled.append({"segment_id": sid, "start": row["start"], "end": row["end"], "speaker": speaker})
    return {"lines": labelled, "source_fingerprint": fingerprint, "total_export_lines": len(rows),
            "note": "Human line labels only; blank or ? entries are unscored. These are not speech-activity turn labels."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("make")
    make.add_argument("export", type=Path)
    make.add_argument("sheet", type=Path)
    make.add_argument("--limit", type=int, default=30)
    make.add_argument("--start", type=int, default=1)
    convert = commands.add_parser("reference")
    convert.add_argument("export", type=Path)
    convert.add_argument("sheet", type=Path)
    convert.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        export = json.loads(args.export.read_text(encoding="utf-8-sig"))
        if args.command == "make":
            make_sheet(export, args.sheet, limit=args.limit, start=args.start)
            print("Sheet ready. Fill speaker with the same name for the same person; leave unsure entries blank or use ?.")
        else:
            result = read_sheet(export, args.sheet)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            print(f"Saved {len(result['lines'])} labelled lines. Unlabelled lines are not scored.")
    except (ValueError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
