import copy
from itertools import combinations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from speaker_vote import VOTERS
from tools.speaker_vote_eval import build_voters, evaluate_clip


CASES = json.loads((Path(__file__).parent/'fixtures/speaker_vote_cases.json').read_text())['cases']


class VoteEvaluationTests(unittest.TestCase):
    def case(self,name='app_wrong_three_voters_right'):
        return copy.deepcopy(next(c for c in CASES if c['name']==name))

    def test_fixed_and_broken_are_reported_honestly(self):
        fixed = evaluate_clip(self.case())['runs']['all']['overall']
        broken = evaluate_clip(self.case('all_three_voters_wrong_together'))['runs']['all']['overall']
        self.assertEqual((fixed['changed'],fixed['fixed'],fixed['broken']),(1,1,0))
        self.assertEqual((broken['changed'],broken['fixed'],broken['broken']),(1,0,1))

    def test_all_single_and_pair_ablations_exist_and_weak_runs_do_nothing(self):
        runs = evaluate_clip(self.case())['runs']
        for name in VOTERS:
            self.assertIn('without_'+name,runs)
        for pair in combinations(VOTERS,2):
            run = runs['without_'+'_and_'.join(pair)]
            self.assertEqual(run['all_lines']['changed'],0)
            self.assertEqual(run['all_lines']['badged'],0)
        self.assertEqual(runs['without_dialogue']['all_lines']['changed'],0)

    def test_stricter_and_existing_only_comparisons(self):
        tied = evaluate_clip(self.case('tie_dialogue_wins'))['runs']
        self.assertEqual(tied['all']['overall']['fixed'],1)
        self.assertEqual(tied['require_three_votes']['overall']['fixed'],0)
        new = evaluate_clip(self.case('new_speaker_supported'))['runs']
        self.assertEqual(new['all']['summary']['new_speakers'],[dict(label='Speaker 3',lines=6)])
        self.assertEqual(new['existing_speakers_only']['summary']['new_speakers'],[])

    def test_pre_guard_diagnostic_exposes_tiny_speaker_tradeoff(self):
        runs = evaluate_clip(self.case('real_but_tiny_speaker'))['runs']
        self.assertEqual(runs['all']['overall']['fixed'],0)
        self.assertEqual(runs['before_candidate_size_and_cap']['overall']['fixed'],1)
        self.assertEqual(runs['all']['summary']['new_speakers'],[])

    def test_saved_clusters_are_used_without_fake_embedding_reconstruction(self):
        data = self.case()
        voters,metadata = build_voters(data['lines'])
        self.assertEqual(metadata['voice_source'],'saved_clusters_no_embeddings_available')
        self.assertEqual(metadata['choose_k'],2)
        self.assertEqual(voters['voice'][data['lines'][-1]['segment_id']],'v2')

    def test_supplied_embeddings_use_choose_k_and_clusterer(self):
        data = self.case()
        for row in data['lines']:
            row['embedding'] = [1.,.01] if row['voice_cluster']=='v1' else [-1.,.01]
            row['voice_cluster'] = 'ignored'
        voters,metadata = build_voters(data['lines'])
        self.assertEqual(metadata['voice_source'],'computed_from_supplied_embeddings')
        self.assertEqual(metadata['voice_groups'],2)
        self.assertNotEqual(voters['voice'][data['lines'][0]['segment_id']],voters['voice'][data['lines'][-1]['segment_id']])

    def test_reference_never_affects_decisions_and_input_is_unchanged(self):
        data = self.case('new_speaker_supported')
        saved = copy.deepcopy(data)
        first = evaluate_clip(data)
        self.assertEqual(data,saved)
        for row in data['lines']:
            row['reference'] = 'unrelated'
        second = evaluate_clip(data)
        for name in first['runs']:
            self.assertEqual(first['runs'][name]['summary'],second['runs'][name]['summary'])
            self.assertEqual(first['runs'][name]['all_lines']['changed'],second['runs'][name]['all_lines']['changed'])

    def test_sheets_share_overall_mapping_and_sum_to_overall_counts(self):
        data = self.case()
        for i,row in enumerate(data['lines']):
            row['sheet'] = 'first' if i%2 else 'second'
        run = evaluate_clip(data)['runs']['all']
        for key in ('labelled_lines','before_correct','after_correct','fixed','broken','badged','wrong_caught'):
            self.assertEqual(sum(sheet[key] for sheet in run['sheets'].values()),run['overall'][key])

    def test_unlabelled_changes_and_no_labels_do_not_get_accuracy_claims(self):
        data = self.case()
        data['lines'][-1]['reference'] = None
        run = evaluate_clip(data)['runs']['all']
        self.assertEqual(run['all_lines']['changed'],1)
        self.assertEqual(run['overall']['changed'],0)
        self.assertEqual(run['all_lines']['unknown_truth'],1)
        for row in data['lines']:
            row['reference'] = None
        result = evaluate_clip(data)
        self.assertIsNone(result['runs']['all']['overall']['after_accuracy'])
        json.dumps(result,allow_nan=False)

    def test_badge_counts_include_rejected_candidates_and_wrong_lines_caught(self):
        phantom = evaluate_clip(self.case('phantom_two_lines'))['runs']['all']
        self.assertEqual(phantom['all_lines']['badged'],2)
        self.assertEqual(phantom['overall']['wrong_caught'],0)
        self.assertEqual(phantom['summary']['dropped_small'][0]['lines'],2)

    def test_invalid_input_duplicates_and_reference_types_rejected(self):
        for data in (None,{},dict(lines=[]),dict(lines=[None]),dict(lines=[dict(segment_id='a',app='A',reference=3)])):
            with self.assertRaises(ValueError):
                evaluate_clip(data)
        data = self.case()
        data['lines'].append(dict(data['lines'][0]))
        with self.assertRaises(ValueError):
            evaluate_clip(data)

    def test_cli_reads_only_invented_json_and_returns_json(self):
        script = Path(__file__).resolve().parents[1]/'tools/speaker_vote_eval.py'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'invented.json'
            path.write_text(json.dumps(self.case()),encoding='utf-8')
            process = subprocess.run([sys.executable,str(script),str(path),'--json'],capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['runs']['all']['overall']['fixed'],1)


if __name__ == '__main__':
    unittest.main()
