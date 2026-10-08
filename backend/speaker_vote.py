"""Local, deterministic speaker voting. No models, requests, prices or mutation.

Dialogue labels are the frame, not semantic identities. Support counts are not
probabilities. Policy defaults are uncalibrated engineering choices.
"""
from collections import Counter
from dataclasses import dataclass
import json
import math
import re


VOTERS = ('dialogue', 'audio', 'voice', 'app')
MAX_LABEL = 64


def _label(value):
    return (isinstance(value, str) and 0 < len(value) <= MAX_LABEL
            and value == value.strip() and all(c.isprintable() for c in value))


def _ids(values):
    if not isinstance(values, (list, tuple)):
        return None
    if any(not isinstance(v, str) or not v.strip() for v in values):
        return None
    return list(values) if len(set(values)) == len(values) else None


def _parse(raw, line_ids):
    try:
        ids = _ids(line_ids)
        if not ids:
            return None
        if isinstance(raw, str):
            text = raw.strip()
            if text.startswith('```') and text.endswith('```'):
                parts = text.splitlines()
                if parts[0].strip().lower() not in ('```', '```json'):
                    return None
                text = '\n'.join(parts[1:-1])
            raw = json.loads(text)
        if not isinstance(raw, dict) or not isinstance(raw.get('lines'), list):
            return None
        people = raw.get('people')
        if isinstance(people, bool) or not isinstance(people, int) or not 1 <= people <= len(ids):
            return None
        known, out = set(ids), {}
        for row in raw['lines']:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str):
                return None
            sid = row['id']
            if sid not in known:
                continue
            if sid in out or not _label(row.get('speaker')):
                return None
            out[sid] = row['speaker']
        if len(out) * 5 < len(ids) * 4 or len(set(out.values())) > people:
            return None
        return {sid:out[sid] for sid in ids if sid in out}
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
        return None


def parse_dialogue_answer(raw, line_ids):
    """Return id -> short label, or drop the entire malformed/undercovered voter."""
    return _parse(raw, line_ids)


def parse_audio_answer(raw, line_ids):
    return _parse(raw, line_ids)


def _mapping(value):
    if isinstance(value, dict) and 'name' in value and isinstance(value.get('labels'), dict):
        value = value['labels']
    if not isinstance(value, dict):
        return None
    if any(not isinstance(k, str) or not k.strip() or not _label(v) for k,v in value.items()):
        return None
    return dict(value)


def align_labels(voter, frame):
    """Greedy positive line-count alignment, count desc then labels asc.

    Use {'name': 'audio'|'voice'|'app', 'labels': {id: label}} for voter-specific
    unmatched namespaces. Plain mappings use the name 'voter'. Returns a fresh
    id -> aligned label mapping. Malformed inputs return an empty mapping.
    """
    other, base = _mapping(voter), _mapping(frame)
    if other is None or base is None:
        return {}
    name = voter.get('name', 'voter') if isinstance(voter, dict) and 'name' in voter and isinstance(voter.get('labels'), dict) else 'voter'
    if name not in VOTERS:
        name = 'voter'
    weights = Counter((label, base[sid]) for sid,label in other.items() if sid in base)
    mapping, used = {}, set()
    for (label, target), count in sorted(weights.items(), key=lambda item: (-item[1], item[0][0], item[0][1])):
        if label not in mapping and target not in used:
            mapping[label] = target
            used.add(target)
    occupied = set(base.values())
    for label in sorted(set(other.values()) - set(mapping)):
        target = f'x_{name}_{label}'
        while target in occupied:
            target += '_'
        mapping[label] = target
        occupied.add(target)
    return {sid:mapping[label] for sid,label in other.items()}


def choose_k(dialogue_labels, min_lines=5):
    """Number of dialogue groups with enough lines; names have no meaning."""
    if isinstance(min_lines, bool) or not isinstance(min_lines, int) or min_lines < 1:
        return 0
    if isinstance(dialogue_labels, dict):
        dialogue_labels = dialogue_labels.values()
    try:
        return sum(n >= min_lines for n in Counter(v for v in dialogue_labels if _label(v)).values())
    except TypeError:
        return 0


