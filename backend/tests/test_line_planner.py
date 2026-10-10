"""AI line planner (line_planner.py), tested offline with an injected fake model: no network, no cost."""
import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import line_planner as lp


def row(i, a, b, text, speaker, **extra):
    r = dict(segment_id=f'seg_{i}', start=a, end=b, text=text, speaker=speaker, speaker_id=speaker, words=[])
    r.update(extra)
    return r


def fake(*answers):
    """A model that gives the listed answers one after the other and counts what it was asked."""
    box = {'prompts': [], 'n': 0}
    def ask(prompt):
        box['prompts'].append(prompt)
        a = answers[min(box['n'], len(answers) - 1)]
        box['n'] += 1
        return a, {'in': 100, 'out': 20, 'thoughts': 5}
    ask.box = box
    return ask


def dialog():
    return [row(0, 0, 4, 'Do you accept the deal? Because I', 'a'),
            row(1, 4, 7, "think you should. It's final.", 'b'),
            row(2, 7, 8, 'Fine.', 'a')]


class PlannerTests(unittest.TestCase):
    def test_it_is_off_unless_switched_on(self):
        self.assertFalse(lp.ENABLED)

    def test_a_valid_move_changes_only_the_cut(self):
        rows = dialog()
        out, rep = lp.plan_lines('j', rows, 'k', ask=fake({'ops': [{'op': 'move_words', 'from': 'seg_0', 'to': 'seg_1', 'n': 2, 'side': 'end'}]}))
        self.assertEqual(out[0]['text'], 'Do you accept the deal?')
        self.assertEqual(out[1]['text'], "Because I think you should. It's final.")
        self.assertEqual((rep['applied'], rep['refused'], rep['tokens_in'], rep['tokens_out']), (1, 0, 100, 20))
        self.assertEqual(rows[0]['text'], 'Do you accept the deal? Because I', 'the caller\'s rows are not touched')

    def test_moving_the_start_of_a_line_to_the_line_before(self):
        rows = [row(0, 0, 3, 'Do you accept?', 'a'), row(1, 3, 6, 'Yes I do. Thanks.', 'b')]
        out, rep = lp.plan_lines('j', rows, 'k', ask=fake([{'op': 'move_words', 'from': 'seg_1', 'to': 'seg_0', 'n': 1, 'side': 'start'}]))
        self.assertEqual((out[0]['text'], out[1]['text']), ('Do you accept? Yes', 'I do. Thanks.'))

    def test_merge_of_the_same_speaker_and_not_of_two_speakers(self):
        rows = [row(0, 0, 1, 'Good', 'a'), row(1, 1.2, 3, 'Lord.', 'a'), row(2, 3, 4, 'Hm.', 'b')]
        out, rep = lp.plan_lines('j', rows, 'k', ask=fake({'ops': [{'op': 'merge', 'a': 'seg_0', 'b': 'seg_1'}, {'op': 'merge', 'a': 'seg_1', 'b': 'seg_2'}]}))
        self.assertEqual([r['text'] for r in out], ['Good Lord.', 'Hm.'])
        self.assertEqual((rep['applied'], rep['refused']), (1, 1))
        self.assertEqual([r['segment_id'] for r in out], ['seg_0', 'seg_1'])

    def test_every_rule_refuses_a_bad_operation(self):
        rows = [row(0, 0, 3, 'one two three four five six seven eight', 'a'), row(1, 3, 6, 'nine ten', 'b'),
                row(2, 6, 7, 'eleven', 'b'), row(3, 9, 10, 'twelve', 'b'), row(4, 10, 11, 'thirteen', 'a', locked=True)]
        bad = [
            {'op': 'move_words', 'from': 'seg_0', 'to': 'seg_2', 'n': 1, 'side': 'end'},        # not neighbours
            {'op': 'move_words', 'from': 'seg_0', 'to': 'seg_1', 'n': 7, 'side': 'end'},        # too many words
            {'op': 'move_words', 'from': 'seg_1', 'to': 'seg_0', 'n': 2, 'side': 'start'},      # would empty the line
            {'op': 'move_words', 'from': 'seg_1', 'to': 'seg_2', 'n': 1, 'side': 'end'},        # same speaker
            {'op': 'move_words', 'from': 'seg_3', 'to': 'seg_4', 'n': 0, 'side': 'end'},
            {'op': 'move_words', 'from': 'seg_3', 'to': 'seg_4', 'n': 1, 'side': 'sideways'},
            {'op': 'move_words', 'from': 'seg_3', 'to': 'seg_4', 'n': 'x', 'side': 'end'},
            {'op': 'merge', 'a': 'seg_2', 'b': 'seg_3'},                                       # 2 s pause
            {'op': 'merge', 'a': 'seg_0', 'b': 'seg_9'},                                       # unknown line
            {'op': 'rewrite', 'id': 'seg_0', 'text': 'x'},
        ]
        out, rep = lp.plan_lines('j', rows, 'k', ask=fake({'ops': bad[:lp.MAX_OPS_PER_WINDOW]}))
        self.assertEqual(rep['applied'], 0)
        self.assertEqual([r['text'] for r in out], [r['text'] for r in rows])
        out, rep = lp.plan_lines('j', rows, 'k', ask=fake({'ops': bad[8:] + [{'op': 'move_words', 'from': 'seg_3', 'to': 'seg_4', 'n': 1, 'side': 'end'}]}))
        self.assertEqual(rep['applied'], 0, 'a locked line never changes')

    def test_context_lines_are_shown_but_never_changed(self):
        rows = [row(i, i, i + 1, f'line{i} a b', 'a' if i % 2 else 'b') for i in range(10)]
        ask = fake({'ops': [{'op': 'move_words', 'from': 'seg_5', 'to': 'seg_6', 'n': 1, 'side': 'end'}]}, {'ops': []})
        out, rep = lp.plan_lines('j', rows, 'k', ask=ask, window=4)
        self.assertEqual(rep['applied'], 0, 'seg_5 is outside the first window of 4 lines (context only)')
        self.assertIn('(context)', ask.box['prompts'][0])
        self.assertEqual(rep['windows'], 3)

    def test_nonsense_answers_change_nothing(self):
        for answer in (None, 'text', {'ops': 'x'}, {'ops': [None, 3, 'x']}, [], {}):
            rows = dialog()
            out, rep = lp.plan_lines('j', rows, 'k', ask=fake(answer))
            self.assertEqual([r['text'] for r in out], [r['text'] for r in dialog()])

    def test_a_crashing_model_leaves_the_rows_alone(self):
        def boom(prompt):
            raise RuntimeError('network down')
        rows = dialog()
        out, rep = lp.plan_lines('j', rows, 'k', ask=boom)
        self.assertIs(out, rows); self.assertIn('error', rep)

    def test_no_key_and_no_model_means_nothing_happens(self):
        rows = dialog()
        out, rep = lp.plan_lines('j', rows, '')
        self.assertIs(out, rows); self.assertEqual(rep.get('skipped'), 'no key')

    def test_the_time_budget_stops_it(self):
        rows = [row(i, i, i + 1, 'a b c', 'a' if i % 2 else 'b') for i in range(30)]
        out, rep = lp.plan_lines('j', rows, 'k', ask=fake({'ops': []}), budget_sec=-1, window=5)
        self.assertEqual((rep['windows'], rep.get('stopped')), (0, 'time budget'))

    def test_the_words_must_come_out_exactly_as_they_went_in(self):
        original = lp._apply
        def damaging(op, rows, vocals):
            original(op, rows, vocals)
            rows[0]['text'] = rows[0]['text'] + ' extra'
        lp._apply = damaging
        try:
            rows = dialog()
            out, rep = lp.plan_lines('j', rows, 'k', ask=fake({'ops': [{'op': 'move_words', 'from': 'seg_0', 'to': 'seg_1', 'n': 2, 'side': 'end'}]}))
        finally:
            lp._apply = original
        self.assertEqual((rep['reverted_windows'], rep['applied']), (1, 0))
        self.assertEqual([r['text'] for r in out], [r['text'] for r in dialog()])

    def test_the_prompt_lists_the_lines_with_speaker_and_time(self):
        p = lp.build_prompt(dialog()[:2], [], dialog()[2:])
        self.assertIn('seg_0 | a | 0.00-4.00 | Do you accept the deal? Because I', p)
        self.assertIn('seg_2 | a | 7.00-8.00 | Fine. (context)', p)

    def test_flag_marks_both_lines_and_ok_drops_the_programs_doubt(self):
        rows = dialog()
        out, rep = lp.plan_lines('j', rows, 'k', ask=fake({'ops': [{'op': 'flag', 'a': 'seg_0', 'b': 'seg_1'}]}))
        self.assertEqual([r.get('cut_check') for r in out], ['end', 'start', None])
        self.assertEqual((out[0]['cut_reasons'], rep['flagged'], [r['text'] for r in out]), (['ai_unsure'], 1, [r['text'] for r in dialog()]))
        doubtful = dialog(); doubtful[0].update(cut_check='end', cut_reasons=['open_end']); doubtful[1].update(cut_check='start', cut_reasons=['open_end'])
        out, rep = lp.plan_lines('j', doubtful, 'k', ask=fake({'ops': [{'op': 'ok', 'a': 'seg_0', 'b': 'seg_1'}]}))
        self.assertEqual(([r.get('cut_check') for r in out], rep['cleared']), ([None, None, None], 1))
        self.assertEqual(doubtful[0]['cut_check'], 'end', 'the caller\'s rows are not touched')

    def test_flag_and_ok_only_for_neighbours_inside_the_window(self):
        out, rep = lp.plan_lines('j', dialog(), 'k', ask=fake({'ops': [{'op': 'flag', 'a': 'seg_0', 'b': 'seg_2'}, {'op': 'ok', 'a': 'seg_0', 'b': 'seg_9'}]}))
        self.assertEqual((rep['applied'], rep['refused']), (0, 2))

    def test_a_merge_keeps_only_the_marks_that_are_still_at_its_edges(self):
        rows = [row(0, 0, 1, 'Good', 'a', cut_check='start', cut_reasons=['open_end']), row(1, 1.2, 3, 'Lord.', 'a', cut_check='end', cut_reasons=['small_start']),
                row(2, 3, 4, 'Hm.', 'b', cut_check='start', cut_reasons=['small_start'])]
        out, rep = lp.plan_lines('j', rows, 'k', ask=fake({'ops': [{'op': 'merge', 'a': 'seg_0', 'b': 'seg_1'}]}))
        self.assertEqual(([r['text'] for r in out], out[0]['cut_check']), (['Good Lord.', 'Hm.'], 'both'))
        rows[0].pop('cut_check'); rows[1].pop('cut_check')
        out, rep = lp.plan_lines('j', rows, 'k', ask=fake({'ops': [{'op': 'merge', 'a': 'seg_0', 'b': 'seg_1'}]}))
        self.assertNotIn('cut_check', out[0])

    def test_suspect_cuts_are_shown_to_the_model(self):
        rows = dialog(); rows[0]['cut_check'] = 'end'
        self.assertIn('Because I <<suspect cut>>', lp.build_prompt(rows[:2], [], []))


if __name__ == '__main__':
    unittest.main()
