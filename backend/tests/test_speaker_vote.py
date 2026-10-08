import copy
from itertools import combinations
import json
import math
from pathlib import Path
import unittest

import numpy as np

from speaker_vote import (MAX_LABEL, VOTERS, Policy, align_labels, choose_k,
                          cluster_voices, decide, parse_audio_answer, parse_dialogue_answer)


CASES = json.loads((Path(__file__).parent/'fixtures/speaker_vote_cases.json').read_text())['cases']


def inputs(case):
    lines = [dict(segment_id=r['segment_id'], speaker=r['app'],
                  **({'manual_speaker':r['manual_speaker']} if 'manual_speaker' in r else {})) for r in case['lines']]
    voters = {name:{r['segment_id']:r[key] for r in case['lines'] if r.get(key) is not None}
              for name,key in (('dialogue','dialogue'),('audio','audio'),('voice','voice_cluster'))}
    for name in case.get('drop', []):
        voters[name] = None
    return lines,voters


class AnswerTests(unittest.TestCase):
    def answer(self, n=10):
        return dict(people=2, lines=[dict(id=f'l{i}', speaker='A' if i%2 else 'B') for i in range(n)])

    def test_both_parsers_accept_objects_json_and_fenced_json(self):
        ids = [f'l{i}' for i in range(10)]
        raw = self.answer()
        for parser in (parse_dialogue_answer, parse_audio_answer):
            expected = {r['id']:r['speaker'] for r in raw['lines']}
            for value in (raw, json.dumps(raw), '```json\n'+json.dumps(raw)+'\n```'):
                self.assertEqual(parser(value,ids),expected)

    def test_exact_eighty_percent_is_accepted_unknown_ids_ignored(self):
        answer = self.answer(8)
        answer['lines'].append(dict(id='outside',speaker=None))
        self.assertEqual(len(parse_dialogue_answer(answer,[f'l{i}' for i in range(10)])),8)
        self.assertIsNone(parse_dialogue_answer(self.answer(7),[f'l{i}' for i in range(10)]))

    def test_duplicate_id_drops_whole_answer(self):
        answer = self.answer()
        answer['lines'].append(dict(answer['lines'][0]))
        self.assertIsNone(parse_audio_answer(answer,[f'l{i}' for i in range(10)]))

    def test_labels_must_be_short_printable_strings(self):
        for label in (None, 1, False, float('nan'), [], {}, '', ' ', ' A', 'A\nB', 'x'*(MAX_LABEL+1)):
            answer = self.answer()
            answer['lines'][0]['speaker'] = label
            self.assertIsNone(parse_dialogue_answer(answer,[f'l{i}' for i in range(10)]))

    def test_malformed_answers_people_and_ids_never_raise(self):
        for raw in (None,[],3,'broken','{"people":NaN}',dict(people=True,lines=[]),dict(people=1,lines=[None]),dict(people=1,lines=[dict(id=[],speaker='A')])):
            self.assertIsNone(parse_audio_answer(raw,['l0']))
        for ids in (None,[],['x','x'],[''],[float('nan')]):
            self.assertIsNone(parse_dialogue_answer(self.answer(),ids))
        answer = self.answer()
        answer['people'] = 1
        self.assertIsNone(parse_audio_answer(answer,[f'l{i}' for i in range(10)]))


class AlignmentTests(unittest.TestCase):
    def test_greedy_is_one_to_one_positive_overlap_only(self):
        frame = {'1':'A','2':'A','3':'B','4':'B'}
        voter = {'name':'audio','labels':{'1':'Q','2':'Q','3':'Q','4':'R','5':'S'}}
        self.assertEqual(align_labels(voter,frame),{'1':'A','2':'A','3':'A','4':'B','5':'x_audio_S'})

    def test_equal_counts_sort_source_then_frame_label(self):
        frame = {'1':'A','2':'B','3':'A','4':'B'}
        out = align_labels({'name':'voice','labels':{'1':'Y','2':'X','3':'X','4':'Y'}},frame)
        self.assertEqual(out,{'1':'B','2':'A','3':'A','4':'B'})

    def test_namespaces_do_not_collide_with_frame_or_other_voters(self):
        frame = {'1':'x_audio_Z'}
        audio = align_labels({'name':'audio','labels':{'1':'Q','2':'Z'}},frame)
        voice = align_labels({'name':'voice','labels':{'1':'Q','2':'Z'}},frame)
        self.assertEqual(audio['2'],'x_audio_Z_')
        self.assertNotEqual(audio['2'],voice['2'])
        self.assertNotEqual(audio['2'],frame['1'])

    def test_line_id_named_labels_is_valid(self):
        self.assertEqual(align_labels({'labels':'X'},{'labels':'A'}),{'labels':'A'})

    def test_invalid_mapping_is_empty_and_inputs_unchanged(self):
        source = {'name':'audio','labels':{'1':'Q'}}
        saved = copy.deepcopy(source)
        align_labels(source,{'1':'A'})
        self.assertEqual(source,saved)
        for bad in (None,[],{'1':float('nan')}):
            self.assertEqual(align_labels(bad,{'1':'A'}),{})


