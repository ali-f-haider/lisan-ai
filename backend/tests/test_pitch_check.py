"""The pitch check (pitch_check.py): a free second opinion on who says a line. Offline, synthetic voices only."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf

import pitch_check as pc

ROOT = Path(__file__).resolve().parents[1]
SR = 16000


def voice(f0, seconds, seed=0, vibrato=0.01, noise=0.003):
    """A steady voiced sound: harmonics with falling strength, a little vibrato and noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    phase = 2 * np.pi * np.cumsum(f0 * (1 + vibrato * np.sin(2 * np.pi * 5 * t))) / SR
    x = sum(np.sin(k * phase) / k for k in range(1, 12))
    x = 0.25 * x / np.max(np.abs(x))
    return (x + noise * rng.standard_normal(x.size)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * SR), dtype=np.float32)


def line(i, a, b, spk, text="text", **kw):
    r = {"segment_id": f"seg_{i}", "start": a, "end": b, "speaker": spk, "text": text}
    r.update(kw)
    return r


def scene(parts):
    """parts: [(speaker label as said, f0 or None for silence/noise, seconds)] -> (rows, audio) with 0.3 s gaps."""
    rows, chunks, t = [], [], 0.0
    for i, (spk, f0, sec) in enumerate(parts):
        chunks.append(silence(0.3))
        t += 0.3
        chunks.append(voice(f0, sec, seed=i) if f0 else (0.01 * np.random.default_rng(i).standard_normal(int(sec * SR))).astype(np.float32))
        rows.append(line(i, round(t, 3), round(t + sec, 3), spk, "line %d" % i))
        t += sec
    return rows, np.concatenate(chunks)


def write_wav(audio, sr=SR):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "vocals.wav")
    sf.write(p, audio, sr)
    return p


MAN, BOY = 110.0, 250.0


class Measuring(unittest.TestCase):
    def test_pitch_of_steady_voices_is_found(self):
        for f0 in (95.0, 110.0, 180.0, 250.0, 330.0):
            n, med, spread = pc.line_pitch(voice(f0, 1.0), SR)
            self.assertGreater(n, 60, f0)
            self.assertAlmostEqual(med / f0, 1.0, delta=0.04, msg=f0)
            self.assertLess(spread, 2.0)

    def test_the_lower_octave_is_not_picked_for_a_voice_with_a_strong_second_harmonic(self):
        x = voice(250.0, 1.0)
        n, med, _ = pc.line_pitch(x, SR)
        self.assertAlmostEqual(med / 250.0, 1.0, delta=0.04)

    def test_other_sample_rates_work(self):
        t = np.arange(44100) / 44100.0
        x = (0.3 * sum(np.sin(2 * np.pi * 120 * k * t) / k for k in range(1, 8))).astype(np.float32)
        n, med, _ = pc.line_pitch(x, 44100)
        self.assertAlmostEqual(med / 120.0, 1.0, delta=0.04)

    def test_silence_and_noise_are_not_voice(self):
        self.assertEqual(pc.line_pitch(silence(1.0), SR)[0], 0)
        noise = (0.05 * np.random.default_rng(3).standard_normal(SR)).astype(np.float32)
        self.assertLess(pc.line_pitch(noise, SR)[0], pc.MIN_VOICED_FRAMES)

    def test_too_short_a_sound_gives_nothing_and_does_not_raise(self):
        self.assertEqual(pc.line_pitch(np.zeros(10, dtype=np.float32), SR), (0, 0.0, 0.0))
        self.assertEqual(pc.line_pitch(np.zeros(0, dtype=np.float32), SR), (0, 0.0, 0.0))

    def test_a_line_with_two_very_different_pitches_is_not_measured(self):
        rows, audio = scene([("Speaker 1", MAN, 0.8)])
        mixed = np.concatenate([voice(MAN, 0.5, seed=1), voice(BOY, 0.5, seed=2)])
        path = write_wav(np.concatenate([silence(0.3), mixed]))
        meas = pc.measure([line(0, 0.3, 1.3, "Speaker 1")], path)
        self.assertEqual(meas, {})

    def test_only_the_lines_own_samples_are_read(self):
        rows, audio = scene([("Speaker 1", MAN, 1.0), ("Speaker 2", BOY, 1.0)])
        meas = pc.measure(rows, write_wav(audio))
        self.assertAlmostEqual(meas["seg_0"][1] / MAN, 1.0, delta=0.04)
        self.assertAlmostEqual(meas["seg_1"][1] / BOY, 1.0, delta=0.04)


