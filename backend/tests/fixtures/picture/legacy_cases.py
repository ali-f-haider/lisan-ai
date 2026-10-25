"""Deterministic old-input generator; captured before adding a picture voter."""
import random

def cases():
    rng=random.Random(18182026)
    for i in range(6000):
        n=rng.randrange(1,35)
        rows=[dict(segment_id=f'r{j}',speaker='Speaker '+str(rng.randrange(1,5))) for j in range(n)]
        if i%4==0:rows[rng.randrange(n)]['manual_speaker']=True
        voters={}
        for name in ('dialogue','audio','voice','app'):
            if rng.random()<.17:voters[name]=None;continue
            if name=='app':
                if rng.random()<.2:voters[name]={r['segment_id']:r['speaker'] for r in rows}
                continue
            voters[name]={r['segment_id']:'ABCD'[rng.randrange(4)] for r in rows if rng.random()>(.5 if name=='voice' else .15)}
            if i%31==0 and name=='voice':voters[name][rows[0]['segment_id']]=None
        options=dict(audio_weight=rng.randrange(1,4),allow_new=bool(rng.randrange(2)),min_new_lines=rng.randrange(1,7),
                     max_new_speakers=rng.randrange(4),min_support=rng.randrange(1,5))
        if i%53==0:options['min_support']=5  # legacy-invalid must stay invalid
        yield rows,voters,options
