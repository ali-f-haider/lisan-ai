import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

import image_unit as I
import speaker_trace
import speaker_vote as V
from tools import picture_eval

FIX=Path(__file__).parent/'fixtures/picture'
def load(n):return json.loads((FIX/n).read_text(encoding='utf-8'))


class PictureVoteTests(unittest.TestCase):
    def setUp(self):
        all_cases=json.loads((FIX.parent/'speaker_vote_cases.json').read_text())['cases']
        self.case=copy.deepcopy(next(c for c in all_cases if c['name']=='app_wrong_three_voters_right'))
        from test_speaker_vote import inputs
        self.rows,self.voters=inputs(self.case)

    def test_picture_is_sparse_and_none_is_removed_without_coverage_requirement(self):
        sid=self.rows[-1]['segment_id'];frame=self.voters['dialogue']
        voters=dict(self.voters,picture={sid:frame[sid],self.rows[0]['segment_id']:None})
        result=V.decide(self.rows,voters)
        self.assertIn('picture',result['summary']['voters_used'])
        row=result['lines'][-1]
        self.assertIn('picture',row['voters_for']);self.assertEqual(row['voters_present'],5)
        self.assertEqual(result['lines'][0]['voters_present'],4)

    def test_picture_counts_once_or_twice_and_dissent_alone_does_not_badge(self):
        # Most rows establish stable identities. On the target, audio B has
        # weight2; dialogue A + app A + picture B produce 3 vs2 (weight1),
        # or 4 vs2 (weight2). Another target is balanced to expose weight2.
        rows=[dict(segment_id=str(i),speaker='Speaker 1' if i<5 else 'Speaker 2') for i in range(10)]
        frame={str(i):'A' if i<5 else 'B' for i in range(10)}
        audio=dict(frame);audio['0']='B'
        voice=dict(frame);voice['0']='B'
        picture={'0':'A'}
        votes=dict(dialogue=frame,audio=audio,voice=voice,picture=picture)
        one=V.decide(rows,votes,V.Policy(picture_weight=1))['lines'][0]
        two=V.decide(rows,votes,V.Policy(picture_weight=2))['lines'][0]
        self.assertEqual(one['speaker'],'Speaker 2')  # A3/B3 tie: audio
        self.assertEqual(two['speaker'],'Speaker 1')  # A4/B3
        self.assertIn('picture_disagrees',one['reasons'])
        unanimous=dict(dialogue=frame,audio=frame,voice=frame,picture={'0':'B'})
        out=V.decide(rows,unanimous)['lines'][0]
        self.assertIn('picture_disagrees',out['reasons']);self.assertFalse(out['badge'])
        self.assertEqual(out['support'],4)

    def test_audio_then_dialogue_then_picture_tie_order_preserves_existing_rules(self):
        rows=[dict(segment_id=str(i),speaker='Speaker 1' if i<5 else 'Speaker 2') for i in range(10)]
        frame={str(i):'A' if i<5 else 'B' for i in range(10)}
        audio=dict(frame);audio['0']='B'
        # A:dialogue/app =2, B:audio=2 -> audio wins with legacy reason.
        out=V.decide(rows,dict(dialogue=frame,audio=audio,picture={'0':'B'}),V.Policy(picture_weight=1,audio_weight=1))['lines'][0]
        self.assertEqual(out['speaker'],'Speaker 2');self.assertIn('tie_broken_by_audio',out['reasons'])
        voters=dict(dialogue=frame,audio=None,voice={str(i):frame[str(i)] for i in range(10)},picture={'0':'B'})
        voters['voice']['0']='B'
        out=V.decide(rows,voters)['lines'][0]
        self.assertEqual(out['speaker'],'Speaker 1');self.assertIn('tie_broken_by_dialogue',out['reasons'])
        # No audio or dialogue label is tied for top. Picture vs a new voice
        # group ties; only picture is preferred to the deterministic label sort.
        # App B + voice B tie picture C (weight2); dialogue A is below both.
        three=[dict(segment_id=str(i),speaker='Speaker '+str(i//5+1)) for i in range(15)]
        f={str(i):chr(65+i//5) for i in range(15)}
        voice=dict(f);voice['0']='B'
        three[0]['speaker']='Speaker 2'
        voters=dict(dialogue=f,audio=None,voice=voice,picture={'0':'C'})
        out=V.decide(three,voters,V.Policy(picture_weight=2))['lines'][0]
        self.assertEqual(out['speaker'],'Speaker 3')
        self.assertIn('tie_broken_by_picture',out['reasons'])

    def test_invalid_picture_weights_and_existing_only_policy(self):
        for w in [0,3,True,1.5,float('nan')]:
            self.assertEqual(V.decide(self.rows,self.voters,{'picture_weight':w})['summary']['status'],'invalid_input')
        voters=dict(self.voters,picture={r['segment_id']:'new_visual_only' for r in self.rows})
        result=V.decide(self.rows,voters,{'picture_weight':2,'allow_new':False})
        self.assertEqual(result['summary']['new_speakers'],[])
        locked=copy.deepcopy(self.rows);locked[-1]['manual_speaker']=True
        out=V.decide(locked,voters)['lines'][-1]
        self.assertFalse(out['changed'] or out['badge'])

    def test_chunk_local_alignment_is_preserved_not_globally_redone(self):
        rows=load('rows.json');second=[dict(r,segment_id='other_'+r['segment_id'],start=r['start']+32,end=r['end']+32) for r in rows]
        result=load('clashing_chunks.json')
        frame={r['segment_id']:'A' if i<4 else 'B' for i,r in enumerate(rows)}
        frame.update({r['segment_id']:'C' if i<4 else 'D' for i,r in enumerate(second)})
        out=I.votes_for_vote(result,rows+second,frame)
        self.assertEqual(out['l0'],'A');self.assertEqual(out['other_l0'],'C')
        missing=I.votes_for_vote(result,rows+second,{})
        self.assertEqual(missing['l0'],'p0_A');self.assertEqual(missing['other_l0'],'p1_A')
        # An already aligned minority label must remain itself at decide().
        v={r['segment_id']:frame[r['segment_id']] for r in rows+second}
        app=[dict(segment_id=r['segment_id'],speaker='Speaker 1' if v[r['segment_id']]=='A' else 'Speaker 2') for r in rows+second]
        voted=V.decide(app,dict(dialogue=v,audio=v,picture=out))
        self.assertIn('picture',voted['summary']['voters_used'])

    def test_trace_name_map_is_extended_only(self):
        self.assertEqual(speaker_trace._VOTERS['picture'],'picture')


class LegacyAndEvaluationTests(unittest.TestCase):
    def test_6000_generated_old_inputs_have_identical_complete_outputs(self):
        spec=importlib.util.spec_from_file_location('picture_legacy_cases',FIX/'legacy_cases.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        expected=load('legacy-output-hashes.json');self.assertEqual(expected['cases'],6000)
        for i,((rows,voters,policy),wanted) in enumerate(zip(m.cases(),expected['hashes'])):
            result=V.decide(rows,voters,policy)
            actual=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            self.assertEqual(actual,wanted,i)

    def test_dry_eval_truth_never_enters_predictions_and_recommendation_is_explicit(self):
        data=load('evaluation.json');saved=copy.deepcopy(data)
        report=picture_eval.evaluate_clip(data)
        self.assertEqual(data,saved);self.assertEqual(report['runs']['without_picture']['speakers'],7)
        self.assertEqual(report['recommended_picture_weight'],2)
        self.assertEqual(report['confident_comparison']['lines'],46)
        for r in data['lines']:r['reference']='Different truth'
        altered=picture_eval.evaluate_clip(data)
        self.assertEqual(report['predictions'],altered['predictions'])
        bad=copy.deepcopy(saved)
        for r in bad['picture']['chunks'][0]['answer']['lines']:
            if r['id']=='e01':r['speaker']='G'
        hurt=picture_eval.evaluate_clip(bad)
        self.assertEqual(hurt['recommended_picture_weight'],1)
        self.assertGreater(hurt['confident_comparison']['audio_right'],hurt['confident_comparison']['picture_right'])


if __name__=='__main__':unittest.main()