class Deciding(unittest.TestCase):
    def meas(self, rows, hz, frames=60, spread=2.0):
        return {r["segment_id"]: (frames, h, spread) for r, h in zip(rows, hz) if h}

    def base(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2.0, "Speaker 1" if i % 2 == 0 else "Speaker 2") for i in range(12)]
        hz = [MAN if i % 2 == 0 else BOY for i in range(12)]
        return rows, hz

    def test_a_boy_line_given_to_the_man_is_moved_and_the_others_stay(self):
        rows, hz = self.base()
        rows[4]["speaker"] = "Speaker 2"                       # a man's line labelled as the boy
        rows[3]["speaker"] = "Speaker 1"                       # a boy's line labelled as the man
        moves, prof = pc.decide(rows, self.meas(rows, hz))
        self.assertEqual({(m["sid"], m["from"], m["to"]) for m in moves},
                         {("seg_4", "Speaker 2", "Speaker 1"), ("seg_3", "Speaker 1", "Speaker 2")})
        self.assertAlmostEqual(prof["Speaker 1"], MAN, delta=1)

    def test_a_line_between_the_two_voices_is_left_alone(self):
        rows, hz = self.base()
        hz[0] = 170.0                                          # neither the man's nor the boy's range
        self.assertEqual(pc.decide(rows, self.meas(rows, hz))[0], [])

    def test_a_loud_man_a_little_higher_than_usual_is_left_alone(self):
        rows, hz = self.base()
        hz[2] = 110 * 2 ** (6.0 / 12)                          # six semitones up: not far enough from the man's usual pitch
        self.assertEqual(pc.decide(rows, self.meas(rows, hz))[0], [])

    def test_a_line_the_user_set_by_hand_is_never_moved(self):
        rows, hz = self.base()
        rows[4]["speaker"] = "Speaker 2"
        rows[4]["manual_speaker"] = True
        self.assertEqual(pc.decide(rows, self.meas(rows, hz))[0], [])

    def test_lines_that_were_not_measured_are_never_moved(self):
        rows, hz = self.base()
        rows[4]["speaker"] = "Speaker 2"
        m = self.meas(rows, hz)
        del m["seg_4"]
        self.assertEqual(pc.decide(rows, m)[0], [])

    def test_two_voices_that_sound_alike_cannot_be_told_apart(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2.0, "Speaker 1" if i % 2 == 0 else "Speaker 2") for i in range(12)]
        hz = [120.0 if i % 2 == 0 else 135.0 for i in range(12)]
        hz[0] = 260.0
        self.assertEqual(pc.decide(rows, self.meas(rows, hz))[0], [])

    def test_a_speaker_with_too_few_measured_lines_has_no_usual_pitch(self):
        rows, hz = self.base()
        m = self.meas(rows, hz)
        for sid in ("seg_1", "seg_3", "seg_5", "seg_7"):         # the boy keeps only 2 measured lines
            del m[sid]
        rows[0]["speaker"] = "Speaker 2"
        moves, prof = pc.decide(rows, m)
        self.assertNotIn("Speaker 2", prof)
        self.assertEqual(moves, [])

    def test_a_shout_that_lands_near_the_other_voice_is_still_left_alone(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2.0, "Speaker 1" if i % 2 == 0 else "Speaker 2") for i in range(12)]
        hz = [120.0 if i % 2 == 0 else 200.0 for i in range(12)]
        hz[0] = 165.0                                           # 3.6 semitones from the other voice but only 5.5 from its own
        self.assertEqual(pc.decide(rows, self.meas(rows, hz))[0], [])

    def test_two_voices_less_than_eight_semitones_apart_are_never_used_to_move_a_line(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2.0, "Speaker 1" if i % 2 == 0 else "Speaker 2") for i in range(12)]
        hz = [100.0 if i % 2 == 0 else 150.0 for i in range(12)]    # 7 semitones apart
        hz[0] = 185.0                                           # far from its own, near the other
        self.assertEqual(pc.decide(rows, self.meas(rows, hz))[0], [])

    def test_a_line_near_two_other_speakers_is_not_guessed(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2.0, ["Speaker 1", "Speaker 2", "Speaker 3"][i % 3]) for i in range(18)]
        hz = [[100.0, 200.0, 225.0][i % 3] for i in range(18)]
        hz[0] = 212.0                                           # right between speakers 2 and 3, labelled as speaker 1
        self.assertEqual(pc.decide(rows, self.meas(rows, hz))[0], [])

    def test_an_emphatic_word_of_the_man_that_he_nearly_reaches_elsewhere_is_not_moved(self):
        """A real case: the man's "Good." measured 222 Hz (his usual 130 Hz), close to the boy's range, but he reaches 196 Hz elsewhere."""
        rows = [line(i, i * 3.0, i * 3.0 + 2.0, "Speaker 1" if i % 2 == 0 else "Speaker 2") for i in range(14)]
        hz = [[105.0, 131.0, 118.0, 196.0, 90.0, 179.0, 131.0][i // 2] if i % 2 == 0 else 258.0 for i in range(14)]
        rows[2]["speaker"] = "Speaker 1"
        hz[2] = 222.0                                           # nine semitones above his usual pitch, two above his highest elsewhere
        self.assertEqual(pc.decide(rows, self.meas(rows, hz))[0], [])

    def test_the_same_line_is_moved_when_the_man_never_goes_that_high_elsewhere(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2.0, "Speaker 1" if i % 2 == 0 else "Speaker 2") for i in range(14)]
        hz = [[105.0, 131.0, 118.0, 140.0, 90.0, 125.0, 131.0][i // 2] if i % 2 == 0 else 258.0 for i in range(14)]
        hz[2] = 222.0
        moves = pc.decide(rows, self.meas(rows, hz))[0]
        self.assertEqual([(m["sid"], m["to"]) for m in moves], [("seg_2", "Speaker 2")])

    def test_a_far_line_does_not_widen_the_range_it_is_judged_against(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2.0, "Speaker 1" if i % 2 == 0 else "Speaker 2") for i in range(14)]
        hz = [110.0 if i % 2 == 0 else 258.0 for i in range(14)]
        hz[2], hz[4] = 250.0, 262.0                              # two boy lines labelled as the man
        moves = pc.decide(rows, self.meas(rows, hz))[0]
        self.assertEqual({m["sid"] for m in moves}, {"seg_2", "seg_4"})

    def test_a_speaker_with_no_range_to_judge_by_never_has_a_line_moved(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2.0, "Speaker 1" if i % 2 == 0 else "Speaker 2") for i in range(14)]
        hz = [110.0 if i % 2 == 0 else 258.0 for i in range(14)]
        hz[2] = 250.0
        with mock.patch.object(pc, "ranges", return_value={}):
            self.assertEqual(pc.decide(rows, self.meas(rows, hz))[0], [])
        self.assertEqual([m["sid"] for m in pc.decide(rows, self.meas(rows, hz))[0]], ["seg_2"])

    def test_three_speakers_a_line_goes_only_to_the_one_it_is_near(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2.0, ["Speaker 1", "Speaker 2", "Speaker 3"][i % 3]) for i in range(18)]
        hz = [[100.0, 190.0, 300.0][i % 3] for i in range(18)]
        rows[1]["speaker"] = "Speaker 1"                        # a 190 Hz line labelled as the 100 Hz speaker
        moves, _ = pc.decide(rows, self.meas(rows, hz))
        self.assertEqual([(m["sid"], m["to"]) for m in moves], [("seg_1", "Speaker 2")])


