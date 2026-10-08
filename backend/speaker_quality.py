"""Local speaker review evidence; no audio, models, requests or credit charges."""
from bisect import bisect_left
from collections import defaultdict
import math
import re

_END = re.compile(r'[.!?؟]["\'’”)\]]*\s*$')


def _value(word, key):
    return word.get(key) if isinstance(word, dict) else getattr(word, key, None)


def can_smooth_words(previous, fragment, following):
    """Only a short, unpunctuated, non-overlapping continuation may be smoothed.

    Punctuation and pauses are guards against erasing a genuine reply, not proof
    of identity. This deliberately does not infer speakers from text meaning.
    """
    try:
        if not previous or not following or len(fragment) != 1:
            return False
        if _END.search(str(_value(previous[-1], "word") or "").strip()) or _END.search(str(_value(fragment[-1], "word") or "").strip()):
            return False
        a, b = float(_value(fragment[0], "start")), float(_value(fragment[-1], "end"))
        left = a - float(_value(previous[-1], "end"))
        right = float(_value(following[0], "start")) - b
        return all(math.isfinite(x) for x in (a, b, left, right)) and 0 < b - a <= 1.3 and 0 <= left <= 0.3 and 0 <= right <= 0.3
    except (TypeError, ValueError, IndexError):
        return False


def _union_length(pieces):
    end, total = -math.inf, 0.0
    for a, b in sorted(pieces):
        total += max(0.0, b - max(a, end))
        end = max(end, b)
    return total


class TurnIndex:
    def __init__(self, turns, speaker_label_map=None):
        self.turns = []
        labels = speaker_label_map or {}
        for turn in turns:
            try:
                a, b = float(turn["start"]), float(turn["end"])
                speaker = turn["speaker"]
                if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a or not isinstance(speaker, str):
                    continue
                label = labels.get(speaker, speaker)
                if not isinstance(label, str) or not label.strip():
                    continue
                self.turns.append((a, b, label))
            except (KeyError, ValueError, TypeError):
                continue
        self.turns.sort()
        self.starts = [t[0] for t in self.turns]
        self.prefix_end = []
        end = -math.inf
        for _, b, _ in self.turns:
            end = max(end, b)
            self.prefix_end.append(end)

    def overlaps(self, a, b):
        i = bisect_left(self.starts, b) - 1
        out = []
        while i >= 0 and self.prefix_end[i] > a:
            lo, hi, speaker = self.turns[i]
            if hi > a:
                out.append((max(a, lo), min(b, hi), speaker))
            i -= 1
        return out

    def evidence(self, a, b):
        pieces = self.overlaps(a, b)
        by_speaker = defaultdict(list)
        for lo, hi, speaker in pieces:
            by_speaker[speaker].append((lo, hi))
        coverage = {s: _union_length(p) for s, p in by_speaker.items()}
        # Simultaneous DISTINCT speakers, rather than adjacent turns in one word.
        events = []
        for lo, hi, speaker in pieces:
            events.extend(((lo, 1, speaker), (hi, -1, speaker)))
        active, overlap, previous = defaultdict(int), 0.0, a
        for t, delta, speaker in sorted(events):
            if sum(v > 0 for v in active.values()) > 1:
                overlap += t - previous
            active[speaker] += delta
            previous = t
        return coverage, overlap


def assess_rows(rows, turns, speaker_label_map=None, *, before=None):
    """Return metadata by line id; never change labels or mutate any input.

    Confidence describes available timestamp evidence, NOT a calibrated identity
    probability. Pass raw->editor label mapping and, optionally, pre-smoothing rows.
    """
    index = TurnIndex(turns, speaker_label_map)
    original = {r.get("segment_id"): r.get("speaker") for r in before or []}
    all_pieces = defaultdict(list)
    for a, b, speaker in index.turns:
        all_pieces[speaker].append((a, b))
    seconds = {s: _union_length(p) for s, p in all_pieces.items()}
    total = sum(seconds.values())
    little = {s for s, n in seconds.items() if total >= 60 and n < min(15.0, total * 0.1)}
    result = {}
    for row in rows:
        sid, speaker = row.get("segment_id"), row.get("speaker")
        if not isinstance(sid, str):
            continue
        reasons = []
        severe = False
        try:
            a, b = float(row["start"]), float(row["end"])
            if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a:
                raise ValueError()
            coverage, overlap = index.evidence(a, b)
            assigned = coverage.get(speaker, 0.0) / (b - a)
            if not coverage:
                reasons.append("no_detected_turn")
                severe = True
            elif not coverage.get(speaker):
                reasons.append("speaker_not_supported")
                severe = True
            if assigned < 0.5:
                reasons.append("weak_time_coverage")
                severe = True
            if overlap > 1e-6:
                reasons.append("overlapping_speech")
                severe = True
            word_count = len(str(row.get("text") or "").split())
            if b - a < 1.0 or word_count <= 2:
                reasons.append("short_reply")
                severe = True
            words = row.get("words") or []
            timed = 0
            for word in words:
                try:
                    wa, wb = float(_value(word, "start")), float(_value(word, "end"))
                    if not math.isfinite(wa) or not math.isfinite(wb) or wa < 0 or wb <= wa:
                        continue
                    timed += 1
                    supports, _ = index.evidence(wa, wb)
                    if not supports:
                        reasons.append("word_in_gap")
                        severe = True
                    else:
                        if not supports.get(speaker):
                            reasons.append("word_speaker_disagrees")
                            severe = True
                        if len(supports) > 1:
                            reasons.append("word_crosses_turns")
                except (ValueError, TypeError):
                    continue
            if not timed or timed < len(words):
                reasons.append("missing_word_times")
            if sid in original and original[sid] != speaker:
                reasons.append("smoothed_assignment")
                severe = True
            if speaker in little:
                reasons.append("little_speaker_evidence")
            reasons = list(dict.fromkeys(reasons))
            confidence = "low" if severe else "medium" if reasons or assigned < 0.8 else "high"
            result[sid] = {"speaker_confidence": confidence, "speaker_reasons": reasons,
                           "speaker_time_coverage": round(min(1.0, max(0.0, assigned)), 3)}
        except (ValueError, KeyError, TypeError):
            result[sid] = {"speaker_confidence": "low", "speaker_reasons": ["invalid_line_times"], "speaker_time_coverage": 0.0}
    return result


def count_mismatch(turns, stated):
    """Informational only; never enforce, merge or price speakers."""
    detected = len({speaker for _, _, speaker in TurnIndex(turns).turns})
    try:
        expected = int(stated)
        if expected < 1 or isinstance(stated, bool) or float(stated) != expected:
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    return {"stated": expected, "detected": detected, "needs_review": expected != detected}
