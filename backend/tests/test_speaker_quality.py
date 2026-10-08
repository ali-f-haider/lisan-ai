import ast
import copy
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from speaker_quality import assess_rows, can_smooth_words, count_mismatch, TurnIndex
from tools.speaker_eval import evaluate

ROOT = Path(__file__).resolve().parents[1]


def speaker_helpers():
    # Load only pure speaker functions, without importing audio engines or config.
    tree = ast.parse((ROOT / 'whisper_service.py').read_text(encoding='utf-8-sig'))
    names = {'speaker_at_time', 'assign_speaker_for_segment', 'assign_speaker_by_words',
             'group_words_by_speaker', 'smooth_speaker_islands', 'chunk_words_by_duration', 'merge_mid_sentence_rows'}
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    scope = dict(re=re, SPEAKER_ISLAND_MAX_SEC=1.3, SPEAKER_ISLAND_MAX_WORDS=4, SPEAKER_ISLAND_GAP_SEC=.3)
    exec(compile(tree, 'whisper_service.py', 'exec'), scope)
    return scope


class SpeakerAssignmentTests(unittest.TestCase):
    def test_every_invented_case_does_not_regress(self):
        funcs = speaker_helpers()
        cases = json.loads((ROOT/'tests/fixtures/speaker_assignment.json').read_text())['cases']
        for c in cases:
            with self.subTest(case=c['name']):
                words = [SimpleNamespace(**w) for w in c['words']]
                groups = funcs['group_words_by_speaker'](words, c['turns'])
                found = {id(w): s for s, group in groups for w in group}
                labels = [found[id(w)] for w in words]
                self.assertEqual(labels, c['after_labels'])
                ref = {'turns': [dict(start=w.start,end=w.end,speaker=s) for w,s in zip(words,c['truth'])],
                       'lines': [dict(segment_id=str(i),speaker=s) for i,s in enumerate(c['truth'])]}
                def score(predicted):
                    return evaluate(ref, {'rows': [dict(segment_id=str(i),start=w.start,end=w.end,speaker=s)
                                                   for i,(w,s) in enumerate(zip(words,predicted))]})
                before, after = score(c['before_labels']), score(labels)
                self.assertLessEqual(after['der']['rate'], before['der']['rate'])
                self.assertGreaterEqual(after['line_accuracy'], before['line_accuracy'])
                if c['name'] == 'single_speaker':
                    self.assertEqual(labels, c['before_labels'])
                    self.assertEqual(groups, [('A', words)])

    def test_row_smoothing_preserves_overlap_and_invalid_duration(self):
        smooth = speaker_helpers()['smooth_speaker_islands']
        for start,end in [(.9,1.3), (1,1), (1,float('nan'))]:
            rows = [dict(start=0,end=1,speaker='A',text='we'),
                    dict(start=start,end=end,speaker='B',text='can'),
                    dict(start=1.3,end=2,speaker='A',text='continue')]
            self.assertEqual(smooth(rows)[1]['speaker'], 'B')

    def test_row_smoothing_still_removes_continuous_false_flip(self):
        rows=[dict(start=i,end=i+1,speaker=s,text=t) for i,(s,t) in enumerate(zip('ABA',['we','can','continue']))]
        self.assertEqual(speaker_helpers()['smooth_speaker_islands'](rows)[1]['speaker'],'A')

    def test_single_speaker_row_objects_are_unchanged(self):
        rows=[dict(start=i,end=i+1,speaker='A',text='continue') for i in range(4)]
        original=copy.deepcopy(rows)
        self.assertIs(speaker_helpers()['smooth_speaker_islands'](rows),rows)
        self.assertEqual(rows, original)

    def test_missing_word_times_and_empty_input_keep_existing_fallback(self):
        group=speaker_helpers()['group_words_by_speaker']
        self.assertEqual(group([SimpleNamespace(start=None,end=None,word='hi')],[dict(start=0,end=1,speaker='A')]),[])
        self.assertEqual(group([],[]),[])

    def test_guard_rejects_bad_times_and_long_or_punctuated_fragments(self):
        w=lambda a,b,t:dict(start=a,end=b,word=t)
        prev, nxt=[w(0,1,'we')],[w(1.2,2,'continue')]
        self.assertTrue(can_smooth_words(prev,[w(1,1.2,'can')],nxt))
        for fragment in [[w(1,1.2,'Okay.')],[w(1,3,'can')],[w(None,1.2,'can')]]:
            self.assertFalse(can_smooth_words(prev,fragment,nxt))


