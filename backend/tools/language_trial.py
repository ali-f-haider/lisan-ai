"""Offline gold-screen validation and explicitly requested live text trials.

No key or transport is loaded in --dry. Live prices must be supplied by the
operator; reported costs cover responses with returned usage, not hidden retries.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
import languages
import language_prompts

TRIAL_LANGUAGES = languages.NEW_LANGUAGES + languages.STAGED_LANGUAGES

_TASHKEEL=re.compile('[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]')


def evaluate(arabic,checks):
    results=[]
    for check in checks:
        text=_TASHKEEL.sub('',arabic) if check.get('mode')=='strip_tashkeel' else arabic
        matched=bool(re.search(check['pattern'],text))
        if check.get('must_not_match'):
            matched=not matched
        results.append({'pattern':check['pattern'],'passed':matched,'reason':check.get('reason','')})
    return {'passed':bool(results) and all(r['passed'] for r in results),'checks':results}


def request_translation(prompt,key):
    # Deliberate lazy import: no engine import and no network in dry/unit tests.
    from gemini_service import call_gemini
    return call_gemini(key,{'contents':[{'parts':[{'text':prompt}]}],
                         'generationConfig':{'maxOutputTokens':8192,'responseMimeType':'application/json'}},timeout=120)


def _usage(data):
    metadata=(data or {}).get('usageMetadata') or {}
    def count(key):
        value=metadata.get(key,0)
        return value if type(value) is int and value>=0 else 0
    return {'input':count('promptTokenCount'),'output':count('candidatesTokenCount'),
            'thoughts':count('thoughtsTokenCount'),'available':bool(metadata)}


def _answer(data,sid):
    parts=data['candidates'][0]['content']['parts']
    text=''.join(p.get('text','') for p in parts if not p.get('thought'))
    text=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip())
    answer=json.loads(text)
    if isinstance(answer,list):
        for row in answer:
            if isinstance(row,dict) and row.get('segment_id')==sid and isinstance(row.get('arabic_text'),str):
                return row['arabic_text']
    raise ValueError('No translation returned for the requested line.')


def run(codes,dry=False,limit=25,input_price=None,output_price=None,fixtures=None):
    if not dry:
        for value in (input_price,output_price):
            if type(value) not in (float,int) or not math.isfinite(value) or value<0:
                raise ValueError('Live trials require finite input/output prices per million tokens.')
        key=os.environ.get('GEMINI_API_KEY','')
        if not key:
            raise ValueError('Set GEMINI_API_KEY in the local environment before a live trial; do not paste it into chat.')
    gold=Path(fixtures) if fixtures else ROOT/'tests'/'fixtures'/'lang_gold'
    report={'mode':'dry' if dry else 'live','cases':[],'languages':{},
            'input_usd_per_million':input_price,'output_usd_per_million':output_price,
            'measurement':'dry screens do not measure translation quality',
            'cost_scope':'Returned usage only; thoughts priced as output. Missing usage/retry charges cannot be estimated.',
            'native_review':'All expectations require native review before using percentages as quality evidence.'}
    for code in codes:
        if code not in TRIAL_LANGUAGES:
            raise ValueError('Select es, fr, pt, it, de, nl or pl for a development text trial.')
        data=json.loads((gold/(code+'.json')).read_text(encoding='utf-8'))
        total={'prepared':0,'attempted':0,'passed':0,'failed':0,'input':0,'output':0,'thoughts':0,
               'reported_cost_usd':0.0,'missing_usage':0,'pass_percent':None}
        for case in data['cases'][:max(0,min(25,int(limit)))]:
            for check in case['checks']:
                re.compile(check['pattern'])
            sid=case['id']
            segment={'segment_id':sid,'start':0.0,'end':case['duration'],'text':case['text'],'speaker':'A'}
            gender={'speakers':{'A':case['speaker_gender']},'lines':{sid:{'speaker_gender':case['speaker_gender'],
                    'before':[{'speaker':'context','gender':'unknown','english':v} for v in case['context_lines']]}}}
            prompt=language_prompts.translation_prompt([segment],code,gender=gender)
            result={'id':sid,'language':code,'status':'DRY','prompt_characters':len(prompt),
                    'tokens':None,'reported_cost_usd':None,'checks':case['checks']}
            total['prepared']+=1
            if not dry:
                total['attempted']+=1
                started=time.monotonic()
                tokens=None
                try:
                    data,error=request_translation(prompt,key)
                    tokens=_usage(data)
                    result['tokens']=tokens
                    if not tokens['available']:
                        total['missing_usage']+=1
                    cost=(tokens['input']*input_price+(tokens['output']+tokens['thoughts'])*output_price)/1_000_000
                    result['reported_cost_usd']=cost if tokens['available'] else None
                    total['reported_cost_usd']+=cost
                    for name in ('input','output','thoughts'):
                        total[name]+=tokens[name]
                    if data is None:
                        raise ValueError('Translation request did not return a result.')
                    arabic=_answer(data,sid)
                    result.update(evaluate(arabic,case['checks']))
                    result['arabic_text']=arabic
                    result['status']='PASS' if result['passed'] else 'FAIL'
                except Exception as ex:
                    if tokens is None:
                        total['missing_usage']+=1
                    result.update(status='FAIL',passed=False,error=type(ex).__name__+': translation could not be evaluated')
                result['seconds']=round(time.monotonic()-started,3)
                total['passed' if result['passed'] else 'failed']+=1
            report['cases'].append(result)
        if total['attempted']:
            total['pass_percent']=100*total['passed']/total['attempted']
        report['languages'][code]=total
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description='Lisan AI source-language text trial; --dry makes no paid requests.')
    parser.add_argument('--languages',nargs='+',choices=TRIAL_LANGUAGES,default=list(languages.NEW_LANGUAGES))
    parser.add_argument('--dry',action='store_true')
    parser.add_argument('--limit',type=int,default=25)
    parser.add_argument('--input-usd-per-million',type=float)
    parser.add_argument('--output-usd-per-million',type=float)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args(argv)
    try:
        report=run(args.languages,args.dry,args.limit,args.input_usd_per_million,args.output_usd_per_million)
    except (ValueError,OSError,json.JSONDecodeError) as ex:
        parser.error(str(ex))
    for case in report['cases']:
        print(f"{case['language']} {case['id']}: {case['status']}")
    for code,total in report['languages'].items():
        percentage='not measured (dry)' if total['pass_percent'] is None else f"{total['pass_percent']:.1f}% regex screen passed"
        print(f"{code}: {percentage}; input={total['input']} output={total['output']} thoughts={total['thoughts']}; returned-usage cost=${total['reported_cost_usd']:.6f}; missing usage={total['missing_usage']}")
    if args.report:
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