def cluster_voices(embeddings, k, *, restarts=5, max_iter=50):
    """Spherical k-means with fixed farthest-first starts, no RNG or model.

    Missing, non-finite, zero and inconsistent-dimension vectors cast no vote.
    Tied modal dimensions choose the smaller dimension. Group count is clamped
    to distinct valid directions. Cluster ids are arbitrary, canonical strings.
    Invalid parameters return a complete all-None result, never partial labels.
    """
    if not isinstance(embeddings, (list, tuple)):
        return []
    empty = [None] * len(embeddings)
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in (k, restarts, max_iter)):
        return empty
    import numpy as np
    vectors = []
    for i,value in enumerate(embeddings):
        try:
            if value is None or isinstance(value, (str, bytes)):
                continue
            v = np.asarray(value, dtype=np.float64)
            if v.ndim != 1 or v.size < 2 or not np.isfinite(v).all():
                continue
            scale = float(np.max(np.abs(v)))
            if scale == 0:
                continue
            v = v / scale
            v = v / np.linalg.norm(v)
            vectors.append((i,v))
        except (TypeError, ValueError, OverflowError):
            continue
    if not vectors:
        return empty
    sizes = Counter(v.size for _,v in vectors)
    dim = min(sizes, key=lambda d: (-sizes[d], d))
    vectors = sorted(((i,v) for i,v in vectors if v.size == dim), key=lambda pair: tuple(pair[1]))
    matrix = np.stack([v for _,v in vectors])
    unique = np.unique(matrix, axis=0)
    k = min(k, len(unique))
    best = None
    # Fixed evenly spaced initial indices; subsequent centers are farthest first.
    for initial in sorted({r * len(unique) // min(restarts, len(unique)) for r in range(min(restarts, len(unique)))}):
        indices = [initial]
        while len(indices) < k:
            near = (unique @ unique[indices].T).max(axis=1)
            near[indices] = math.inf
            indices.append(int(np.argmin(near)))
        centers = unique[indices].copy()
        previous = None
        for _ in range(max_iter):
            scores = matrix @ centers.T
            labels = scores.argmax(axis=1)
            if previous is not None and np.array_equal(previous, labels):
                break
            previous = labels.copy()
            updated = centers.copy()
            reserved = set()
            for c in range(k):
                members = matrix[labels == c]
                center = members.sum(axis=0) if len(members) else np.zeros(dim)
                norm = float(np.linalg.norm(center))
                if norm > 1e-12:
                    updated[c] = center / norm
                else:
                    # Empty/antipodal clusters: re-seed a worst-represented row.
                    for j in np.argsort(scores.max(axis=1), kind='stable'):
                        if int(j) not in reserved:
                            updated[c] = matrix[j]
                            reserved.add(int(j))
                            break
            centers = updated
        order = sorted(range(k), key=lambda c: tuple(centers[c]))
        canonical = {old:new for new,old in enumerate(order)}
        labels = (matrix @ centers.T).argmax(axis=1)
        labels = tuple(canonical[int(c)] for c in labels)
        objective = float((matrix @ centers.T).max(axis=1).sum())
        candidate = (objective, labels)
        if best is None or objective > best[0] + 1e-12 or (abs(objective - best[0]) <= 1e-12 and labels < best[1]):
            best = candidate
    out = empty[:]
    for (i,_), label in zip(vectors, best[1]):
        out[i] = f'v{label}'
    return out


@dataclass(frozen=True)
class Policy:
    min_new_lines: int = 5
    max_new_speakers: int = 2
    min_support: int = 1
    allow_new: bool = True


def _policy(value):
    if value is None:
        value = Policy()
    if isinstance(value, dict):
        try:
            value = Policy(**value)
        except TypeError:
            return None
    if not isinstance(value, Policy):
        return None
    if (any(isinstance(n, bool) or not isinstance(n, int) for n in
            (value.min_new_lines, value.max_new_speakers, value.min_support))
            or value.min_new_lines < 1 or value.max_new_speakers < 0
            or not 1 <= value.min_support <= len(VOTERS) or not isinstance(value.allow_new, bool)):
        return None
    return value


def _result(rows, used, reason):
    return dict(lines=[dict(segment_id=r['segment_id'], speaker=r['speaker'], changed=False,
                           source='app', support=0, voters_for=[], voters_present=0,
                           badge=False, reasons=[reason]) for r in rows],
                summary=dict(new_speakers=[], dropped_small=[], voters_used=used,
                             status=reason, lines_changed=0, badged_lines=0))


def decide(lines, voters=None, policy=None):
    """Plurality in dialogue's label frame; ties prefer dialogue.

    By default the app votes from lines. Explicit voters['app']=None removes its
    vote for offline ablation only; the unchanged app remains the fallback.
    At least three complete-enough voters INCLUDING dialogue are required both
    globally and on each acted-on line. Invalid line ids/labels abort the entire
    result, rather than returning a partially changed project.
    """
    if not isinstance(lines, (list, tuple)):
        return _result([], [], 'invalid_lines')
    rows = list(lines)
    if any(not isinstance(r, dict) for r in rows):
        return _result([], [], 'invalid_lines')
    ids = _ids([r.get('segment_id') for r in rows])
    if ids is None or any(not _label(r.get('speaker')) or ('manual_speaker' in r and not isinstance(r['manual_speaker'], bool)) for r in rows):
        return _result([], [], 'invalid_lines')
    if not rows:
        return _result([], [], 'empty')
    chosen = _policy(policy)
    if chosen is None or (voters is not None and not isinstance(voters, dict)):
        return _result(rows, [], 'invalid_input')
    voters = voters or {}
    original = {r['segment_id']:r['speaker'] for r in rows}
    available = {}
    for name in VOTERS:
        value = voters.get(name, original if name == 'app' else None)
        if name == 'voice' and isinstance(value, dict):
            value = {sid:label for sid,label in value.items() if label is not None}
        mapped = _mapping(value)
        if mapped is None:
            continue
        mapped = {sid:label for sid,label in mapped.items() if sid in original}
        if not mapped or (name != 'voice' and len(mapped) * 5 < len(rows) * 4):
            continue
        if name == 'app' and mapped != original:
            continue
        available[name] = mapped
    used = [name for name in VOTERS if name in available]
    if 'dialogue' not in available or len(available) < 3:
        return _result(rows, used, 'insufficient_voters')
    frame = available['dialogue']
    aligned = {name:(dict(frame) if name == 'dialogue' else align_labels({'name':name, 'labels':mapping}, frame))
               for name,mapping in available.items()}
    # Align the app even when its vote is absent: it still defines existing names.
    app_frame = align_labels({'name':'app', 'labels':original}, frame)
    existing = {app_frame[sid]:name for sid,name in original.items()}
    preliminary, wins = [], {}
    for index,row in enumerate(rows):
        sid = row['segment_id']
        votes = {name:aligned[name][sid] for name in used if sid in aligned[name]}
        reasons, winner = [], app_frame[sid]
        active = 'dialogue' in votes and len(votes) >= 3 and not row.get('manual_speaker', False)
        if active:
            counts = Counter(votes.values())
            top = max(counts.values())
            tied = sorted(label for label,count in counts.items() if count == top)
            winner = votes['dialogue'] if votes['dialogue'] in tied else tied[0]
            if len(tied) > 1:
                reasons.append('tie_broken_by_dialogue')
            if top < chosen.min_support:
                winner = app_frame[sid]
                reasons.append('insufficient_support')
            elif winner not in existing:
                wins.setdefault(winner, []).append((index, sum(label == winner for label in votes.values())))
        else:
            reasons.append('manual_choice' if row.get('manual_speaker', False) else 'insufficient_line_voters')
        preliminary.append((row, votes, winner, active, reasons))
    accepted, rejected = {}, []
    matches = [re.fullmatch(r'Speaker ([0-9]+)', name) for name in original.values()]
    next_number = max((int(m[1]) for m in matches if m), default=0) + 1
    for label,entries in sorted(wins.items(), key=lambda item: (item[1][0][0], item[0])):
        backed = sum(support >= 2 for _,support in entries)
        reason = ('new_disabled' if not chosen.allow_new else
                  'too_few_lines' if len(entries) < chosen.min_new_lines else
                  'too_little_support' if backed < chosen.min_new_lines else
                  'new_speaker_limit' if len(accepted) >= chosen.max_new_speakers else None)
        if reason:
            rejected.append(dict(label=label, lines=len(entries), backed_lines=backed, reason=reason))
        else:
            accepted[label] = f'Speaker {next_number}'
            next_number += 1
    out = []
    for row,votes,winner,active,reasons in preliminary:
        sid = row['segment_id']
        candidate_rejected = active and winner not in existing and winner not in accepted
        if candidate_rejected:
            winner = app_frame[sid]
            reasons.append('candidate_rejected')
        final = accepted[winner] if winner in accepted else existing[winner]
        voters_for = [name for name,label in votes.items() if label == winner]
        support, present = len(voters_for), len(votes)
        badge = active and (candidate_rejected or support * 2 <= present)
        if active and support * 2 <= present:
            reasons.append('voters_split')
        changed = final != row['speaker']
        out.append(dict(segment_id=sid, speaker=final, changed=changed,
                        source='new' if winner in accepted else 'vote' if changed else 'app',
                        support=support, voters_for=voters_for, voters_present=present,
                        badge=badge, reasons=reasons))
    return dict(lines=out, summary=dict(new_speakers=[dict(label=name, lines=sum(r['speaker'] == name for r in out))
                for name in accepted.values()], dropped_small=rejected, voters_used=used, status='ok',
                lines_changed=sum(r['changed'] for r in out), badged_lines=sum(r['badge'] for r in out)))
