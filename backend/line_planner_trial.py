"""Measure the AI line planner on a clip whose lines you corrected by hand. Costs a few cents; run it from the backend folder.

  python line_planner_trial.py --engine engine_lines.json --truth my_lines.json --price-in P --price-out Q

engine_lines.json  the lines the engine made (the project's segments file: a list of lines or {"segments": [...]})
my_lines.json      the same video with the lines the way you corrected them
P and Q            the Gemini price per MILLION tokens (input, output) that you read on your Google billing page.
                   The script never guesses a price: without both numbers it reports tokens only.
Needs GEMINI_API_KEY in the environment (use a test key). --dry shows the first request and exits without calling anything.

What it prints: how many of your line cuts the engine already had, how many the planner added or broke, the tokens used,
the cost for this clip, and the cost per minute of video (use --minutes if the last line ends before the clip does).
A "cut" is the place in the word stream where one line stops and the next begins, so only the splitting is judged.
"""
import argparse
import difflib
import json
import os
import re
import sys

import line_planner
import line_tidy


def load(path):
    data = json.load(open(path, encoding="utf-8"))
    data = data.get("segments", data) if isinstance(data, dict) else data
    rows = []
    for i, r in enumerate(data):
        rows.append({"segment_id": r.get("segment_id") or f"seg_{i}", "start": float(r["start"]), "end": float(r["end"]),
                     "text": r.get("text") or r.get("english_text") or "", "speaker": r.get("speaker") or r.get("speaker_id") or "?",
                     "speaker_id": r.get("speaker_id") if r.get("speaker_id") is not None else r.get("speaker"),
                     "words": r.get("words") or []})
    return sorted(rows, key=lambda r: (r["start"], r["end"]))


def words_of(rows):
    out = []
    for r in rows:
        out += [re.sub(r"\W+", "", w.lower()) for w in str(r.get("text") or "").split()]
    return [w for w in out if w]


def cuts(rows):
    """Word-stream positions where a new line starts (the first line does not count)."""
    pos, out = 0, set()
    for r in rows:
        n = len(words_of([r]))
        if pos and n:
            out.add(pos)
        pos += n
    return out


def compare(truth_rows, rows):
    """(cuts of the truth the rows have, cuts of the truth in total, extra cuts the rows have that the truth does not).
    The two word streams are aligned first, so a few corrected words do not shift every position."""
    a, b = words_of(truth_rows), words_of(rows)
    to_b = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                to_b[i1 + k] = j1 + k
    truth = {to_b[c] for c in cuts(truth_rows) if c in to_b}
    mine = cuts(rows)
    seen = set(to_b.values())
    return len(truth & mine), len(truth), len({c for c in mine if c in seen} - truth)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--price-in", type=float, help="price per million input tokens (read it from your Google billing page)")
    ap.add_argument("--price-out", type=float, help="price per million output tokens (thinking tokens are billed as output)")
    ap.add_argument("--minutes", type=float, help="length of the clip in minutes (default: end of the last line)")
    ap.add_argument("--dry", action="store_true", help="print the first request and stop")
    ap.add_argument("--show", action="store_true", help="print every operation that was applied")
    args = ap.parse_args()

    engine, truth = load(args.engine), load(args.truth)
    tidy_rows, tidy_rep = line_tidy.tidy_lines([dict(r) for r in engine])
    if args.dry:
        print(line_planner.build_prompt(tidy_rows[:line_planner.WINDOW], [], tidy_rows[line_planner.WINDOW:line_planner.WINDOW + line_planner.CONTEXT]))
        return 0
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("Set GEMINI_API_KEY (a test key) first, or use --dry.")
        return 2
    log = []
    real = line_planner._apply
    line_planner._apply = lambda op, rows, vocals: (log.append(op), real(op, rows, vocals))[1]
    planned, rep = line_planner.plan_lines("trial", tidy_rows, key, budget_sec=900)

    print("\nCut check - how many of YOUR line cuts are in the result (higher is better), and how many extra cuts it has (lower is better)")
    for name, rows in (("engine as it was", engine), ("after the free clean-up", tidy_rows), ("after the AI planner", planned)):
        hit, total, extra = compare(truth, rows)
        print(f"  {name:26s} {hit}/{total} of your cuts ({100 * hit / max(1, total):.0f}%), {extra} extra, {len(rows)} lines")
    print(f"\nFree clean-up: {tidy_rep}")
    print(f"Planner: windows={rep['windows']} asked={rep['asked']} applied={rep['applied']} refused={rep['refused']} "
          f"reverted_windows={rep['reverted_windows']} seconds={rep['seconds']}" + (f" stopped={rep['stopped']}" if rep.get("stopped") else "")
          + (f" ERROR={rep['error']}" if rep.get("error") else ""))
    if rep["refusals"]:
        print("  refused because:", ", ".join(sorted(set(rep["refusals"]))))
    if args.show:
        for op in log:
            print("  ", json.dumps(op))
    t_in, t_out = rep["tokens_in"], rep["tokens_out"] + rep["tokens_thoughts"]
    minutes = args.minutes or max(r["end"] for r in engine) / 60.0
    print(f"\nTokens: input {t_in}, output {rep['tokens_out']}, thinking {rep['tokens_thoughts']}  for {minutes:.1f} minutes of video")
    if args.price_in is not None and args.price_out is not None:
        usd = t_in / 1e6 * args.price_in + t_out / 1e6 * args.price_out
        print(f"Cost of this clip: ${usd:.4f}   per minute of video: ${usd / max(minutes, 1e-9):.4f}")
    else:
        print("Give --price-in and --price-out (per million tokens) to see the cost; no price is guessed here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