class ClusteringTests(unittest.TestCase):
    def test_separated_directions_and_missing_vectors(self):
        values = [[1,.01],[1,-.01],[-1,.01],[-1,-.01],None]
        groups = cluster_voices(values,2)
        self.assertEqual(groups[0],groups[1])
        self.assertEqual(groups[2],groups[3])
        self.assertNotEqual(groups[0],groups[2])
        self.assertIsNone(groups[4])

    def test_normalization_repeat_and_reorder_are_deterministic(self):
        values = [[1,.1],[2,.2],[-1,.1],[-2,.2],[.1,1]]
        first = cluster_voices(values,3)
        self.assertEqual(first,cluster_voices(values,3))
        self.assertEqual(first,list(reversed(cluster_voices(list(reversed(values)),3))))
        self.assertEqual(first[0],first[1])

    def test_missing_nan_zero_malformed_and_mixed_dimensions_cast_no_vote(self):
        values = [[1,0],[0,1],None,[0,0],[float('nan'),1],[math.inf,0],[],[1,0,0],'bad',{}]
        labels = cluster_voices(values,2)
        self.assertTrue(all(label is None for label in labels[2:]))
        self.assertTrue(all(label is not None for label in labels[:2]))

    def test_single_direction_large_k_and_antipodal_data_are_finite(self):
        self.assertEqual(cluster_voices([[1,0],[2,0]],99),['v0','v0'])
        self.assertTrue(all(isinstance(s,str) for s in cluster_voices([[1,0],[-1,0]],1)))
        self.assertTrue(all(isinstance(s,str) for s in cluster_voices([[1e308,1e308],[-1e308,1e308]],2)))

    def test_invalid_parameters_and_all_missing(self):
        for k in (None,True,float('nan'),0,-1):
            self.assertEqual(cluster_voices([[1,0],None],k),[None,None])
        self.assertEqual(cluster_voices([None,None],2),[None,None])
        self.assertEqual(cluster_voices(None,2),[])

    def test_does_not_mutate_arrays_or_use_global_random_state(self):
        array = np.array([1.,.2])
        saved = array.copy()
        before = np.random.get_state()
        cluster_voices([array,[-1,0]],2)
        after = np.random.get_state()
        np.testing.assert_array_equal(array,saved)
        self.assertEqual(before[0],after[0])
        np.testing.assert_array_equal(before[1],after[1])
        self.assertEqual(before[2:],after[2:])

    def test_choose_k_counts_labels_not_names(self):
        labels = ['X']*5+['Y']*4+['Z']*8+[None]
        self.assertEqual(choose_k(labels),2)
        self.assertEqual(choose_k(dict(enumerate(labels))),2)
        self.assertEqual(choose_k(['other' if v=='X' else v for v in labels]),2)
        self.assertEqual(choose_k(None),0)
        self.assertEqual(choose_k(labels,min_lines=True),0)


