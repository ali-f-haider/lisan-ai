"""Measure the gender-aware Arabic translation on a small gold set. Costs a few cents; run it from the backend folder.

  GEMINI_API_KEY=<a test key> python gender_trial.py [--price-in P --price-out Q] [--show] [--dry]

Each case is English lines with known speaker genders and one line to translate; the Arabic that comes back must contain the
right gender form (and not the wrong one). The expected forms were written by Claude: let a native Arabic reader confirm them
(and add the cases that matter for your videos below) before you trust the percentage. Needs no project data.
A case with "check": True also expects the line to be marked "check gender" (the translation had to guess).
"""
import argparse
import json
import os
import re
import sys

import gemini_service
import gender_context as gc
from models import Segment

MARKS = re.compile("[ً-ٰٕ]")
END = r"(?=\s|[.!؟،,?]|$)"


def strip(t):
    return MARKS.sub("", t or "")


def case(name, lines, target, genders, must, must_not=(), raw=False, check=None):
    """lines: [(speaker, english)]; target: index of the line that is judged; genders: {speaker: 'male'|'female'|''} (chosen by the user)."""
    return dict(name=name, lines=lines, target=target, genders=genders, must=must, must_not=list(must_not), raw=raw, check=check)


M, F = "male", "female"
CASES = [
    case("female says 'I'm tired'", [("A", "I'm so tired.")], 0, {"A": F}, r"(متعبة|تعبانة|مرهقة|منهكة)", [r"(?<!\S)(متعب|تعبان|مرهق|منهك)" + END]),
    case("male says 'I'm tired'", [("A", "I'm so tired.")], 0, {"A": M}, r"(متعب|تعبان|مرهق|منهك)" + END, [r"(متعبة|تعبانة|مرهقة|منهكة)"]),
    case("female says 'I'm ready'", [("A", "I'm ready.")], 0, {"A": F}, r"(مستعدة|جاهزة)", [r"(?<!\S)(مستعد|جاهز)" + END]),
    case("man tells a woman she looks tired", [("A", "You look tired."), ("B", "I didn't sleep.")], 0, {"A": M, "B": F}, r"تبدين", [r"تبدو" + END]),
    case("man tells a man he looks tired", [("A", "You look tired."), ("B", "I didn't sleep.")], 0, {"A": M, "B": M}, r"تبدو", [r"تبدين"]),
    case("woman tells a man 'I love you'", [("A", "I love you."), ("B", "Me too.")], 0, {"A": F, "B": M}, r"كَ" + END, [r"كِ" + END], raw=True),
    case("man tells a woman 'I love you'", [("A", "I love you."), ("B", "Me too.")], 0, {"A": M, "B": F}, r"كِ" + END, [r"كَ" + END], raw=True),
    case("a woman asked whether she is ready", [("A", "Are you ready?"), ("B", "Yes, I'm ready.")], 0, {"A": M, "B": F}, r"(مستعدة|جاهزة|أنتِ|هل أنتِ)", [r"(مستعدون|جاهزون|أنتم)"]),
    case("a group of women is asked", [("A", "Are you all ready, ladies?"), ("B", "Yes."), ("C", "Yes.")], 0, {"A": M, "B": F, "C": F}, r"(جاهزات|مستعدات|أنتنّ|أنتن)", [r"(جاهزون|مستعدون)"]),
    case("a mixed group is asked", [("A", "Are you all ready?"), ("B", "Yes."), ("C", "Yes.")], 0, {"A": M, "B": F, "C": M}, r"(جاهزون|مستعدون|أنتم)", [r"(جاهزات|مستعدات)"]),
    case("a name shows who is addressed (genders not given)", [("A", "Sarah, you look tired."), ("B", "Yes.")], 0, {}, r"تبدين", []),
    case("nothing shows who is addressed: masculine and marked", [("A", "You look tired.")], 0, {}, r"تبدو", [], check=True),
]


def run_case(c, key):
    rows = [{"segment_id": f"s{i}", "start": i * 3.0, "end": i * 3.0 + 2.0, "speaker": sp, "text": t} for i, (sp, t) in enumerate(c["lines"])]
    genders = {sp: ({"gender": c["genders"][sp], "source": "chosen", "uncertain": False} if c["genders"].get(sp) else {"gender": "", "source": "", "uncertain": True})
               for sp, _ in c["lines"]}
    tid = rows[c["target"]]["segment_id"]
    gender = {"speakers": gc.speaker_table(genders), "lines": gc.build_context(rows, [tid], genders)}
    seg = Segment(segment_id=tid, start=rows[c["target"]]["start"], end=rows[c["target"]]["end"] + 2.0, speaker=rows[c["target"]]["speaker"], text=rows[c["target"]]["text"])
    return gemini_service.translate_segments("trial", [seg], key, gender=gender), gender


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--price-in", type=float); ap.add_argument("--price-out", type=float)
    ap.add_argument("--show", action="store_true"); ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    key = os.environ.get("GEMINI_API_KEY", "")
    if args.dry:
        for c in CASES[:2]:
            res = {"x": 1}
            rows = [{"segment_id": f"s{i}", "start": i * 3.0, "end": i * 3.0 + 2.0, "speaker": sp, "text": t} for i, (sp, t) in enumerate(c["lines"])]
            print(c["name"], json.dumps(gc.build_context(rows, ["s0"], {sp: {"gender": g} for sp, g in c["genders"].items()}), ensure_ascii=False))
        return 0
    if not key:
        print("Set GEMINI_API_KEY (a test key) first, or use --dry."); return 2
    usage = {"in": 0, "out": 0}
    real = gemini_service.record_gemini
    def count(job_id, data):
        u = (data or {}).get("usageMetadata") or {}
        usage["in"] += int(u.get("promptTokenCount", 0) or 0); usage["out"] += int(u.get("candidatesTokenCount", 0) or 0) + int(u.get("thoughtsTokenCount", 0) or 0)
    gemini_service.record_gemini = count
    passed = 0
    for c in CASES:
        res, _ = run_case(c, key)
        items = res.get("translated_segments") or []
        item = items[0] if items else {}
        text = item.get("arabic_text") or ""
        shown = text if c["raw"] else strip(text)
        probe = text if c["raw"] else strip(text)
        ok = bool(re.search(c["must"], probe)) and not any(re.search(p, probe) for p in c["must_not"])
        if c["check"] is not None:
            ok = ok and (item.get("gender_check") is True) == c["check"]
        passed += ok
        print(("PASS " if ok else "FAIL ") + c["name"] + (f"   -> {text}   addressee={item.get('addressee')} check={item.get('gender_check')}" if (args.show or not ok) else ""))
    print(f"\n{passed} of {len(CASES)} cases right ({100 * passed / len(CASES):.0f}%)")
    print(f"Tokens: input {usage['in']}, output {usage['out']}")
    if args.price_in is not None and args.price_out is not None:
        print(f"Cost of this run: ${usage['in'] / 1e6 * args.price_in + usage['out'] / 1e6 * args.price_out:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
