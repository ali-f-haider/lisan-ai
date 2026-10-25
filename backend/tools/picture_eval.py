"""Compare anonymous saved picture advice with existing speaker votes.

Offline by default. --live explicitly sends a video and timed source text using
an environment key. Truth is used ONLY for scoring, never the request or vote.
"""
import argparse
from collections import Counter
import copy
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
import image_unit
import speaker_vote
from tools.speaker_eval import optimal_mapping
from tools.speaker_vote_eval import build_voters


def _metrics(rows, labels, badges=None):
    truth={r['segment_id']:r['reference'] for r in rows if isinstance(r.get('reference'),str) and r['reference'] not in ('','?')}
    predicted={k:v for k,v in labels.items() if k in truth and isinstance(v,str)}
    weights=Counter((v,truth[k]) for k,v in predicted.items())
    mapping=optimal_mapping(weights,set(predicted.values()),set(truth.values()))
    correct={k for k,v in predicted.items() if mapping.get(v)==truth[k]}
    answered=len(predicted);total=len(truth)
    return {'labelled':total,'answered':answered,'right':len(correct),'wrong':answered-len(correct),'unanswered':total-answered,
            'check_speaker':sum(bool((badges or {}).get(sid)) for sid in truth),
            'speakers':len(set(predicted.values())),'accuracy_confident_or_answered':len(correct)/answered if answered else None,
            'accuracy_all_labelled':len(correct)/total if total else None,'mapping':mapping,'right_ids':sorted(correct)}


