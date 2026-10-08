"""Offline JSON-only voting evaluation, including single and pair ablations.

Input rows have app/dialogue/audio/reference labels plus saved voice_cluster or
embedding vectors. Reference labels are NEVER passed to the voting engine.
"""
import argparse
from collections import Counter
from itertools import combinations
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from speaker_eval import evaluate as score
else:
    from .speaker_eval import evaluate as score
from speaker_vote import (VOTERS, Policy, choose_k, cluster_voices, decide,
                          parse_audio_answer, parse_dialogue_answer)


def _answer(rows, key):
    labels = [r[key] for r in rows if isinstance(r.get(key), str)]
    return dict(people=len(set(labels)), lines=[dict(id=r['segment_id'], speaker=r[key])
                for r in rows if r.get(key) is not None])


def build_voters(rows):
    ids = [r['segment_id'] for r in rows]
    dialogue = parse_dialogue_answer(_answer(rows, 'dialogue'), ids)
    audio = parse_audio_answer(_answer(rows, 'audio'), ids)
    k = choose_k(dialogue or {})
    if any('embedding' in r for r in rows):
        groups = cluster_voices([r.get('embedding') for r in rows], k)
        voice = {sid:g for sid,g in zip(ids, groups) if g is not None}
        source = 'computed_from_supplied_embeddings'
    else:
        voice = {r['segment_id']:r['voice_cluster'] for r in rows if r.get('voice_cluster') is not None}
        source = 'saved_clusters_no_embeddings_available'
    return dict(dialogue=dialogue, audio=audio, voice=voice or None), dict(
        voice_source=source, choose_k=k, voice_groups=len({v for v in voice.values() if isinstance(v, str)}) if voice else 0,
        note='Saved cluster labels cannot be reclustered to choose_k without embeddings. No synthetic vectors are substituted.')


def _score(rows, result, baseline):
    reference = {'lines':[dict(segment_id=r['segment_id'], speaker=r['reference'])
                         for r in rows if r.get('reference') not in (None, '', '?')]}
    final = result['lines']
    # One best one-to-one mapping per full run; sheets share that mapping.
    after_score = score(reference, {'rows':final})
    before_map, after_map = baseline['mapping'], after_score['mapping']
    by_id = {r['segment_id']:r for r in final}
    def subset(selected):
        labelled = [r for r in selected if r.get('reference') not in (None, '', '?')]
        before = after = fixed = broken = changed = caught = badged = 0
        for row in labelled:
            predicted = by_id[row['segment_id']]
            was = before_map.get(row['app']) == row['reference']
            now = after_map.get(predicted['speaker']) == row['reference']
            before += was; after += now
            fixed += not was and now
            broken += was and not now
            changed += predicted['speaker'] != row['app']
            badged += predicted['badge']
            caught += predicted['badge'] and not now
        total = len(labelled)
        return dict(labelled_lines=total, before_correct=before, after_correct=after,
                    before_accuracy=before/total if total else None,
                    after_accuracy=after/total if total else None,
                    changed=changed, fixed=fixed, broken=broken,
                    badged=badged, wrong=total-after, wrong_caught=caught)
    sheets = sorted({r['sheet'] for r in rows if isinstance(r.get('sheet'), str) and r['sheet']})
    return dict(overall=subset(rows), sheets={sheet:subset([r for r in rows if r.get('sheet') == sheet]) for sheet in sheets},
                all_lines=dict(lines=len(rows), changed=sum(r['changed'] for r in final),
                               badged=sum(r['badge'] for r in final),
                               unknown_truth=sum(r.get('reference') in (None, '', '?') for r in rows),
                               known_wrong_caught=subset(rows)['wrong_caught']),
                mapping=after_map, baseline_mapping=before_map,
                summary=result['summary'])


def evaluate_clip(data):
    rows = data.get('lines') if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError('The input needs a non-empty lines list.')
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Every line must be an object.')
        sid = row.get('segment_id')
        if not isinstance(sid, str) or not sid.strip() or sid in ids:
            raise ValueError('Line ids must be non-empty and unique.')
        if not isinstance(row.get('app'), str) or not row['app'].strip():
            raise ValueError('Every line needs its original app label.')
        if row.get('reference') is not None and not isinstance(row['reference'], str):
            raise ValueError('Reference labels must be strings or null.')
        ids.add(sid)
    app = [dict(segment_id=r['segment_id'], speaker=r['app'],
                **({'manual_speaker':r['manual_speaker']} if 'manual_speaker' in r else {})) for r in rows]
    reference = {'lines':[dict(segment_id=r['segment_id'], speaker=r.get('reference')) for r in rows]}
    baseline = score(reference, {'rows':app})
    voters, metadata = build_voters(rows)
    runs = {}
    baseline_result = decide(app, {})
    runs['app_alone'] = _score(rows, baseline_result, baseline)
    runs['all'] = _score(rows, decide(app, voters), baseline)
    for count in (1, 2):
        for removed in combinations(VOTERS, count):
            variant = dict(voters)
            for name in removed:
                variant[name] = None
            name = 'without_' + '_and_'.join(removed)
            runs[name] = _score(rows, decide(app, variant), baseline)
    runs['require_three_votes'] = _score(rows, decide(app, voters, Policy(min_support=3)), baseline)
    runs['existing_speakers_only'] = _score(rows, decide(app, voters, Policy(allow_new=False)), baseline)
    # Diagnostic only: explains prototype figures measured before tiny groups
    # were rejected. Production defaults above are never tuned to the labels.
    runs['before_candidate_size_and_cap'] = _score(rows, decide(app, voters,
        Policy(min_new_lines=1, max_new_speakers=len(rows))), baseline)
    return dict(provenance='measured_here_on_the_supplied_labels; not held-out performance',
                alignment='greedy positive count, then voter label, then frame label',
                scoring='optimal overall one-to-one name mapping per run, shared across sheets; reference never enters decisions',
                metadata=metadata, runs=runs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('clip', type=Path)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        result = evaluate_clip(json.loads(args.clip.read_text(encoding='utf-8-sig')))
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.error(str(error))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return
    print('Measured here on supplied labels; this is not held-out accuracy.')
    print('Run | Correct/labelled | Changed | Fixed | Broken | Badged labelled | Wrong caught')
    for name,run in result['runs'].items():
        r = run['overall']
        print(f"{name} | {r['after_correct']}/{r['labelled_lines']} | {r['changed']} | {r['fixed']} | {r['broken']} | {r['badged']} | {r['wrong_caught']}/{r['wrong']}")
    print(json.dumps(result['metadata'], ensure_ascii=False))
    print('All-run sheets:', json.dumps(result['runs']['all']['sheets'], ensure_ascii=False))
    print('New speakers:', json.dumps(result['runs']['all']['summary']['new_speakers'], ensure_ascii=False))
    print('Rejected candidates:', json.dumps(result['runs']['all']['summary']['dropped_small'], ensure_ascii=False))
    print('All-line badge coverage:', json.dumps(result['runs']['all']['all_lines'], ensure_ascii=False))


if __name__ == '__main__':
    main()