class Running(unittest.TestCase):
    def wrong_labels(self):
        parts = [("Speaker 1", MAN, 1.2), ("Speaker 2", BOY, 1.2)] * 5
        rows, audio = scene(parts)
        rows[3]["speaker"] = "Speaker 1"                        # the boy's line labelled as the man
        rows[3]["text"] = "How's your face?"
        return rows, audio

    def test_end_to_end_a_wrong_line_is_moved_and_marked_for_checking(self):
        rows, audio = self.wrong_labels()
        res = pc.run(rows, write_wav(audio))
        self.assertEqual((res["status"], res["moved"]), ("ok", 1))
        self.assertEqual(rows[3]["speaker"], "Speaker 2")
        self.assertTrue(rows[3]["speaker_vote"]["badge"])
        self.assertIn("voters_split", rows[3]["speaker_vote"]["reasons"])
        self.assertIn("seg_3", res["text"])
        self.assertIn("S1->S2", res["text"])
        self.assertIn("usual pitch: S1 1", res["text"])
        self.assertIn(", S2 2", res["text"])
        self.assertLessEqual(len(res["text"]), 1400)
        self.assertEqual([r["speaker"] for i, r in enumerate(rows) if i != 3], ["Speaker 1" if i % 2 == 0 else "Speaker 2" for i in range(10) if i != 3])

    def test_correct_labels_are_left_exactly_as_they_were(self):
        rows, audio = scene([("Speaker 1", MAN, 1.2), ("Speaker 2", BOY, 1.2)] * 5)
        before = [dict(r) for r in rows]
        res = pc.run(rows, write_wav(audio))
        self.assertEqual((res["status"], res["moved"]), ("ok", 0))
        self.assertEqual(rows, before)

    def test_it_can_be_switched_off(self):
        rows, audio = self.wrong_labels()
        with mock.patch.object(pc, "ENABLED", False):
            res = pc.run(rows, write_wav(audio))
        self.assertEqual(res["status"], "off")
        self.assertEqual(rows[3]["speaker"], "Speaker 1")

    def test_nothing_is_changed_when_it_disagrees_too_often(self):
        rows, audio = scene([("Speaker 1", MAN, 1.2)] * 8 + [("Speaker 2", BOY, 1.2)] * 5 + [("Speaker 1", BOY, 1.2)] * 5)
        res = pc.run(rows, write_wav(audio))
        self.assertEqual((res["status"], res["moved"]), ("skipped", 0))
        self.assertIn("too many to trust", res["text"])
        self.assertEqual(sum(r["speaker"] == "Speaker 1" for r in rows), 13)

    def test_a_missing_recording_one_speaker_or_garbage_never_raises(self):
        rows, audio = self.wrong_labels()
        self.assertEqual(pc.run(rows, "/nonexistent/x.wav")["status"], "skipped")
        self.assertEqual(pc.run(rows, None)["status"], "skipped")
        one = [dict(r, speaker="Speaker 1") for r in rows]
        self.assertEqual(pc.run(one, write_wav(audio))["status"], "skipped")
        for bad in ([], [None], [{"segment_id": "a"}], [{"segment_id": "a", "start": "x", "end": None, "speaker": "S"}]):
            self.assertIn(pc.run(bad, write_wav(audio))["status"], ("skipped", "ok", "error"))
        junk = os.path.join(tempfile.mkdtemp(), "x.wav")
        Path(junk).write_bytes(b"not audio")
        self.assertIn(pc.run(rows, junk)["status"], ("error", "skipped"))

    def test_a_clip_with_no_measurable_voice_changes_nothing(self):
        rows, audio = scene([("Speaker 1", None, 1.2), ("Speaker 2", None, 1.2)] * 4)
        res = pc.run(rows, write_wav(audio))
        self.assertEqual((res["status"], res["moved"]), ("skipped", 0))


