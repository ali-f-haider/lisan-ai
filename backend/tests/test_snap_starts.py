"""v1.82.72: a line that starts long before its first sound of speech is started where the speech really begins.
Offline; the voices file is made here (tones and noise), the lines are invented."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

import line_tidy as lt

SR = 16000


def voices(tmp, parts, seconds=40.0, floor=0.0):
    """parts: (start, end, amplitude, kind) with kind 'tone' (a voiced 120 Hz sound) or 'noise' (an unvoiced rustle)."""
    t = np.arange(int(seconds * SR)) / SR
    rng = np.random.default_rng(1)
    x = floor * rng.standard_normal(t.size) if floor else np.zeros_like(t)      # `floor`: the quiet sound of the room under the voices
    for a, b, amp, kind in parts:
        m = (t >= a) & (t < b)
        x[m] += amp * (np.sin(2 * np.pi * 120 * t[m]) if kind == "tone" else rng.standard_normal(int(m.sum())))
    path = Path(tmp) / "v.wav"
    sf.write(str(path), x.astype("float32"), SR)
    return path


def row(i, start, end, text="word", **extra):
    r = {"segment_id": f"seg_{i}", "start": start, "end": end, "text": text, "speaker": "Speaker 1"}
    r.update(extra)
    return r


class Moves(unittest.TestCase):
    def test_a_line_with_a_long_silence_before_its_voice_starts_at_the_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices(tmp, [(10.0, 10.4, 0.3, "tone"), (14.0, 14.5, 0.3, "tone")])   # a short "You know," then the rest
            rows = [row(0, 8.0, 9.5), row(1, 10.0, 15.0, "You know, for a split second")]
            rows[1]["start"] = 5.0          # the recogniser stated 5.0 s, the voice comes at 10.0 s
            rows, moved = lt.snap_starts(rows, v)
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0][0], "seg_1")
        self.assertAlmostEqual(rows[1]["start"], 9.75, delta=0.1)
        self.assertEqual(rows[1]["end"], 15.0)
        self.assertEqual(rows[1]["text"], "You know, for a split second")

    def test_only_the_start_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices(tmp, [(10.0, 12.0, 0.3, "tone")])
            rows = [row(0, 5.0, 13.0, "hello there", emotion="calm", arabic_text="x")]
            before = copy.deepcopy(rows[0])
            rows, _ = lt.snap_starts(rows, v)
        after = dict(rows[0])
        after["start"] = before["start"]
        self.assertEqual(after, before)

    def test_a_soft_unvoiced_beginning_is_kept_inside_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices(tmp, [(10.0, 10.4, 0.1, "noise"), (10.4, 12.0, 0.3, "tone")])      # "sh" then the vowel
            rows, _ = lt.snap_starts([row(0, 5.0, 13.0)], v)
        self.assertLessEqual(rows[0]["start"], 10.0)
        self.assertGreater(rows[0]["start"], 9.5)

    def test_a_quiet_first_word_before_a_loud_one_is_not_cut_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices(tmp, [(10.0, 10.6, 0.01, "tone"), (14.0, 15.0, 0.4, "tone")])      # a murmur, then a shout
            rows, _ = lt.snap_starts([row(0, 5.0, 16.0)], v)
        self.assertLessEqual(rows[0]["start"], 10.0)

    def test_the_sound_of_the_line_before_after_its_stated_end_is_not_taken_for_the_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices(tmp, [(16.9, 17.5, 0.3, "tone"), (19.96, 20.5, 0.3, "tone")])      # "Good." still sounds at 17.5
            rows = [row(0, 16.9, 17.3, "Good."), row(1, 17.34, 20.5, "How's your face?")]
            rows, moved = lt.snap_starts(rows, v)
        self.assertEqual([m[0] for m in moved], ["seg_1"])
        self.assertAlmostEqual(rows[1]["start"], 19.7, delta=0.1)

    def test_word_times_from_before_the_voice_follow_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices(tmp, [(10.0, 12.0, 0.3, "tone")])
            r = row(0, 5.0, 13.0, "hi there", words=[{"word": "hi", "start": 5.0, "end": 6.0}, {"word": "there", "start": 10.1, "end": 11.0}])
            rows, _ = lt.snap_starts([r], v)
        self.assertEqual(rows[0]["words"][0]["start"], rows[0]["start"])
        self.assertGreaterEqual(rows[0]["words"][0]["end"], rows[0]["words"][0]["start"])
        self.assertEqual(rows[0]["words"][1]["start"], 10.1)

    def test_each_of_the_three_ears_can_start_the_line_alone(self):
        cases = {
            # a burst of 0.12 s: too short for a 0.25 s level run and for a pitch run, but a burst
            "burst": ([(10.0, 10.12, 0.3, "noise")], 0.0),
            # the same short burst in a room with a quiet hum, 28 dB over the room
            "burst in a room": ([(10.0, 10.12, 0.05, "noise")], 0.002),
            # a soft unvoiced rustle 14 dB over the room: loud enough for the level ear (12 dB), not for the burst ear (15 dB)
            "level": ([(10.0, 10.5, 0.025, "noise")], 0.005),
            # a soft voice 9 dB over the room: only the pitch ear hears a steady voiced sound
            "pitch": ([(10.0, 12.0, 0.02, "tone")], 0.005),
        }
        for name, (parts, floor) in cases.items():
            with tempfile.TemporaryDirectory() as tmp:
                v = voices(tmp, parts, floor=floor)
                rows, moved = lt.snap_starts([row(0, 5.0, 13.0)], v)
            self.assertEqual(len(moved), 1, name)
            self.assertAlmostEqual(rows[0]["start"], 9.75, delta=0.15, msg=name)

    def test_running_it_twice_changes_nothing_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices(tmp, [(10.0, 12.0, 0.3, "tone")])
            rows, first = lt.snap_starts([row(0, 5.0, 13.0)], v)
            rows, second = lt.snap_starts(rows, v)
        self.assertEqual((len(first), second), (1, []))


class Stays(unittest.TestCase):
    def check(self, parts, rows, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices(tmp, parts)
            before = copy.deepcopy(rows)
            out, moved = lt.snap_starts(rows, v)
        self.assertEqual(moved, [])
        self.assertEqual(out, before)

    def test_a_short_silence_before_the_voice(self):
        self.check([(10.5, 12.0, 0.3, "tone")], [row(0, 10.0, 13.0)])

    def test_a_one_word_burst_early_in_a_long_silence_is_the_start_not_the_silence_after_it(self):
        # "Fine." is only 0.2 s long, then a long silence, then "No problem.": the line starts at "Fine."
        self.check([(10.5, 10.7, 0.3, "noise"), (13.4, 14.5, 0.3, "tone")], [row(0, 10.0, 15.0, "Fine. No problem.")])

    def test_nothing_sounds_in_the_line(self):
        self.check([(30.0, 31.0, 0.3, "tone")], [row(0, 5.0, 13.0)])

    def test_a_locked_line(self):
        self.check([(10.0, 12.0, 0.3, "tone")], [row(0, 5.0, 13.0, locked=True)])

    def test_a_line_too_short_to_have_a_long_lead(self):
        self.check([(11.0, 11.5, 0.3, "tone")], [row(0, 10.0, 11.6)])

    def test_the_voice_starts_too_close_to_the_end(self):
        self.check([(12.9, 13.0, 0.3, "tone")], [row(0, 5.0, 13.0)])

    def test_without_the_voices_file(self):
        rows = [row(0, 5.0, 13.0)]
        out, moved = lt.snap_starts(copy.deepcopy(rows), None)
        self.assertEqual((out, moved), (rows, []))
        out, moved = lt.snap_starts(copy.deepcopy(rows), "/no/such/file.wav")
        self.assertEqual((out, moved), (rows, []))

    def test_switched_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices(tmp, [(10.0, 12.0, 0.3, "tone")])
            with mock.patch.object(lt, "SNAP_STARTS", False):
                out, moved = lt.snap_starts([row(0, 5.0, 13.0)], v)
        self.assertEqual((moved, out[0]["start"]), ([], 5.0))

    def test_a_row_without_times_is_skipped_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices(tmp, [(10.0, 12.0, 0.3, "tone")])
            out, moved = lt.snap_starts([{"segment_id": "x"}, row(1, 5.0, 13.0)], v)
        self.assertEqual([m[0] for m in moved], ["seg_1"])

    def test_the_move_is_never_more_than_the_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = voices(tmp, [(30.0, 32.0, 0.3, "tone")])
            out, moved = lt.snap_starts([row(0, 5.0, 33.0)], v)
        self.assertLessEqual(out[0]["start"], 5.0 + lt.LEAD_MAX_SHIFT)


class Wiring(unittest.TestCase):
    def test_the_analysis_runs_it_after_the_planner_and_before_the_review(self):
        src = Path(__file__).resolve().parent.parent.joinpath("longdub_service.py").read_text(encoding="utf-8")
        a, b, c = src.index("line_planner.plan_lines("), src.index("line_tidy.snap_starts("), src.index("_review_speakers(job, rows, turns, label_map, vote=vote)")
        self.assertTrue(a < b < c)
        self.assertIn('"line_starts"', src)


if __name__ == "__main__":
    unittest.main()
