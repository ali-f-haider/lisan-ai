"""Text to voice, 1.96.4 (Ali): "Fit into __ seconds" (the pauses change first, then the speed, only within natural limits; free,
the voices are not generated again) and "Keep the times of my subtitles" (every line spoken at its subtitle's time)."""
import unittest
from unittest import mock

import text_voice as tv
from test_text_voice import Base, UID, mp3

SRT = """1
00:00:01,000 --> 00:00:02,500
Hello there.

2
00:00:04,000 --> 00:00:05,000
<i>Good evening.</i>

3
00:00:07,000 --> 00:00:07,400
Goodbye.
"""


class Plan(unittest.TestCase):
    def test_the_pauses_change_first(self):
        g, f, length = tv._fit_plan([10, 10, 10], [1, 1, 1], [0, 1, 1], 31.0)
        self.assertAlmostEqual(g, 0.5, places=2)
        self.assertAlmostEqual(f, 1.0, places=2)
        self.assertAlmostEqual(length, 31.0, places=1)

    def test_then_the_speed_within_the_natural_range(self):
        g, f, length = tv._fit_plan([10, 10, 10], [1, 1, 1], [0, 1, 1], 28.0)
        self.assertEqual(g, tv.FIT_PAUSE_MIN)
        self.assertAlmostEqual(length, 28.0, places=1)
        g, f, length = tv._fit_plan([10, 10, 10], [1, 1, 1], [0, 1, 1], 10.0)      # impossible: stops at the fastest natural speed
        self.assertAlmostEqual(length, 30 / tv.FIT_SPEED_MAX + 2 * tv.FIT_PAUSE_MIN, places=1)

    def test_longer_pauses_then_slower(self):
        g, f, length = tv._fit_plan([10, 10, 10], [1, 1, 1], [0, 1, 1], 60.0)
        self.assertEqual(g, tv.FIT_PAUSE_MAX)
        self.assertAlmostEqual(length, 30 / tv.FIT_SPEED_MIN + 2 * tv.FIT_PAUSE_MAX, places=1)


class Subtitles(unittest.TestCase):
    def test_the_file_gives_the_words_and_their_times(self):
        cues = tv.subtitle_cues(SRT)
        self.assertEqual([(c["start"], c["end"], c["text"]) for c in cues],
                         [(1.0, 2.5, "Hello there."), (4.0, 5.0, "Good evening."), (7.0, 7.4, "Goodbye.")])
        text, err = tv.read_text_file("a.srt", SRT.encode())
        self.assertEqual(text, "Hello there.\nGood evening.\nGoodbye.")


class Fit(Base):
    def ready(self, text="Hello there.\nGood evening.\n\nGoodbye.", cues=None):
        job, _ = tv.new_job(UID, text, "en", cues=cues)
        job["prepared"] = True
        for ln in job["lines"]:
            ln["arabic"], ln["emotion"] = "مَرْحَبًا " + ln["id"], "neutral"
        tv._save(job)
        tv.set_speakers(job, [{"id": "s1", "voice_id": "v1"}])
        return tv.load(job["id"])

    def speak(self, job, seconds=1.0):
        tone = mp3(seconds)
        with mock.patch("longdub_service._tts_with_retry", lambda *a, **k: (tone, "")):
            ok, err = tv.start_speak(tv.load(job["id"]))
            self.assertTrue(ok, err)
            return self.wait(job["id"])

    def test_fitting_is_free_and_reaches_the_target(self):
        job = self.speak(self.ready())
        natural = job["audio"]["seconds"]
        paid = dict(self.ledger.charges)
        ok, _ = tv.set_options(job, {"fit_seconds": round(natural - 0.8, 1)})
        self.assertTrue(ok)
        view = tv.public(tv.load(job["id"]))
        self.assertTrue(view["audio_outdated"])
        self.assertEqual(view["speak_price"], 0)                 # nothing is generated again
        job = self.speak(tv.load(job["id"]))
        self.assertEqual(self.ledger.charges, paid)
        fit = tv.public(job)["fit"]
        self.assertLessEqual(abs(fit["seconds"] - fit["target"]), tv.FIT_TOLERANCE + 0.05)
        self.assertLess(fit["pauses"], 1.0)

    def test_an_impossible_target_says_how_much_to_cut(self):
        job = self.speak(self.ready(), seconds=2.0)
        tv.set_options(job, {"fit_seconds": 3})
        fit = tv.public(self.speak(tv.load(job["id"]), seconds=2.0))["fit"]
        self.assertGreater(fit["over"], 0)
        self.assertGreater(fit["cut_chars"], 0)

    def test_switching_off_and_bad_values(self):
        job = self.ready()
        self.assertEqual(tv.set_options(job, {"fit_seconds": "abc"})[1][1], 400)
        self.assertEqual(tv.set_options(job, {"fit_seconds": 1})[1][1], 400)
        tv.set_options(job, {"fit_seconds": 30})
        self.assertEqual(tv.public(tv.load(job["id"]))["fit_seconds"], 30)
        tv.set_options(tv.load(job["id"]), {"fit_seconds": None})
        self.assertIsNone(tv.public(tv.load(job["id"]))["fit_seconds"])

    def test_each_line_starts_at_its_subtitle(self):
        text, _ = tv.read_text_file("a.srt", SRT.encode())
        job = self.ready(text, tv.subtitle_cues(SRT))
        view = tv.public(job)
        self.assertTrue(view["times_available"] and view["keep_times"])
        job = self.speak(job, seconds=0.8)
        timing = {t["id"]: t for t in job["audio"]["timing"]}
        self.assertAlmostEqual(timing["l1"]["start"], 1.0, delta=0.05)
        self.assertAlmostEqual(timing["l2"]["start"], 4.0, delta=0.05)
        self.assertAlmostEqual(timing["l3"]["start"], 7.0, delta=0.05)
        fit = tv.public(job)["fit"]
        self.assertTrue(fit["timed"])
        self.assertEqual([x["id"] for x in fit["late"]], ["l3"])     # 0.8 s of speech in a 0.4 s subtitle, even at 1.25x
        tv.set_options(tv.load(job["id"]), {"keep_times": False})
        self.assertFalse(tv.public(tv.load(job["id"]))["keep_times"])

    def test_a_changed_text_is_spoken_without_the_times(self):
        cues = tv.subtitle_cues(SRT)
        job, _ = tv.new_job(UID, "Hello there.\nGood evening, friend.\nGoodbye.", "en", cues=cues)
        self.assertFalse(tv.public(job)["times_available"])


if __name__ == "__main__":
    unittest.main()
