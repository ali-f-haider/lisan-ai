"""The trial's cut comparison (line_planner_trial.py), offline."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import line_planner_trial as t


def rows(*texts):
    return [dict(segment_id=f'seg_{i}', start=i, end=i + 1, text=x, speaker='a') for i, x in enumerate(texts)]


class CompareTests(unittest.TestCase):
    def test_counts_hits_misses_and_extras(self):
        truth = rows('Do you accept the deal?', 'Because I think so.', 'Fine.')
        engine = rows('Do you accept the deal? Because I', 'think so.', 'Fine.')
        self.assertEqual(t.compare(truth, engine), (1, 2, 1))     # 'Fine.' cut kept; the other cut is in the wrong place
        self.assertEqual(t.compare(truth, truth), (2, 2, 0))

    def test_a_few_corrected_words_do_not_shift_the_positions(self):
        truth = rows('I-If you do', 'that is immunity.', 'Okay.')
        engine = rows('I-If you do that', 'is a immunity.', 'Okay.')
        self.assertEqual(t.compare(truth, engine)[1], 2)

    def test_marks_are_read_as_cut_positions_and_wrong_cuts_are_found(self):
        truth = rows('Do you accept the deal?', 'Because I think so.', 'Fine.')
        engine = rows('Do you accept the deal? Because I', 'think so.', 'Fine.')
        engine[0]['cut_check'] = 'end'
        hit, total, wrong = t.detail(truth, engine)
        self.assertEqual((len(wrong), wrong <= t.marked_cuts(engine), t.marked_cuts(engine)), (1, True, {7}))
        engine[0].pop('cut_check'); engine[1]['cut_check'] = 'start'
        self.assertEqual(t.marked_cuts(engine), {7})
        engine[1].pop('cut_check')
        self.assertEqual(t.marked_cuts(engine), set())

if __name__ == '__main__':
    unittest.main()