class SpeakerQualityTests(unittest.TestCase):
    def row(self, **kw):
        return dict(segment_id='s',start=0,end=3,speaker='A',text='We can continue with this task',
                    words=[dict(start=0,end=3,word='sentence')],**kw)

    def test_strong_evidence_high_and_inputs_unchanged(self):
        rows=[self.row()]; turns=[dict(start=0,end=3,speaker='raw')]
        original=copy.deepcopy((rows,turns))
        result=assess_rows(rows,turns,{'raw':'A'})['s']
        self.assertEqual(result['speaker_confidence'],'high')
        self.assertEqual(result['speaker_time_coverage'],1)
        self.assertEqual((rows,turns),original)

    def test_overlap_is_distinct_simultaneous_speakers_only(self):
        index=TurnIndex([dict(start=0,end=2,speaker='A'),dict(start=1,end=3,speaker='B')])
        coverage, overlap=index.evidence(0,3)
        self.assertEqual(coverage,{'A':2,'B':2})
        self.assertEqual(overlap,1)
        same=TurnIndex([dict(start=0,end=3,speaker='A')]*2)
        self.assertEqual(same.evidence(0,3),({'A':3},0))

    def test_long_preceding_turn_is_not_lost_by_index(self):
        index=TurnIndex([dict(start=0,end=100,speaker='A'),dict(start=1,end=2,speaker='B')])
        self.assertEqual(index.evidence(50,51),({'A':1},0))

    def test_adjacent_turns_cross_word_without_overlap(self):
        result=assess_rows([self.row()],[dict(start=0,end=1,speaker='A'),dict(start=1,end=3,speaker='B')])['s']
        self.assertIn('word_crosses_turns',result['speaker_reasons'])
        self.assertNotIn('overlapping_speech',result['speaker_reasons'])
        self.assertEqual(result['speaker_confidence'],'low')

    def test_gaps_and_short_replies_are_low(self):
        row=self.row(); row.update(start=4,end=4.5,text='Okay.',words=[dict(start=4,end=4.5,word='Okay.')])
        result=assess_rows([row],[dict(start=0,end=3,speaker='A')])['s']
        self.assertEqual(result['speaker_confidence'],'low')
        self.assertTrue({'no_detected_turn','short_reply','word_in_gap'} <= set(result['speaker_reasons']))

    def test_wrong_speaker_and_smoothed_assignment_flagged(self):
        row=self.row()
        result=assess_rows([row],[dict(start=0,end=3,speaker='B')],before=[dict(segment_id='s',speaker='B')])['s']
        self.assertTrue({'speaker_not_supported','smoothed_assignment'} <= set(result['speaker_reasons']))
        self.assertEqual(result['speaker_confidence'],'low')

    def test_missing_word_times_medium_and_rare_speaker_flagged(self):
        row=self.row(); row['words']=[]
        turns=[dict(start=0,end=3,speaker='A'),dict(start=3,end=100,speaker='B')]
        result=assess_rows([row],turns)['s']
        self.assertEqual(result['speaker_confidence'],'medium')
        self.assertTrue({'missing_word_times','little_speaker_evidence'} <= set(result['speaker_reasons']))

    def test_invalid_times_are_low_and_json_safe(self):
        row=self.row(); row['end']=float('nan')
        result=assess_rows([row],[dict(start=0,end=float('inf'),speaker='A')])
        self.assertEqual(result['s']['speaker_reasons'],['invalid_line_times'])
        json.dumps(result,allow_nan=False)

    def test_smoothed_word_is_risky_even_without_before_snapshot(self):
        row=self.row()
        row['words']=[dict(start=0,end=1,word='We'),dict(start=1,end=1.2,word='can'),dict(start=1.2,end=3,word='continue')]
        turns=[dict(start=0,end=1,speaker='A'),dict(start=1,end=1.2,speaker='B'),dict(start=1.2,end=3,speaker='A')]
        result=assess_rows([row],turns)['s']
        self.assertEqual(result['speaker_confidence'],'low')
        self.assertIn('word_speaker_disagrees',result['speaker_reasons'])

    def test_missing_or_invalid_label_mapping_does_not_crash(self):
        turns=[dict(start=0,end=3,speaker='raw')]
        self.assertEqual(assess_rows([self.row()],turns,{'raw':None})['s']['speaker_confidence'],'low')
        self.assertIn('speaker_not_supported',assess_rows([self.row()],turns)['s']['speaker_reasons'])

    def test_count_mismatch_does_not_change_labels(self):
        turns=[dict(start=0,end=1,speaker='A'),dict(start=1,end=2,speaker='B')]
        original=copy.deepcopy(turns)
        self.assertEqual(count_mismatch(turns,1),dict(stated=1,detected=2,needs_review=True))
        self.assertFalse(count_mismatch(turns,2)['needs_review'])
        self.assertEqual(turns,original)
        for value in [None,True,-1,1.5,float('nan')]:
            self.assertIsNone(count_mismatch(turns,value))


if __name__ == '__main__':
    unittest.main()