class Fragments(unittest.TestCase):
    """One sentence is said by one voice: "How's" | "your face?" must not end up with two different speakers."""

    def rows(self):
        return [line(0, 0.0, 2.0, "Speaker 1", "How'd you sleep, ace?"), line(1, 2.5, 3.5, "Speaker 2", "Okay."),
                line(2, 3.6, 4.6, "Speaker 1", "How's"), line(3, 6.0, 6.5, "Speaker 1", "your face?"),
                line(4, 7.0, 8.0, "Speaker 1", "Fine.")]

    def move(self, **kw):
        return [{"sid": "seg_3", "from": "Speaker 1", "to": "Speaker 2", "hz": 250.0}]

    PROF = {"Speaker 1": 110.0, "Speaker 2": 250.0}

    def test_the_unfinished_start_before_a_moved_end_goes_with_it(self):
        extra = pc.with_fragments(self.rows(), self.move(), {}, self.PROF)
        self.assertEqual(extra, [{"sid": "seg_2", "from": "Speaker 1", "to": "Speaker 2", "with": "seg_3"}])

    def test_a_fragment_that_ends_its_sentence_stays(self):
        rows = self.rows()
        rows[2]["text"] = "Good."
        self.assertEqual(pc.with_fragments(rows, self.move(), {}, self.PROF), [])

    def test_a_long_neighbour_stays(self):
        rows = self.rows()
        rows[2]["text"] = "so how is it going with"
        self.assertEqual(pc.with_fragments(rows, self.move(), {}, self.PROF), [])

    def test_a_neighbour_far_away_in_time_stays(self):
        rows = self.rows()
        rows[3]["start"], rows[3]["end"] = 12.0, 12.5
        self.assertEqual(pc.with_fragments(rows, self.move(), {}, self.PROF), [])

    def test_a_neighbour_of_the_other_speaker_or_set_by_hand_stays(self):
        rows = self.rows()
        rows[2]["speaker"] = "Speaker 2"
        self.assertEqual(pc.with_fragments(rows, self.move(), {}, self.PROF), [])
        rows[2]["speaker"], rows[2]["manual_speaker"] = "Speaker 1", True
        self.assertEqual(pc.with_fragments(rows, self.move(), {}, self.PROF), [])

    def test_a_neighbour_whose_own_pitch_says_it_is_the_old_speaker_stays(self):
        self.assertEqual(pc.with_fragments(self.rows(), self.move(), {"seg_2": (40, 112.0, 2.0)}, self.PROF), [])
        extra = pc.with_fragments(self.rows(), self.move(), {"seg_2": (40, 245.0, 2.0)}, self.PROF)
        self.assertEqual([e["sid"] for e in extra], ["seg_2"])

    def test_an_unfinished_moved_line_takes_the_short_ending_after_it(self):
        rows = [line(0, 0.0, 1.0, "Speaker 1", "Where did you"), line(1, 1.1, 1.6, "Speaker 1", "go?"), line(2, 3.0, 4.0, "Speaker 2", "Home.")]
        extra = pc.with_fragments(rows, [{"sid": "seg_0", "from": "Speaker 1", "to": "Speaker 2", "hz": 250.0}], {}, self.PROF)
        self.assertEqual([e["sid"] for e in extra], ["seg_1"])

    def test_end_to_end_the_beginning_follows_and_both_are_marked_and_listed(self):
        parts = [("Speaker 1", MAN, 1.2), ("Speaker 2", BOY, 1.2)] * 4
        rows, audio = scene(parts)
        t0 = rows[-1]["end"]
        rows.append(line(8, t0 + 1.5, t0 + 1.8, "Speaker 1", "How's"))
        rows.append(line(9, t0 + 3.0, t0 + 3.6, "Speaker 1", "your face?"))
        audio = np.concatenate([audio, silence(1.5), silence(0.3), silence(1.2), voice(BOY, 0.6, seed=99)])
        res = pc.run(rows, write_wav(audio))
        by = {r["segment_id"]: r for r in rows}
        self.assertEqual(by["seg_9"]["speaker"], "Speaker 2")
        self.assertEqual(by["seg_8"]["speaker"], "Speaker 2")
        self.assertTrue(by["seg_8"]["speaker_vote"]["badge"])
        self.assertIn("goes with seg_9", res["text"])


