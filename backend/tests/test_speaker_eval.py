import copy
import csv
import itertools
import json
from pathlib import Path
import tempfile
import unittest

from tools.speaker_eval import evaluate, optimal_mapping
from tools.speaker_labels import make_sheet, read_sheet


def turn(a, b, speaker):
    return dict(start=a, end=b, speaker=speaker)


class SpeakerEvalTests(unittest.TestCase):
    def test_permuted_labels_are_perfect(self):
        result = evaluate([turn(0, 1, 'A'), turn(1, 2, 'B')],
                          [turn(0, 1, 'y'), turn(1, 2, 'x')])
        self.assertEqual(result['der']['rate'], 0)
        self.assertEqual(result['mapping'], {'y': 'A', 'x': 'B'})

    def test_all_der_components(self):
        result = evaluate({'turns': [turn(0, 2, 'A'), turn(2, 4, 'B')], 'duration': 5},
                          [turn(0, 1, 'x'), turn(2, 3, 'y'), turn(3, 5, 'x')])['der']
        self.assertEqual(result['missed_speaker_seconds'], 1)
        self.assertEqual(result['false_alarm_speaker_seconds'], 1)
        self.assertEqual(result['confusion_speaker_seconds'], 1)
        self.assertEqual(result['rate'], .75)

    def test_mapping_is_optimal_not_greedy(self):
        weights = {('x', 'a'): 9, ('x', 'b'): 8, ('y', 'a'): 8, ('y', 'b'): 0}
        self.assertEqual(optimal_mapping(weights, ['x', 'y'], ['a', 'b']), {'x': 'b', 'y': 'a'})

    def test_mapping_matches_brute_force_and_is_deterministic(self):
        for values in itertools.product(range(3), repeat=4):
            weights = dict(zip(itertools.product('xy', 'ab'), values))
            mapping = optimal_mapping(weights, 'yx', 'ba')
            got = sum(weights[h, r] for h, r in mapping.items())
            best = max(sum(weights[h, r] for h, r in zip('xy', p)) for p in itertools.permutations('ab'))
            self.assertEqual(got, best)
            self.assertEqual(mapping, optimal_mapping(weights, 'xy', 'ab'))

    def test_overlapping_speakers_are_scored(self):
        der = evaluate([turn(0, 1, 'A'), turn(0, 1, 'B')], [turn(0, 1, 'x')])['der']
        self.assertEqual(der['reference_speaker_seconds'], 2)
        self.assertEqual(der['rate'], .5)

    def test_duplicate_same_speaker_tracks_do_not_double_count(self):
        turns = [turn(0, 1, 'A'), turn(.2, .8, 'A')]
        self.assertEqual(evaluate(turns, turns)['der']['reference_speaker_seconds'], 1)

    def test_split_and_merge_penalized_by_one_to_one_mapping(self):
        self.assertEqual(evaluate([turn(0, 2, 'A')], [turn(0, 1, 'x'), turn(1, 2, 'y')])['der']['rate'], .5)
        self.assertEqual(evaluate([turn(0, 1, 'A'), turn(1, 2, 'B')], [turn(0, 2, 'x')])['der']['rate'], .5)

    def test_collar_excludes_each_side_of_reference_boundaries(self):
        ref = [turn(0, 1, 'A'), turn(1, 2, 'B')]
        hyp = [turn(0, 1.1, 'x'), turn(1.1, 2, 'y')]
        self.assertGreater(evaluate(ref, hyp)['der']['rate'], 0)
        self.assertEqual(evaluate(ref, hyp, collar=.1)['der']['rate'], 0)
        self.assertAlmostEqual(evaluate(ref, hyp, collar=.1)['der']['reference_speaker_seconds'], 1.6)

    def test_frame_centres_and_half_open_boundaries(self):
        der = evaluate([turn(.005, .015, 'A')], [turn(.005, .015, 'x')])['der']
        self.assertEqual(der['reference_speaker_seconds'], .01)

    def test_partial_regions_exclude_unlabelled_speech(self):
        result = evaluate({'turns': [turn(0, 1, 'A')], 'regions': [{'start': 0, 'end': 1}]},
                          [turn(0, 2, 'x')])
        self.assertEqual(result['der']['rate'], 0)

    def test_empty_truth_does_not_emit_nan(self):
        result = evaluate({'turns': [], 'duration': 1}, [turn(0, 1, 'x')])
        self.assertIsNone(result['der']['rate'])
        self.assertEqual(result['der']['false_alarm_speaker_seconds'], 1)
        json.dumps(result, allow_nan=False)

    def test_invalid_times_are_rejected(self):
        for bad in [float('nan'), float('inf'), -1]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                evaluate([turn(bad, 1, 'A')], [])
            with self.subTest(duration=bad), self.assertRaises(ValueError):
                evaluate({'turns': [], 'duration': bad}, [])

    def test_line_only_labels_do_not_invent_der(self):
        ref = {'lines': [{'segment_id': '1', 'speaker': 'Ali'}, {'segment_id': '2', 'speaker': 'B'}]}
        hyp = {'segments': [{'segment_id': '1', 'speaker': 'x'}]}
        result = evaluate(ref, hyp)
        self.assertIsNone(result['der'])
        self.assertEqual(result['line_accuracy'], .5)
        self.assertEqual(result['wrong_lines'][0]['segment_id'], '2')

    def test_duplicate_ids_rejected_and_unlabelled_skipped(self):
        with self.assertRaises(ValueError):
            evaluate({'lines': [{'segment_id': '1', 'speaker': 'A'}] * 2}, [])
        self.assertEqual(evaluate({'lines': [{'segment_id': '1', 'speaker': '?'}]}, [])['labelled_lines'], 0)
        with self.assertRaises(ValueError):
            evaluate({'lines': [{'segment_id': '1', 'speaker': '?'}] * 2}, [])

    def test_interval_labels_survive_reflow(self):
        ref = {'lines': [dict(segment_id='old', **turn(0, 2, 'Ali'))]}
        hyp = {'turns': [turn(0, 2, 'x')], 'rows': [dict(segment_id='new', **turn(0, 2, 'x'))]}
        self.assertEqual(evaluate(ref, hyp, line_mode='intervals')['line_accuracy'], 1)
        self.assertEqual(evaluate(ref, hyp)['line_accuracy'], 0)

    def test_row_der_explicitly_marked_proxy_and_risky_lines_reported(self):
        hyp = {'segments': [dict(segment_id='a', **turn(0, 1, 'x'), speaker_confidence='low', speaker_reasons=['short_reply'])]}
        result = evaluate({'turns': [turn(0, 1, 'A')]}, hyp)
        self.assertEqual(result['der']['hypothesis_kind'], 'row_span_proxy')
        self.assertEqual(result['flagged_unlabelled_lines'][0]['reasons'], ['short_reply'])


class SpeakerLabelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sheet = Path(self.temp.name) / 'labels.csv'
        self.export = {'segments': [dict(segment_id='seg_0', text='Hello,\nمرحبا', **turn(0, 1, 'Speaker 1')),
                                    dict(segment_id='seg_1', text='=1+1', **turn(1, 2, 'Speaker 2'))]}

    def fill(self, names):
        with self.sheet.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        for row, name in zip(rows, names):
            row['speaker'] = name
        with self.sheet.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=rows[0]); w.writeheader(); w.writerows(rows)
        return rows

    def test_csv_round_trip_unicode_and_unsafe_display(self):
        make_sheet(self.export, self.sheet)
        rows = self.fill(['أحمد', '?'])
        self.assertEqual(rows[0]['text'], 'Hello,\nمرحبا')
        self.assertEqual(rows[1]['text'], "'=1+1")
        ref = read_sheet(self.export, self.sheet)
        self.assertEqual(ref['lines'][0]['speaker'], 'أحمد')
        self.assertEqual(len(ref['lines']), 1)
        self.assertEqual(evaluate(ref, self.export)['line_accuracy'], 1)

    def test_same_frozen_lines_allow_new_predictions_but_not_changed_text(self):
        make_sheet(self.export, self.sheet); self.fill(['A', 'B'])
        ref = read_sheet(self.export, self.sheet)
        changed = copy.deepcopy(self.export)
        changed['segments'][0]['speaker'] = 'new'
        evaluate(ref, changed)
        changed['segments'][0]['text'] = 'Different'
        with self.assertRaises(ValueError):
            evaluate(ref, changed)
        with self.assertRaises(ValueError):
            read_sheet(changed, self.sheet)

    def test_small_sheet_can_start_later(self):
        make_sheet(self.export, self.sheet, start=2, limit=1)
        rows = self.fill(['B'])
        self.assertEqual(rows[0]['line_number'], '2')
        self.assertEqual(len(read_sheet(self.export, self.sheet)['lines']), 1)

    def test_invalid_and_duplicate_exports_rejected(self):
        for export in [{}, {'segments': self.export['segments'] * 2}]:
            with self.assertRaises(ValueError):
                make_sheet(export, self.sheet)

    def test_edited_times_rejected(self):
        make_sheet(self.export, self.sheet); self.fill(['A', 'B'])
        text = self.sheet.read_text(encoding='utf-8-sig').replace('1,seg_0,0,1,', '1,seg_0,0,0.9,')
        self.sheet.write_text(text, encoding='utf-8-sig')
        with self.assertRaises(ValueError):
            read_sheet(self.export, self.sheet)


if __name__ == '__main__':
    unittest.main()
