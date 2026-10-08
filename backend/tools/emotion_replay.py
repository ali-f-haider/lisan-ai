"""Offline timing replay. Reads transcript JSON only; never listens or makes requests."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delivery as D


def _old_rate(row):
    """Job 10 baseline (3 words, 0.8 s, gaps > 0.4 s, slot fallback)."""
    try:
        words = [w for w in row.get("words", []) if isinstance(w, dict) and w.get("start") is not None and w.get("end") is not None]
        if len(words) >= 3:
            span = float(words[-1]["end"]) - float(words[0]["start"])
            span -= sum(max(0.0, float(b["start"]) - float(a["end"])) for a, b in zip(words, words[1:])
                        if float(b["start"]) - float(a["end"]) > 0.4)
            syl = D.syllables(" ".join(str(w.get("word") or "") for w in words))
        else:
            if len(row.get("text", "").split()) < 3:
                return None
            span = float(row["end"]) - float(row["start"])
            syl = D.syllables(row.get("text", ""))
        return syl / span if span >= 0.8 and syl >= 3 else None
    except Exception:
        return None


def _old_moment(rows, i):
    return D._median([x for x in (_old_rate(r) for r in rows[max(0, i - 4):i + 5]) if x])


def _old_pace(rows, i):
    own, around = _old_rate(rows[i]), _old_moment(rows, i)
    rate = (own + around) / 2 if own is not None and around is not None else around if around is not None else own
    return "unknown" if rate is None else "slow" if rate < 4.3 else "fast" if rate > 6 else "normal"


def _old_ground(emotion, pace):
    parts = [p.strip() for p in str(emotion or "").split(",") if p.strip()]
    if not parts or pace == "unknown":
        return emotion
    drop = set(D.FAST_TAGS if pace != "fast" else D.SLOW_TAGS)
    return ", ".join(p for p in parts if p.lower() not in drop) or "neutral"


def replay(rows):
    rows = sorted(rows, key=lambda r: (float(r["start"]), float(r["end"])))
    out = []
    for i, row in enumerate(rows):
        before, after = _old_pace(rows, i), D.pace(rows, i)
        original = row.get("emotion", "neutral")
        out.append({"segment_id": row.get("segment_id", str(i)), "speaker": row.get("speaker"),
                    "line_rate_before": _old_rate(row), "line_rate": D.line_rate(row),
                    "moment_rate_mixed": _old_moment(rows, i), "moment_rate": D.moment_rate(rows, i),
                    "pace_before": before, "pace": after, "emotion_before": _old_ground(original, before),
                    "emotion": D.ground(original, after) if not row.get("emotion_set") else original})
    def slow(key):
        return sum(any(t.strip().lower() in D.SLOW_TAGS for t in str(r.get(key) or "").split(",")) for r in out)
    return {"slow_tags_before": slow("emotion_before"), "slow_tags_after": slow("emotion"), "lines": out}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--json", action="store_true", help="Print the full replay as JSON")
    args = parser.parse_args()
    data = json.loads(args.transcript.read_text(encoding="utf-8-sig"))
    rows = data if isinstance(data, list) else data["rows"]
    result = replay(rows)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return
    def number(x):
        return "?" if x is None else f"{x:.2f}"
    print("id | speaker | line before/new | moment mixed/new | pace before/new | tags before -> new")
    for r in result["lines"]:
        print(f"{r['segment_id']} | {r['speaker']} | {number(r['line_rate_before'])}/{number(r['line_rate'])} | "
              f"{number(r['moment_rate_mixed'])}/{number(r['moment_rate'])} | {r['pace_before']}/{r['pace']} | "
              f"{r['emotion_before']} -> {r['emotion']}")
    print(f"Slow tags: {result['slow_tags_before']} before; {result['slow_tags_after']} after. No listening was performed.")


if __name__ == "__main__":
    main()