class MixedPitch(unittest.TestCase):
    def test_a_line_with_two_very_different_voices_is_marked_not_moved(self):
        parts = [("Speaker 1", MAN, 1.2), ("Speaker 2", BOY, 1.2)] * 4
        rows, audio = scene(parts)
        mix_at = rows[-1]["end"] + 0.3
        audio = np.concatenate([audio, silence(0.3), voice(MAN, 0.5, seed=5), voice(BOY, 0.5, seed=6)])
        rows.append(line(8, mix_at, mix_at + 1.0, "Speaker 2", "Fine. No problem."))
        res = pc.run(rows, write_wav(audio))
        self.assertEqual(res["status"], "ok")
        self.assertEqual(rows[-1]["speaker"], "Speaker 2")            # nothing moved
        self.assertTrue(rows[-1]["speaker_vote"]["badge"])
        self.assertEqual(res["flagged"], 1)
        self.assertIn("seg_8", res["text"])
        self.assertIn("nothing moved", res["text"])

    def test_a_mixed_line_the_user_set_by_hand_is_left_alone(self):
        parts = [("Speaker 1", MAN, 1.2), ("Speaker 2", BOY, 1.2)] * 4
        rows, audio = scene(parts)
        at = rows[-1]["end"] + 0.3
        audio = np.concatenate([audio, silence(0.3), voice(MAN, 0.5, seed=5), voice(BOY, 0.5, seed=6)])
        rows.append(line(8, at, at + 1.0, "Speaker 2", "Fine. No problem.", manual_speaker=True))
        res = pc.run(rows, write_wav(audio))
        self.assertEqual(res.get("flagged"), 0)
        self.assertNotIn("speaker_vote", rows[-1])

    def test_a_line_that_moved_along_with_a_neighbour_is_not_also_listed_as_mixed(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2.0, "Speaker 1" if i % 2 == 0 else "Speaker 2") for i in range(12)]
        rows[6].update(text="How's", start=18.0, end=18.3)
        rows[7].update(text="your face?", start=19.0, end=19.5, speaker="Speaker 1")          # the boy's pitch, labelled as the man
        meas = {r["segment_id"]: (60, 110.0 if i % 2 == 0 else 250.0, 2.0) for i, r in enumerate(rows) if i != 6}
        with mock.patch.object(pc, "measure_all", return_value=(meas, {"seg_6": 12.0})), mock.patch.object(pc.os.path, "exists", return_value=True):
            res = pc.run(rows, "x.wav")
        self.assertEqual(rows[7]["speaker"], "Speaker 2")
        self.assertEqual(rows[6]["speaker"], "Speaker 2")                     # went with it
        self.assertEqual(res.get("flagged"), 0)
        self.assertNotIn("check speaker", res["text"])

    def test_clean_lines_are_not_marked(self):
        rows, audio = scene([("Speaker 1", MAN, 1.2), ("Speaker 2", BOY, 1.2)] * 5)
        res = pc.run(rows, write_wav(audio))
        self.assertEqual(res.get("flagged"), 0)
        self.assertFalse(any("speaker_vote" in r for r in rows))


class Wiring(unittest.TestCase):
    def src(self, name):
        return (ROOT / name).read_text(encoding="utf-8").replace("\r\n", "\n")

    def test_the_analysis_runs_it_after_the_vote_and_before_the_speakers_are_set_up(self):
        s = self.src("longdub_service.py")
        self.assertIn("import pitch_check\n", s)
        v = s.index("vote = _vote_speakers(job, rows, wd, vocals_all)")
        p = s.index("pitch_check.run(rows, vocals_all)", v)
        i = s.index("_init_speakers(job, rows)", v)
        self.assertLess(v, p)
        self.assertLess(p, i)
        self.assertIn('_ev(job, "pitch_check"', s[p:i])

    def test_it_is_free_local_and_off_the_short_dub(self):
        s = self.src("pitch_check.py")
        for banned in ("requests", "httpx", "gemini", "urllib", "socket", "credit"):
            self.assertNotIn("import " + banned, s)
        self.assertIn("LONGDUB_PITCH_CHECK", s)
        for name in ("main.py", "whisper_service.py", "shortdub_operations.py"):
            self.assertNotIn("pitch_check", self.src(name))


if __name__ == "__main__":
    unittest.main()
