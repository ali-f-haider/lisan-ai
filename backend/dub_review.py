"""Deterministic review state. A check belongs to the exact ten reviewed rows."""
import hashlib
import json
import math


def overlaps(rows, tolerance=0.02):
    found = {}
    active = []
    for row in sorted(rows, key=lambda r: float(r['start'])):
        start, end = float(row['start']), float(row['end'])
        if not math.isfinite(start + end):
            continue
        active = [r for r in active if float(r['end']) > start + tolerance]
        for other in active:
            if min(end, float(other['end'])) - start > tolerance:
                found.setdefault(row['segment_id'], []).append(other['segment_id'])
                found.setdefault(other['segment_id'], []).append(row['segment_id'])
        active.append(row)
    return found


def batch_hash(rows):
    fields = ('segment_id', 'start', 'end', 'text', 'arabic_text', 'speaker_id', 'emotion')
    payload = [{k: r.get(k) for k in fields} for r in rows]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def batches(rows, saved=None):
    saved = saved or {}
    return [{'page': i // 10, 'first': i + 1, 'last': min(i + 10, len(rows)),
             'hash': batch_hash(rows[i:i + 10]),
             'reviewed': saved.get(str(i // 10)) == batch_hash(rows[i:i + 10])}
            for i in range(0, len(rows), 10)]


def emotion_review(row):
    # Text translation is a suggestion, not a measured probability of vocal emotion.
    return 'confirmed' if row.get('emotion_set') else 'listen_and_review'


def output_budget(size, seconds=0, video=True, tracks=False, references=True):
    """Conservative stream-copy + AAC/container budget, including editing assets."""
    seconds = max(0.0, float(seconds or 0))
    audio = math.ceil(seconds * 25000) + 1048576
    final = max(0, int(size or 0)) + audio if video else audio
    # Corrections need the music track even when separate download tracks weren't chosen.
    final += audio
    if tracks:
        final += audio
    if references:
        final += 8 * 30 * 44100 * 2   # eight mono reference clips, at most 30 seconds each
    return math.ceil(final * 1.05)
