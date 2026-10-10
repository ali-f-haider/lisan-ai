"""Free clean-up of the lines at the edge between two speakers (line_tidy.py) and the subtitle hand-over (subs_align.py).
Offline; the audio check uses a synthetic file."""
import os, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import line_tidy, subs_align


def row(i, a, b, text, speaker, **extra):
    r = dict(segment_id=f'seg_{i}', start=a, end=b, text=text, speaker=speaker, speaker_id=speaker, words=[])
    r.update(extra)
    return r


class JoinTests(unittest.TestCase):
    def test_a_phrase_of_one_speaker_cut_in_two_is_joined_again(self):
        rows = [row(0, 141.33, 141.79, 'Good', 'judge'), row(1, 141.79, 144.41, "Lord. You're a confidential informant.", 'judge')]
        out, report = line_tidy.tidy_lines(rows)
        self.assertEqual(len(out), 1); self.assertEqual(out[0]['text'], "Good Lord. You're a confidential informant.")
        self.assertEqual((out[0]['start'], out[0]['end'], report['joined']), (141.33, 144.41, 1))

    def test_finished_sentences_other_speakers_gaps_and_interruptions_are_not_joined(self):
        for rows in ([row(0, 1, 2, 'Good.', 'a'), row(1, 2, 3, 'Lord.', 'a')],            # a finished sentence
                     [row(0, 1, 2, 'Good', 'a'), row(1, 2, 3, 'Lord.', 'b')],             # another speaker
                     [row(0, 1, 2, 'Good', 'a'), row(1, 3, 4, 'Lord.', 'a')],             # a real pause between them
                     [row(0, 1, 2, 'I just wanted to-', 'a'), row(1, 2, 3, 'Don’t.', 'a')]):  # cut off
            self.assertEqual(len(line_tidy.tidy_lines(rows)[0]), 2)

    def test_a_joined_line_is_never_longer_than_the_cap(self):
        rows = [row(0, 0, 10, 'word ' * 20, 'a'), row(1, 10, 20, 'more', 'a')]
        self.assertEqual(len(line_tidy.tidy_lines(rows)[0]), 2)


class MoveTests(unittest.TestCase):
    def setUp(self):
        import numpy as np, soundfile as sf
        self.dir = tempfile.TemporaryDirectory(); self.addCleanup(self.dir.cleanup)
        sr = 8000
        x = np.zeros(sr * 140, dtype='float32')
        t = np.arange(int(0.6 * sr)) / sr
        x[int(128.85 * sr):int(128.85 * sr) + len(t)] = 0.3 * np.sin(2 * np.pi * 180 * t)      # the voice of "My Immunity", before the line's recorded start
        x += 0.0005 * np.random.RandomState(1).randn(len(x)).astype('float32')
        self.vocals = Path(self.dir.name) / 'vocals.wav'; sf.write(str(self.vocals), x, sr)

    def case(self):
        return [row(0, 123.62, 125.6, 'And that is? My Immunity', 'judge'),
                row(1, 129.57, 141.07, 'Agreement with the federal government, an agreement that expressly covers the charges.', 'reddington')]

    def test_the_unfinished_beginning_moves_to_the_speaker_who_finishes_the_sentence(self):
        out, report = line_tidy.tidy_lines(self.case(), self.vocals)
        self.assertEqual(out[0]['text'], 'And that is?')
        self.assertTrue(out[1]['text'].startswith('My Immunity Agreement with the federal government'))
        self.assertEqual(report['moved'], 1)
        self.assertLess(out[0]['end'], 125.6)                       # the judge's line is shorter now
        self.assertAlmostEqual(out[1]['start'], 128.80, delta=0.08)   # and the line starts where the voice really starts (measured in the voices)
        self.assertEqual(out[1]['end'], 141.07)

    def test_without_the_voices_only_the_words_move(self):
        out, _ = line_tidy.tidy_lines(self.case())
        self.assertEqual(out[1]['start'], 129.57); self.assertTrue(out[1]['text'].startswith('My Immunity'))

    def test_no_word_is_lost_or_invented(self):
        before = ' '.join(r['text'] for r in self.case()).split()
        after = ' '.join(r['text'] for r in line_tidy.tidy_lines(self.case(), self.vocals)[0]).split()
        self.assertEqual(before, after)

    def test_interruptions_abbreviations_and_same_speaker_are_left_alone(self):
        keep = [[row(0, 1, 3, 'I only wanted to-', 'a'), row(1, 3, 5, 'Stop.', 'b')],                    # a cut-off, no finished sentence before it
                [row(0, 1, 3, 'Yes. Mr. Smith?', 'a'), row(1, 3, 5, 'Here.', 'b')],                     # ends in a sentence
                [row(0, 1, 3, 'Fine. And so', 'a'), row(1, 3, 5, 'it goes.', 'a')]]                      # the same speaker (joined, not moved)
        for rows in keep:
            before = [r['text'] for r in rows]
            out = line_tidy.tidy_lines([dict(r) for r in rows])[0]
            self.assertEqual(line_tidy.tidy_lines([dict(r) for r in rows])[1]['moved'], 0, before)
        self.assertEqual(line_tidy.tidy_lines([row(0, 1, 3, 'Yes. Mr. Smith', 'a'), row(1, 3, 5, 'is here.', 'b')])[0][0]['text'], 'Yes.')    # Mr. is not the end of a sentence

    def test_a_long_stray_beginning_is_not_moved(self):
        rows = [row(0, 1, 9, 'Fine. one two three four five six seven eight nine ten', 'a'), row(1, 9, 12, 'eleven.', 'b')]
        self.assertEqual(line_tidy.tidy_lines(rows)[1]['moved'], 0)

    def test_a_broken_input_returns_the_rows_unchanged(self):
        rows = [{'text': 'no times'}]
        out, report = line_tidy.tidy_lines(rows)
        self.assertEqual(out, rows); self.assertTrue(report.get('error'))


