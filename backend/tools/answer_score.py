"""Score who-says-which-line against a hand-made answer sheet (eval/*_reference.json).

Each reference line is compared with the app's lines that overlap it in time; the app's speaker names are matched one-to-one to
the real people (the matching that gets the most speaking time right). Timing differences between the two transcripts do not
count; only who speaks. Usage:
    python tools/answer_score.py eval/c4k_reference.json segments.json
segments.json: what /api/longdub/<job id>/segments returns (or a plain list of lines with start, end, speaker).
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from speaker_eval import optimal_mapping


def _overlap(a, b):
    return max(0.0, min(float(a["end"]), float(b["end"])) - max(float(a["start"]), float(b["start"])))


def score(reference, rows):
    turns = reference["turns"] if isinstance(reference, dict) else reference
    rows = rows.get("segments", rows.get("rows", [])) if isinstance(rows, dict) else rows
    rows = [r for r in rows if r.get("speaker") and float(r["end"]) > float(r["start"])]
    weights = defaultdict(float)            # (app speaker, real person) -> seconds
    per_turn = []
    for t in turns:
        got = defaultdict(float)
        for r in rows:
            o = _overlap(t, r)
            if o > 0:
                got[r["speaker"]] += o
                weights[(r["speaker"], t["speaker"])] += o
        per_turn.append((t, got))
    mapping = optimal_mapping(weights, {h for h, _ in weights}, {p for _, p in weights})
    right = wrong = 0
    right_sec = total_sec = 0.0
    mistakes = []
    for t, got in per_turn:
        if not got:
            continue
        best = max(got, key=got.get)
        ok = mapping.get(best) == t["speaker"]
        right += ok
        wrong += not ok
        total_sec += sum(got.values())
        right_sec += sum(s for h, s in got.items() if mapping.get(h) == t["speaker"])
        if not ok:
            mistakes.append({"time": t["start"], "text": t.get("text", "")[:60], "real": t["speaker"], "app": best, "app_means": mapping.get(best)})
    missing = [t for t, got in per_turn if not got]
    real = sorted({t["speaker"] for t in turns})
    used = sorted({mapping.get(h) for h in mapping if mapping.get(h)})
    return {"lines_right": right, "lines_scored": right + wrong, "line_accuracy": round(right / max(1, right + wrong), 4),
            "time_accuracy": round(right_sec / total_sec, 4) if total_sec else None,
            "lines_with_no_app_line": len(missing), "people_real": len(real), "people_app": len({r["speaker"] for r in rows}),
            "people_never_matched": [p for p in real if p not in used], "mapping": mapping, "mistakes": mistakes,
            "missing": [{"time": t["start"], "text": t.get("text", "")[:60]} for t in missing]}


def _clock(t):
    t = float(t)
    return f"{int(t // 60)}:{t % 60:04.1f}"


def main():
    ref = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    rows = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8-sig"))
    s = score(ref, rows)
    print(f"Lines right: {s['lines_right']} of {s['lines_scored']} ({100 * s['line_accuracy']:.1f}%), speaking time right: "
          f"{100 * (s['time_accuracy'] or 0):.1f}%; people: {s['people_real']} real, {s['people_app']} in the app; "
          f"never found: {', '.join(s['people_never_matched']) or 'none'}; reference lines with no app line: {s['lines_with_no_app_line']}")
    for m in s["mistakes"]:
        print(f"  {_clock(m['time'])}  real {m['real']:<18} app {m['app']} (= {m['app_means']})  {m['text']}")
    for m in s["missing"]:
        print(f"  {_clock(m['time'])}  missing in the app: {m['text']}")


if __name__ == "__main__":
    main()
