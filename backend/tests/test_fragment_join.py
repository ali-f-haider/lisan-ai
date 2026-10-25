"""v1.82.70: "How's" | "your face?" - the first word of a sentence alone on a line, the rest on the next line of the same
speaker, up to 2.5 s later. Offline; the voices file is made here (tone bursts), the lines are invented."""
import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

import line_tidy as lt


def row(i, start, end, text, speaker, **extra):
    r = {"segment_id": f"seg_{i}", "start": start, "end": end, "text": text, "speaker": speaker}
    r.update(extra)
    return r


def scene():
    return [row(0, 14.0, 16.0, "Okay.", "Speaker 2"),
            row(1, 17.4, 18.6, "How's", "Speaker 1"),
            row(2, 20.1, 20.5, "your face?", "Speaker 1"),
            row(3, 20.5, 22.9, "Fine. No problem.", "Speaker 2")]


def voices_file(tmp, voice_spans, seconds=24.0, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    x = np.zeros_like(t)
    for a, b in voice_spans:
        m = (t >= a) & (t < b)
        x[m] = 0.3 * np.sin(2 * np.pi * 120 * t[m])
    path = Path(tmp) / "v.wav"
    sf.write(str(path), x.astype("float32"), sr)
    return path


class Join(unittest.TestCase):
    def test_the_two_parts_become_one_line(self):
        out, n = lt.merge_fragments(scene())
        self.assertEqual(n, 1)
        self.assertEqual([r["text"] for r in out], ["Okay.", "How's your face?", "Fine. No problem."])
        self.assertEqual(out[1]["end"], 20.5)
        self.assertEqual(out[1]["speaker"], "Speaker 1")

    def test_without_the_voices_the_line_keeps_the_start_it_had(self):
        out, _ = lt.merge_fragments(scene())
        self.assertEqual(out[1]["start"], 17.4)

    def test_with_the_voices_the_line_starts_where_the_voice_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices_file(tmp, [(14.2, 15.6), (19.96, 20.5), (20.6, 22.0)])
            out, n = lt.merge_fragments(scene(), v)
        self.assertEqual(n, 1)
        self.assertAlmostEqual(out[1]["start"], 19.91, delta=0.08)
        self.assertEqual(out[1]["end"], 20.5)

    def test_the_tail_of_the_line_before_is_not_taken_for_the_start(self):
        rows = [row(0, 16.9, 17.3, "Good.", "Speaker 2"), row(1, 17.34, 18.6, "How's", "Speaker 1"),
                row(2, 20.1, 20.5, "your face?", "Speaker 1")]
        with tempfile.TemporaryDirectory() as tmp:
            v = voices_file(tmp, [(16.9, 17.5), (19.96, 20.5)])      # "Good." is still sounding at 17.5, after its stated end
            out, n = lt.merge_fragments(rows, v)
        self.assertEqual(n, 1)
        self.assertAlmostEqual(out[1]["start"], 19.91, delta=0.08)

    def test_a_voice_that_starts_where_the_line_starts_does_not_move_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices_file(tmp, [(17.5, 18.4), (20.1, 20.5)])
            out, _ = lt.merge_fragments(scene(), v)
        self.assertEqual(out[1]["start"], 17.4)

    def test_the_start_never_moves_past_the_second_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices_file(tmp, [(20.3, 20.5)])       # too short to count as a voice: nothing moves
            out, _ = lt.merge_fragments(scene(), v)
        self.assertLessEqual(out[1]["start"], 20.1)

    def test_word_times_are_joined_too(self):
        rows = scene()
        rows[1]["words"] = [{"word": "How's", "start": 17.4, "end": 18.6}]
        rows[2]["words"] = [{"word": "your", "start": 20.1, "end": 20.3}, {"word": "face?", "start": 20.3, "end": 20.5}]
        out, _ = lt.merge_fragments(rows)
        self.assertEqual([w["word"] for w in out[1]["words"]], ["How's", "your", "face?"])

    def test_a_broken_voices_file_is_not_an_error(self):
        out, n = lt.merge_fragments(scene(), "/no/such/file.wav")
        self.assertEqual(n, 1)
        self.assertEqual(out[1]["start"], 17.4)


class NotJoined(unittest.TestCase):
    def check(self, mutate, expect_text=None):
        rows = scene()
        mutate(rows)
        before = copy.deepcopy(rows)
        out, n = lt.merge_fragments(rows)
        self.assertEqual(n, 0)
        self.assertEqual(out, before)

    def test_another_speaker(self):
        self.check(lambda r: r[2].update(speaker="Speaker 2"))

    def test_too_far_apart(self):
        self.check(lambda r: r[2].update(start=21.3, end=21.7))

    def test_first_part_is_a_whole_sentence(self):
        self.check(lambda r: r[1].update(text="How are you?"))

    def test_first_part_ends_with_a_comma_the_speaker_paused_there(self):
        self.check(lambda r: r[1].update(text="Well,"))

    def test_first_part_ends_with_a_dash(self):
        self.check(lambda r: r[1].update(text="Well -"))

    def test_first_part_is_longer_than_three_words(self):
        self.check(lambda r: r[1].update(text="So how is that"))

    def test_second_part_starts_a_new_sentence(self):
        self.check(lambda r: r[2].update(text="Your face?"))

    def test_a_line_that_is_locked(self):
        self.check(lambda r: r[1].update(locked=True))

    def test_text_without_capital_and_small_letters_like_arabic(self):
        self.check(lambda r: (r[1].update(text="كيف"), r[2].update(text="وجهك؟")))

    def test_the_joined_line_would_be_too_long(self):
        def make(r):
            r[1].update(start=3.0, end=3.5)
            r[2].update(start=5.0, end=19.0)
        self.check(make)

    def test_empty_text(self):
        self.check(lambda r: r[1].update(text=""))

    def test_the_old_small_gap_rule_still_does_the_close_ones(self):
        rows = scene()
        rows[2].update(start=18.7)
        out, n = lt.merge_fragments(rows)
        self.assertEqual(n, 0)                    # gap 0.1 s: merge_split_phrases handles it, not this rule


class Wiring(unittest.TestCase):
    def test_tidy_lines_reports_and_renumbers(self):
        out, rep = lt.tidy_lines(scene())
        self.assertEqual(rep["fragments"], 1)
        self.assertEqual([r["segment_id"] for r in out], ["seg_0", "seg_1", "seg_2"])
        self.assertEqual(out[1]["text"], "How's your face?")

    def test_tidy_lines_passes_the_voices_on_so_the_start_follows_the_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices_file(tmp, [(14.2, 15.6), (19.96, 20.5), (20.6, 22.0)])
            out, rep = lt.tidy_lines(scene(), v)
        self.assertEqual(rep["fragments"], 1)
        self.assertAlmostEqual(out[1]["start"], 19.91, delta=0.08)

    def test_it_runs_after_the_close_gap_rule_without_double_joining(self):
        rows = [row(0, 1.0, 1.5, "How's", "Speaker 1"), row(1, 1.6, 2.0, "your face?", "Speaker 1")]
        out, rep = lt.tidy_lines(rows)
        self.assertEqual((rep["joined"], rep["fragments"]), (1, 0))
        self.assertEqual(out[0]["text"], "How's your face?")

    def test_a_sentence_after_it_is_left_alone(self):
        rows = scene() + [row(4, 23.0, 24.0, "thanks", "Speaker 1")]
        out, rep = lt.tidy_lines(rows)
        self.assertEqual(rep["fragments"], 1)
        self.assertEqual(out[-1]["text"], "thanks")


if __name__ == "__main__":
    unittest.main()