def evaluate_clip(data, picture=None, picture_weight=1):
    if not isinstance(data,dict) or not isinstance(data.get('lines'),list) or not data['lines']:
        raise ValueError('Provide a non-empty lines list with app labels and reference speakers.')
    rows=data['lines'];ids=[r.get('segment_id') for r in rows if isinstance(r,dict)]
    if len(ids)!=len(rows) or any(not isinstance(s,str) or not s for s in ids) or len(set(ids))!=len(ids):
        raise ValueError('Every line needs a unique string segment_id.')
    voters,_=build_voters(rows)
    frame=voters.get('dialogue') or {}
    clean=[dict(segment_id=r['segment_id'],start=r['start'],end=r['end'],text=r.get('text',''),speaker=r['app']) for r in rows]
    if not image_unit._rows(clean):
        raise ValueError('Every line needs finite start/end timings and an app label.')
    source=picture if isinstance(picture,dict) else data.get('picture') or {}
    # Sanitize saved answers too; fixtures/sidecars are data, not trusted voters.
    chunks=[]
    for c in image_unit._chunks(source):
        offset=image_unit._number(c.get('offset'),0.);length=image_unit._number(c.get('duration'),600.)
        wanted=[r['segment_id'] for r in clean if offset <= (r['start']+r['end'])/2 < offset+length]
        answer=copy.deepcopy(c.get('answer'))
        if isinstance(answer,dict):
            for item in answer.get('screen_text',[]):
                if isinstance(item,dict) and image_unit._number(item.get('t')) is not None:
                    item['t']-=offset
        parsed=image_unit.parse_answer(answer,wanted,length)
        if parsed is not None:
            for item in parsed['screen_text']:item['t']+=offset
            chunks.append(dict(c,answer=parsed))
    picture_votes=image_unit.votes_for_vote({'chunks':chunks},clean,frame)
    app=[dict(segment_id=r['segment_id'],speaker=r['app'],**({'manual_speaker':r['manual_speaker']} if 'manual_speaker' in r else {})) for r in rows]
    runs={}
    for name in speaker_vote.CORE_VOTERS:
        labels={r['segment_id']:r['app'] for r in rows} if name=='app' else voters.get(name) or {}
        runs[name+'_alone']=_metrics(rows,labels)
    runs['picture_alone']=_metrics(rows,picture_votes)
    predictions={}
    for name,extra in [('without_picture',{}),('with_picture',{'picture':picture_votes})]:
        result=speaker_vote.decide(app,dict(voters,**extra),speaker_vote.Policy(picture_weight=picture_weight))
        labels={r['segment_id']:r['speaker'] for r in result['lines']}
        runs[name]=_metrics(rows,labels,{r['segment_id']:r['badge'] for r in result['lines']})
        predictions[name]=labels
    confident=[r for r in rows if isinstance(picture_votes.get(r['segment_id']),str) and isinstance(r.get('reference'),str) and r['reference'] not in ('','?')]
    # Use each voter's FULL-RUN identity mapping on exactly the same line set.
    picture_map=runs['picture_alone']['mapping'];audio_map=runs['audio_alone']['mapping']
    p_right=sum(picture_map.get(picture_votes[r['segment_id']])==r['reference'] for r in confident)
    audio=voters.get('audio') or {}
    a_right=sum(audio_map.get(audio.get(r['segment_id']))==r['reference'] for r in confident)
    n=len(confident);covered=sum(r['segment_id'] in audio for r in confident)
    weight=2 if n and covered and p_right>=a_right else 1
    before=set(runs['without_picture']['right_ids']);after=set(runs['with_picture']['right_ids'])
    return {'basis':'Measured on supplied labels only; not held-out performance or proof of identity.', 'runs':runs,
            'comparison':{'fixed':sorted(after-before),'broken':sorted(before-after),'net_correct_change':len(after)-len(before)},
            'confident_comparison':{'lines':n,'picture_right':p_right,'audio_right':a_right,'audio_answered':covered,
                                   'picture_accuracy':p_right/n if n else None,'audio_accuracy':a_right/n if n else None},
            'recommended_picture_weight':weight,
            'recommendation_reason':'Picture is at least as accurate as audio on the same confident labelled lines.' if weight==2 else 'No comparable labelled confident evidence, or audio is more accurate on those lines.',
            'recommendation_limit':'Diagnostic recommendation only; small/shared evaluation clips do not establish general accuracy. Missing audio on the subset is reported, not concealed.',
            'picture_weight_evaluated':picture_weight,'predictions':predictions,
            'cost':{'picture_saved_usd':image_unit._number((source.get('usage') or {}).get('usd'),None),
                    'picture_recorded_charged_usd':image_unit._number(source.get('charged_usd'),None),
                    'existing_speaker_usd':image_unit._number((data.get('speaker_vote_usage') or {}).get('usd'),None)}}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',type=Path);p.add_argument('--picture',type=Path)
    p.add_argument('--dry',action='store_true');p.add_argument('--live',action='store_true')
    p.add_argument('--video',type=Path);p.add_argument('--work-dir',type=Path)
    p.add_argument('--source-language',default='en');p.add_argument('--people',type=int)
    p.add_argument('--picture-weight',type=int,choices=(1,2),default=1)
    p.add_argument('--json',action='store_true');p.add_argument('--report',type=Path)
    a=p.parse_args(argv)
    if a.dry and a.live:p.error('Choose dry or live, not both.')
    try:
        data=json.loads(a.input.read_text(encoding='utf-8-sig'))
        picture=json.loads(a.picture.read_text(encoding='utf-8-sig')) if a.picture else None
        if a.live:
            key=os.environ.get('GEMINI_API_KEY','')
            if not key or not a.video or not a.work_dir:
                p.error('Live mode requires video, work-dir and the existing secure environment key.')
            rows=[dict(r,speaker=r['app']) for r in data['lines']]
            picture=image_unit.analyse('picture-evaluation',rows,a.video,a.work_dir,key,source_language=a.source_language,people=a.people)
        result=evaluate_clip(data,picture,a.picture_weight)
        result['mode']='live' if a.live else 'dry'
        result['cost']['picture_charged_this_run_usd']=picture.get('charged_usd',0.) if a.live else 0.
        result['fixture_origin']=data.get('origin')
    except (OSError,ValueError,TypeError,KeyError) as e:
        p.error(str(e))
    if a.report:a.report.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    if a.json:
        print(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2));return 0
    print('Run | Right | Wrong | Unanswered | Check speaker | Speakers')
    for name,r in result['runs'].items():print(f"{name} | {r['right']} | {r['wrong']} | {r['unanswered']} | {r['check_speaker']} | {r['speakers']}")
    print('Same confident lines:',json.dumps(result['confident_comparison']))
    print('Recommended picture weight:',result['recommended_picture_weight'],result['recommendation_reason'])
    print('Fixed / broken:',len(result['comparison']['fixed']),len(result['comparison']['broken']))
    print('Cost (admin/developer only):',json.dumps(result['cost']))
    print('More correct lines with few broken ones is help; fewer correct lines, extra speakers or confident mistakes is harm. A lower badge count alone proves nothing.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