class SubtitleHandoverTests(unittest.TestCase):
    SRT = ("1\n00:02:21,790 --> 00:02:24,410\nGood Lord. You're a confidential informant.\n\n"
           "2\n00:02:25,150 --> 00:02:25,950\nMr. Sima? I-If...\n\n"
           "3\n00:02:26,270 --> 00:02:30,910\nIf there's an agreement,\nI haven't seen any evidence to prove it.\n\n"
           "4\n00:02:31,500 --> 00:02:32,900\nThat's hardly a denial.\n")

    def rows(self):
        return [dict(segment_id='p', start=141.79, end=144.41, text="Lord. You're a confidential informant.", speaker='Speaker 2', words=[]),
                dict(segment_id='a', start=145.15, end=145.95, text='Mr. Cima.', speaker='Speaker 2', words=[]),
                dict(segment_id='b', start=146.27, end=150.91, text="If there's an agreement, I haven't seen any evidence to prove it.", speaker='Speaker 3', words=[]),
                dict(segment_id='q', start=151.5, end=152.9, text="That's hardly a denial.", speaker='Speaker 4', words=[])]

    def test_a_stutter_in_front_of_the_next_speakers_words_goes_to_that_speaker(self):
        out, report = subs_align.correct_rows(self.rows(), self.SRT, 'x.srt')
        self.assertTrue(report['ok'])
        self.assertEqual([r['text'] for r in out][1:3], ['Mr. Sima?', "I-If... If there's an agreement, I haven't seen any evidence to prove it."])

    def test_the_old_behaviour_still_holds_for_one_speaker(self):
        rows = self.rows(); rows[1]['speaker'] = rows[2]['speaker'] = 'Speaker 2'
        out, _ = subs_align.correct_rows(rows, self.SRT, 'x.srt')
        self.assertEqual(len(out), 4); self.assertIn('Sima', out[1]['text'])

    def test_abbreviations_do_not_end_a_sentence(self):
        self.assertFalse(subs_align._ends_sentence('Mr.')); self.assertFalse(subs_align._ends_sentence('I-If...'))
        self.assertTrue(subs_align._ends_sentence('Sima?')); self.assertTrue(subs_align._ends_sentence('over.'))


if __name__ == '__main__':
    unittest.main()
