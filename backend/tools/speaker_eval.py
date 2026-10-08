"""Offline speaker scoring: 10 ms frame centres, overlap-aware speaker time.

Default collar is zero. A nonzero collar excludes that many seconds on EACH
side of every reference turn boundary. Mapping is globally optimal one-to-one,
not greedy. This is a small diagnostic scorer, not an official benchmark tool.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

FRAME_SECONDS = 0.01


def rows_fingerprint(rows):
    """Exclude predicted speakers so the same frozen lines can be re-scored."""
    value = [[r.get("segment_id"), r.get("start"), r.get("end"), r.get("text")] for r in rows]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def document(value):
    if isinstance(value, list):
        return {"rows": value} if any("segment_id" in r for r in value) else {"turns": value}
    if not isinstance(value, dict):
        raise ValueError("The file must contain a JSON object or a list of lines or turns.")
    if "segments" in value and "rows" not in value:
        return {**value, "rows": value["segments"]}
    return value


def intervals(values):
    out = []
    for row in values:
        try:
            a, b = float(row["start"]), float(row["end"])
            speaker = row["speaker"]
        except (KeyError, TypeError, ValueError):
            raise ValueError("Each turn needs start, end and speaker.") from None
        if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a or not isinstance(speaker, str) or not speaker.strip():
            raise ValueError("Turn times must be finite, non-negative and increasing; speaker must be a name.")
        out.append({"start": a, "end": b, "speaker": speaker})
    return out


def optimal_mapping(weights, hypothesis, reference):
    """Deterministic Hungarian maximum-weight assignment, O(speakers**3)."""
    hs, rs = sorted(hypothesis), sorted(reference)
    n = max(len(hs), len(rs))
    if not n:
        return {}
    ceiling = max(weights.values(), default=0)
    costs = [[ceiling - weights.get((h, r), 0) for r in rs] + [ceiling] * (n - len(rs)) for h in hs]
    costs += [[ceiling] * n for _ in range(n - len(hs))]
    u, v, p, way = [0] * (n + 1), [0] * (n + 1), [0] * (n + 1), [0] * (n + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minimum, used = [math.inf] * (n + 1), [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], math.inf, 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = costs[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minimum[j]:
                        minimum[j], way[j] = cur, j0
                    if minimum[j] < delta:
                        delta, j1 = minimum[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            j0 = j1
            if not p[j0]:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if not j0:
                break
    return {hs[p[j] - 1]: rs[j - 1] for j in range(1, len(rs) + 1)
            if 0 < p[j] <= len(hs) and weights.get((hs[p[j] - 1], rs[j - 1]), 0) > 0}


def _boundary(seconds):
    # First frame centre at or after seconds; intervals are [start, end).
    return max(0, math.ceil(seconds / FRAME_SECONDS - 0.5 - 1e-9))


def _runs(reference, hypothesis, duration, collar, regions):
    """Run-length frame sweep: memory depends on turns, not audio duration."""
    n = _boundary(duration)
    if not n:
        return
    events = defaultdict(list)
    def add(a, b, kind, label=""):
        lo, hi = min(n, _boundary(max(0, a))), min(n, _boundary(max(0, b)))
        if hi > lo:
            events[lo].append((kind, label, 1))
            events[hi].append((kind, label, -1))
    for kind, values in (("r", reference), ("h", hypothesis)):
        for row in values:
            add(row["start"], row["end"], kind, row["speaker"])
    if collar:
        for row in reference:
            for boundary in (row["start"], row["end"]):
                add(boundary - collar, boundary + collar, "exclude")
    for region in regions if regions is not None else [{"start": 0, "end": duration}]:
        a, b = float(region["start"]), float(region["end"])
        if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a:
            raise ValueError("Evaluation regions need finite increasing times.")
        add(a, b, "region")
    counters = {k: Counter() for k in ("r", "h", "exclude", "region")}
    points = sorted(set(events) | {0, n})
    for pos, nxt in zip(points, points[1:]):
        for kind, label, delta in events[pos]:
            counters[kind][label] += delta
            if counters[kind][label] == 0:
                del counters[kind][label]
        if counters["region"] and not counters["exclude"]:
            yield nxt - pos, set(counters["r"]), set(counters["h"])


def _line_index(rows, reference=False):
    out, seen = {}, set()
    for row in rows:
        sid = row.get("segment_id")
        speaker = row.get("speaker")
        if not isinstance(sid, str) or not sid or sid in seen:
            raise ValueError("Line ids must be non-empty and unique.")
        seen.add(sid)
        if reference and (not speaker or speaker == "?"):
            continue
        if not isinstance(speaker, str):
            raise ValueError("Each scored line needs a speaker name.")
        out[sid] = row
    return out


def _interval_speaker(line, turns):
    a, b = float(line["start"]), float(line["end"])
    if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a:
        raise ValueError("Timed line labels need finite increasing times.")
    # Union each speaker's intervals: repeated tracks must not double-count.
    totals = defaultdict(list)
    for turn in turns:
        lo, hi = max(a, turn["start"]), min(b, turn["end"])
        if hi > lo:
            totals[turn["speaker"]].append((lo, hi))
    weights = {}
    for speaker, pieces in totals.items():
        end, total = -math.inf, 0.0
        for lo, hi in sorted(pieces):
            total += max(0, hi - max(lo, end))
            end = max(end, hi)
        weights[speaker] = total
    return min(weights, key=lambda s: (-weights[s], s)) if weights else None


def evaluate(reference, hypothesis, *, collar=0.0, line_mode="ids"):
    reference, hypothesis = document(reference), document(hypothesis)
    if not math.isfinite(collar) or collar < 0:
        raise ValueError("The collar must be finite and non-negative.")
    if line_mode not in ("ids", "intervals"):
        raise ValueError("Line mode must be ids or intervals.")
    ref_rows = reference.get("lines", reference.get("rows", []))
    hyp_rows = hypothesis.get("rows", hypothesis.get("lines", []))
    rlines, hlines = _line_index(ref_rows, True), _line_index(hyp_rows)
    if (line_mode == "ids" and reference.get("source_fingerprint")
            and reference["source_fingerprint"] != rows_fingerprint(hyp_rows)):
        raise ValueError("These lines differ from the labelled export; use the same export or interval mode with timed labels.")
    ref = intervals(reference["turns"]) if "turns" in reference else None
    timed_rows = [r for r in hyp_rows if "start" in r and "end" in r]
    hyp = intervals(hypothesis["turns"]) if "turns" in hypothesis else intervals(timed_rows)
    if line_mode == "intervals":
        predicted = {sid: _interval_speaker(line, hyp) for sid, line in rlines.items()}
    else:
        predicted = {sid: hlines.get(sid, {}).get("speaker") for sid in rlines}
    rs = {r["speaker"] for r in ref or []} | {r["speaker"] for r in rlines.values()}
    hs = {r["speaker"] for r in hyp} | {r["speaker"] for r in hlines.values()}
    weights = Counter()
    durations = [float(reference.get("duration", 0)), float(hypothesis.get("duration", 0))]
    if any(not math.isfinite(d) or d < 0 for d in durations):
        raise ValueError("Duration must be finite and non-negative.")
    duration = max([r["end"] for r in (ref or []) + hyp] + durations)
    regions = reference.get("regions")
    if ref is not None:
        for frames, active_r, active_h in _runs(ref, hyp, duration, collar, regions):
            for h in active_h:
                for r in active_r:
                    weights[h, r] += frames
        mapping_basis = "scored frame overlap"
    else:
        for sid, r in rlines.items():
            if predicted[sid] is not None:
                weights[predicted[sid], r["speaker"]] += 1
        mapping_basis = "labelled line matches"
    mapping = optimal_mapping(weights, hs, rs)
    der = None
    if ref is not None:
        missed = false_alarm = confusion = total = frames_scored = 0
        for frames, active_r, active_h in _runs(ref, hyp, duration, collar, regions):
            correct = len(active_r & {mapping[h] for h in active_h if h in mapping})
            total += frames * len(active_r)
            missed += frames * max(0, len(active_r) - len(active_h))
            false_alarm += frames * max(0, len(active_h) - len(active_r))
            confusion += frames * (min(len(active_r), len(active_h)) - correct)
            frames_scored += frames
        der = {"rate": (missed + false_alarm + confusion) / total if total else None,
               "missed_speaker_seconds": missed * FRAME_SECONDS, "false_alarm_speaker_seconds": false_alarm * FRAME_SECONDS,
               "confusion_speaker_seconds": confusion * FRAME_SECONDS, "reference_speaker_seconds": total * FRAME_SECONDS,
               "scored_wall_seconds": frames_scored * FRAME_SECONDS,
               "hypothesis_kind": "detector_turns" if "turns" in hypothesis else "row_span_proxy"}
    correct_lines, wrong, risky = 0, [], []
    for sid, r in rlines.items():
        h = predicted[sid]
        match = mapping.get(h) == r["speaker"]
        correct_lines += int(match)
        entry = {"segment_id": sid, "reference": r["speaker"], "predicted": h, "mapped": mapping.get(h)}
        if not match:
            wrong.append(entry)
    for sid, h in hlines.items():
        if h.get("speaker_confidence") == "low":
            risky.append({"segment_id": sid, "reasons": h.get("speaker_reasons", [])})
    return {"frame_seconds": FRAME_SECONDS, "collar_seconds_each_side": collar, "overlap_scored": True,
            "mapping": mapping, "mapping_basis": mapping_basis, "detected_speakers": len(hs),
            "true_speakers_seen_in_reference": len(rs), "der": der,
            "line_accuracy": correct_lines / len(rlines) if rlines else None,
            "correct_lines": correct_lines, "labelled_lines": len(rlines), "wrong_lines": wrong,
            "flagged_unlabelled_lines": [r for r in risky if r["segment_id"] not in rlines],
            "notes": ["Line-only labels cannot measure missed speech or false alarms.",
                      "Partial labels show only the reference speakers observed, not a complete true count.",
                      "Row-span DER is an attribution proxy, not raw detector DER."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--collar", type=float, default=0.0)
    parser.add_argument("--line-mode", choices=("ids", "intervals"), default="ids")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(args.reference.read_text(encoding="utf-8-sig")),
                          json.loads(args.output.read_text(encoding="utf-8-sig")), collar=args.collar, line_mode=args.line_mode)
    except (ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return
    print(f"Speakers: detected {result['detected_speakers']}; seen in reference {result['true_speakers_seen_in_reference']}.")
    print(f"Mapping ({result['mapping_basis']}): {result['mapping']}")
    if result["der"]:
        d = result["der"]
        rate = "undefined (no reference speech)" if d["rate"] is None else f"{100 * d['rate']:.2f}%"
        print(f"DER [{d['hypothesis_kind']}]: {rate}; missed {d['missed_speaker_seconds']:.3f}s, "
              f"false alarm {d['false_alarm_speaker_seconds']:.3f}s, confusion {d['confusion_speaker_seconds']:.3f}s.")
    else:
        print("DER unavailable: reference has line labels only.")
    accuracy = "unavailable" if result["line_accuracy"] is None else f"{100 * result['line_accuracy']:.2f}%"
    print(f"Line accuracy: {accuracy} ({result['correct_lines']}/{result['labelled_lines']}).")
    print("Lines to check (reference mismatches first):")
    for row in (result["wrong_lines"] + result["flagged_unlabelled_lines"])[:20]:
        print(json.dumps(row, ensure_ascii=False))
    for note in result["notes"]:
        print(note)


if __name__ == "__main__":
    main()