class VotingTests(unittest.TestCase):
    def case(self,name):
        return copy.deepcopy(next(c for c in CASES if c['name']==name))

    def run_case(self,name,policy=None):
        lines,voters = inputs(self.case(name))
        return decide(lines,voters,policy)

    def test_app_right_with_fewer_than_half_disagree_is_unchanged_on_every_fixture(self):
        for case in CASES:
            lines,voters = inputs(case)
            out = decide(lines,voters)
            predictions = {r['segment_id']:r for r in out['lines']}
            frame = voters.get('dialogue')
            if not frame:
                self.assertFalse(any(r['changed'] for r in out['lines']))
                continue
            app = {r['segment_id']:r['speaker'] for r in lines}
            aligned_app = align_labels({'name':'app','labels':app},frame)
            aligned = {name:align_labels({'name':name,'labels':mapping},frame) if name!='dialogue' else frame
                       for name,mapping in voters.items() if mapping is not None and name!='app'}
            if voters.get('app','enabled') is not None:
                aligned['app'] = aligned_app
            for row in case['lines']:
                sid = row['segment_id']
                votes = [mapping[sid] for mapping in aligned.values() if sid in mapping]
                disagree = sum(v != aligned_app[sid] for v in votes)
                if row['app']==row['reference'] and disagree*2 < len(votes):
                    self.assertEqual(predictions[sid]['speaker'],row['app'],(case['name'],sid))

    def test_one_and_two_speaker_videos_unchanged(self):
        for name in ('one_speaker','two_speakers','app_right_one_voter_wrong'):
            out = self.run_case(name)
            self.assertFalse(any(r['changed'] for r in out['lines']))

    def test_three_agree_corrects_app_but_shared_wrong_votes_can_break_it(self):
        fixed = self.run_case('app_wrong_three_voters_right')['lines'][-1]
        broken = self.run_case('all_three_voters_wrong_together')['lines'][-1]
        self.assertEqual(fixed['speaker'],'Speaker 2')
        self.assertEqual(broken['speaker'],'Speaker 2')
        self.assertEqual(fixed['support'],3)
        self.assertFalse(fixed['badge'])

    def test_all_four_wrong_together_remain_wrong_without_a_badge(self):
        row = self.run_case('all_four_voters_wrong_together')['lines'][-1]
        self.assertEqual(row['speaker'],'Speaker 1')
        self.assertFalse(row['changed'] or row['badge'])
        self.assertEqual(row['support'],4)

    def test_missing_each_voter_and_every_pair(self):
        for missing in VOTERS:
            result = self.run_case('without_'+missing)
            self.assertEqual(result['lines'][-1]['changed'],missing!='dialogue')
            self.assertNotIn(missing,result['summary']['voters_used'])
        for pair in combinations(VOTERS,2):
            out = self.run_case('without_'+'_and_'.join(pair))
            self.assertFalse(any(r['changed'] or r['badge'] for r in out['lines']))

    def test_arbitrary_labels_do_not_change_the_decision(self):
        normal = self.run_case('app_wrong_three_voters_right')
        permuted = self.run_case('arbitrary_permuted_letters')
        self.assertEqual(normal['lines'],permuted['lines'])

    def test_tie_goes_to_dialogue_and_carries_badge(self):
        row = self.run_case('tie_dialogue_wins')['lines'][-1]
        self.assertEqual(row['speaker'],'Speaker 2')
        self.assertEqual(row['support'],2)
        self.assertEqual(row['voters_present'],4)
        self.assertTrue(row['badge'])
        self.assertIn('tie_broken_by_dialogue',row['reasons'])

    def test_rejected_tiny_candidate_keeps_app_and_badges_final_label_support(self):
        out = self.run_case('phantom_two_lines')
        self.assertEqual(out['summary']['new_speakers'],[])
        for row in out['lines'][-2:]:
            self.assertEqual(row['speaker'],'Speaker 1')
            self.assertEqual(row['support'],1)
            self.assertEqual(row['voters_for'],['app'])
            self.assertTrue(row['badge'])
            self.assertIn('candidate_rejected',row['reasons'])

    def test_supported_new_person_is_named_and_counted(self):
        out = self.run_case('new_speaker_supported')
        self.assertEqual(out['summary']['new_speakers'],[dict(label='Speaker 3',lines=6)])
        self.assertTrue(all(r['source']=='new' and r['support']==3 for r in out['lines'][-6:]))

    def test_one_line_real_speaker_is_rejected_by_size_policy(self):
        row = self.run_case('real_but_tiny_speaker')['lines'][-1]
        self.assertEqual(row['speaker'],'Speaker 1')
        self.assertTrue(row['badge'])
        self.assertIn('candidate_rejected',row['reasons'])

    def test_locked_lines_cannot_qualify_a_new_candidate(self):
        lines,voters = inputs(self.case('new_speaker_supported'))
        for row in lines[-2:]:
            row['manual_speaker'] = True
        out = decide(lines,voters)
        self.assertEqual(out['summary']['new_speakers'],[])
        self.assertFalse(any(r['changed'] for r in out['lines']))

    def test_cap_and_first_appearance_order(self):
        out = self.run_case('candidate_cap')
        self.assertEqual(out['summary']['new_speakers'],[dict(label='Speaker 3',lines=6),dict(label='Speaker 4',lines=6)])
        self.assertEqual(out['summary']['dropped_small'][-1]['reason'],'new_speaker_limit')
        self.assertTrue(all(r['speaker']=='Speaker 1' and r['badge'] for r in out['lines'][-6:]))

    def test_manual_locks_never_change_or_badge(self):
        row = self.run_case('manual_locked')['lines'][-1]
        self.assertFalse(row['changed'] or row['badge'])
        self.assertIn('manual_choice',row['reasons'])

    def test_strict_and_existing_only_policies(self):
        strict = self.run_case('tie_dialogue_wins',Policy(min_support=3))
        self.assertFalse(strict['lines'][-1]['changed'])
        old = self.run_case('new_speaker_supported',Policy(allow_new=False))
        self.assertEqual(old['summary']['new_speakers'],[])
        self.assertFalse(any(r['changed'] for r in old['lines']))

    def test_sparse_voice_casts_no_vote_on_missing_lines(self):
        lines,voters = inputs(self.case('app_wrong_three_voters_right'))
        voters['audio'] = None
        voters['voice'] = {lines[-1]['segment_id']:'v2',lines[0]['segment_id']:'v1'}
        out = decide(lines,voters)
        self.assertEqual(out['lines'][-1]['speaker'],'Speaker 2')
        self.assertFalse(out['lines'][1]['changed'] or out['lines'][1]['badge'])
        self.assertIn('insufficient_line_voters',out['lines'][1]['reasons'])

    def test_missing_dialogue_on_one_line_is_fail_closed_even_with_other_three(self):
        lines,voters = inputs(self.case('app_wrong_three_voters_right'))
        del voters['dialogue'][lines[-1]['segment_id']]
        row = decide(lines,voters)['lines'][-1]
        self.assertFalse(row['changed'] or row['badge'])

    def test_new_candidate_needs_distinct_supported_lines_not_sum_of_votes(self):
        lines,voters = inputs(self.case('new_speaker_supported'))
        # A four-way tie gives dialogue the candidate on the other four lines.
        for row in lines[-4:]:
            sid = row['segment_id']
            voters['audio'][sid] = 'unmatched_audio'
            voters['voice'][sid] = 'unmatched_voice'
        out = decide(lines,voters)
        self.assertEqual(out['summary']['new_speakers'],[])
        self.assertTrue(all(r['speaker']=='Speaker 1' for r in out['lines'][-6:]))

    def test_duplicate_or_malformed_lines_abort_without_partial_result(self):
        good = dict(segment_id='x',speaker='Speaker 1')
        for lines in (None,[good,dict(good)],[good,None],[good,dict(segment_id='y',speaker=float('nan'))],[dict(good,manual_speaker=None)]):
            out = decide(lines,{})
            self.assertEqual(out['lines'],[])
            self.assertEqual(out['summary']['status'],'invalid_lines')
            json.dumps(out,allow_nan=False)
        self.assertEqual(decide([], {})['lines'],[])

    def test_bad_voters_policies_and_nonfinite_values_never_raise(self):
        lines,voters = inputs(self.case('two_speakers'))
        for bad in (None,float('nan'),[],{'line_0':float('nan')},{'line_0':{}}):
            variant = dict(voters,dialogue=bad)
            out = decide(lines,variant)
            self.assertFalse(any(r['changed'] or r['badge'] for r in out['lines']))
            json.dumps(out,allow_nan=False)
        for policy in ({'min_new_lines':float('nan')},{'max_new_speakers':-1},{'bogus':1},False):
            self.assertFalse(any(r['changed'] for r in decide(lines,voters,policy)['lines']))

    def test_repeat_no_input_mutation_and_dictionary_order_independence(self):
        lines,voters = inputs(self.case('candidate_cap'))
        saved = copy.deepcopy((lines,voters))
        first = decide(lines,voters)
        self.assertEqual(first,decide(lines,voters))
        self.assertEqual(first,decide(lines,dict(reversed(list(voters.items())))))
        self.assertEqual((lines,voters),saved)

    def test_app_override_cannot_inject_an_extra_voter(self):
        lines,voters = inputs(self.case('two_speakers'))
        voters['app'] = {r['segment_id']:'fake' for r in lines}
        out = decide(lines,voters)
        self.assertNotIn('app',out['summary']['voters_used'])

    def test_existing_customer_names_up_to_forty_characters(self):
        lines = [dict(segment_id=str(i),speaker='N'*40) for i in range(5)]
        voters = {n:{str(i):'A' for i in range(5)} for n in ('dialogue','audio','voice')}
        self.assertTrue(all(r['speaker']=='N'*40 for r in decide(lines,voters)['lines']))


if __name__ == '__main__':
    unittest.main()
