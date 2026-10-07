"""Voice matching: pitch measurement, gender/age rules, deterministic best-fit assignment."""
import json, os, sys, tempfile, unittest, wave
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_match as vm

SR = 16000


def voiced(f0, seconds=3.0, jitter=0.0, noise=0.0, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    f = f0 * (1 + jitter * np.sin(2 * np.pi * 3 * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = sum((1.0 / h) * np.sin(h * ph) for h in range(1, 12))
    x = x / np.abs(x).max() * 0.4 * (0.6 + 0.4 * np.sin(2 * np.pi * 2.5 * t) ** 2)     # syllable-like loudness
    return (x + noise * rng.standard_normal(x.size)).astype(np.float32)


def v(vid, gender, age=None, desc="", f0=None):
    return dict(voice_id=vid, gender=gender, age=age, descriptive=desc, f0=f0)


class PitchTests(unittest.TestCase):
    def test_pitch_is_measured_within_a_few_percent(self):
        for f0 in (95, 120, 180, 220, 300):
            p = vm.acoustic_profile(voiced(f0))
            self.assertTrue(p, f0)
            self.assertAlmostEqual(p["f0_median"] / f0, 1.0, delta=0.04, msg=f"{f0} -> {p['f0_median']}")

    def test_slow_pitch_movement_is_followed(self):
        p = vm.acoustic_profile(voiced(130, jitter=0.05))
        self.assertAlmostEqual(p["f0_median"] / 130, 1.0, delta=0.06)

    def test_noise_and_silence_give_no_pitch(self):
        rng = np.random.default_rng(3)
        self.assertEqual(vm.acoustic_profile(0.2 * rng.standard_normal(SR * 3).astype(np.float32)), {})
        self.assertEqual(vm.acoustic_profile(np.zeros(SR * 3, dtype=np.float32)), {})
        self.assertEqual(vm.acoustic_profile(np.zeros(100, dtype=np.float32)), {})

    def test_brighter_voice_has_higher_brightness(self):
        dark = vm.acoustic_profile(voiced(120))
        t = np.arange(SR * 3) / SR
        bright_sig = (voiced(120) + 0.5 * np.sin(2 * np.pi * 3000 * t) * (np.abs(voiced(120)) > 0.05)).astype(np.float32)
        self.assertGreater(vm.acoustic_profile(bright_sig)["brightness_hz"], dark["brightness_hz"])

    def test_gender_guess_leaves_the_overlap_unsure(self):
        self.assertEqual(vm.guess_gender(110), "male")
        self.assertEqual(vm.guess_gender(220), "female")
        self.assertIsNone(vm.guess_gender(165))
        self.assertIsNone(vm.guess_gender(None))


class LabelTests(unittest.TestCase):
    def test_age_words_map_to_one_scale(self):
        self.assertEqual(vm.age_band("middle_aged"), "adult")
        self.assertEqual(vm.age_band("Middle-Aged"), "adult")
        self.assertEqual(vm.age_band("old"), "senior")
        self.assertEqual(vm.age_band("elderly"), "senior")
        self.assertEqual(vm.age_band("young"), "young")
        self.assertIsNone(vm.age_band(""))
        self.assertIsNone(vm.age_band("purple"))

    def test_tone_words(self):
        self.assertEqual(vm.tone_words("Calm and WARM narrator"), ["warm", "calm"])
        self.assertEqual(vm.tone_words(["deep", "x"]), ["deep"])
        self.assertEqual(vm.tone_words(None), [])


class MatchTests(unittest.TestCase):
    VOICES = [v("m_young", "male", "young", f0=150), v("m_adult", "male", "middle_aged", f0=120),
              v("m_old", "male", "old", f0=105), v("f_young", "female", "young", f0=230),
              v("f_old", "female", "old", f0=190)]

    def prof(self, **kw):
        return [vm.voice_profile(x, {"f0_median": x.get("f0")}) for x in self.VOICES]

    def test_an_old_man_gets_the_old_male_voice_not_the_young_one(self):
        sp = dict(key="Grandpa", seconds=30, gender="male", age="senior", f0=110, tones=[])
        r = vm.assign([sp], self.prof())["Grandpa"]
        self.assertEqual(r["best"], "m_old")
        self.assertEqual(r["fit"], "good")
        self.assertIn("age", r["options"][0]["reasons"])

    def test_gender_is_never_crossed(self):
        sp = dict(key="A", seconds=5, gender="female", age="senior", f0=200, tones=[])
        r = vm.assign([sp], self.prof())["A"]
        self.assertEqual(r["best"], "f_old")
        self.assertTrue(all(o["voice_id"].startswith("f_") for o in r["options"]))

    def test_unknown_age_is_not_invented_pitch_decides(self):
        sp = dict(key="A", seconds=5, gender="male", age=None, f0=148, tones=[])
        self.assertEqual(vm.assign([sp], self.prof())["A"]["best"], "m_young")
        sp["f0"] = 104
        self.assertEqual(vm.assign([sp], self.prof())["A"]["best"], "m_old")

    def test_two_speakers_never_share_a_voice_and_the_main_speaker_chooses_first(self):
        a = dict(key="main", seconds=60, gender="male", age="senior", f0=108, tones=[])
        b = dict(key="side", seconds=5, gender="male", age="senior", f0=108, tones=[])
        r = vm.assign([b, a], self.prof())
        self.assertEqual(r["main"]["best"], "m_old")
        self.assertNotEqual(r["side"]["best"], "m_old")
        self.assertEqual(r["side"]["best"], "m_adult")          # the next closest age

    def test_nobody_is_left_without_a_voice_when_the_pool_is_small(self):
        a = dict(key="a", seconds=9, gender="female", age="young", f0=230, tones=[])
        b = dict(key="b", seconds=8, gender="female", age="young", f0=230, tones=[])
        c = dict(key="c", seconds=7, gender="female", age="young", f0=230, tones=[])
        r = vm.assign([a, b, c], self.prof())
        self.assertTrue(all(x["best"] for x in r.values()))
        self.assertEqual(r["a"]["best"], "f_young")

    def test_taken_voices_are_skipped(self):
        sp = dict(key="a", seconds=9, gender="male", age="senior", f0=108, tones=[])
        self.assertEqual(vm.assign([sp], self.prof(), taken={"m_old"})["a"]["best"], "m_adult")

    def test_rough_fit_is_reported_when_only_far_ages_exist(self):
        only = [vm.voice_profile(v("m_child", "male", "child", f0=250), {"f0_median": 250})]
        sp = dict(key="a", seconds=9, gender="male", age="senior", f0=105, tones=[])
        r = vm.assign([sp], only)["a"]
        self.assertEqual(r["best"], "m_child")
        self.assertEqual(r["fit"], "rough")

    def test_no_voice_of_the_gender_gives_none(self):
        only = [vm.voice_profile(v("f1", "female", "young"))]
        sp = dict(key="a", seconds=9, gender="male", age="young", f0=110, tones=[])
        self.assertEqual(vm.assign([sp], only)["a"], {"best": None, "options": [], "fit": "none"})

    def test_tone_breaks_a_tie(self):
        voices = [vm.voice_profile(v("a", "male", "adult", "harsh", f0=120), {"f0_median": 120}),
                  vm.voice_profile(v("b", "male", "adult", "calm, warm", f0=120), {"f0_median": 120})]
        sp = dict(key="s", seconds=9, gender="male", age="adult", f0=120, tones=["warm", "calm"])
        self.assertEqual(vm.assign([sp], voices)["s"]["best"], "b")

    def test_the_same_input_always_gives_the_same_answer(self):
        sps = [dict(key=k, seconds=10, gender="male", age="adult", f0=120, tones=[]) for k in "cba"]
        first = vm.assign(sps, self.prof())
        for _ in range(5):
            self.assertEqual(vm.assign(list(reversed(sps)), self.prof()), first)


class TraitTests(unittest.TestCase):
    def test_the_persons_choice_beats_the_listener_which_beats_pitch(self):
        prof = {"f0_median": 110}
        self.assertEqual(vm.speaker_traits(prof, {"gender": "female"}, chosen_gender="male")["gender"], "male")
        self.assertEqual(vm.speaker_traits(prof, {"gender": "female"})["gender"], "female")
        self.assertEqual(vm.speaker_traits(prof, {"gender": "female", "uncertain": True})["gender"], "male")
        self.assertEqual(vm.speaker_traits({"f0_median": 165}, {})["gender"], None)

    def test_age_comes_from_choice_or_a_sure_listener_never_from_pitch(self):
        self.assertEqual(vm.speaker_traits({"f0_median": 100}, {})["age"], None)
        self.assertEqual(vm.speaker_traits({}, {"age": "elderly"})["age"], "senior")
        self.assertIsNone(vm.speaker_traits({}, {"age": "elderly", "uncertain": True})["age"])
        self.assertEqual(vm.speaker_traits({}, {"age": "elderly"}, chosen_age="young")["age"], "young")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("DATA_DIR", None); self.tmp.cleanup()

    def wav(self, f0):
        path = Path(self.tmp.name) / f"p{f0}.wav"
        data = (voiced(f0) * 32767).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(data.tobytes())
        return path

    def test_previews_are_measured_once_and_remembered(self):
        calls = []
        def fetch(url):
            calls.append(url); return self.wav(int(url.split("/")[-1]))
        voices = [dict(voice_id="a", preview_url="x/110"), dict(voice_id="b", preview_url="x/220"), dict(voice_id="c")]
        got = vm.measure_missing(voices, fetch)
        self.assertAlmostEqual(got["a"]["f0_median"] / 110, 1, delta=0.04)
        self.assertAlmostEqual(got["b"]["f0_median"] / 220, 1, delta=0.04)
        self.assertNotIn("c", got)
        self.assertEqual(len(calls), 2)
        vm.measure_missing(voices, fetch)
        self.assertEqual(len(calls), 2)                                   # nothing measured twice

    def test_a_changed_preview_is_measured_again_and_a_failing_one_is_skipped(self):
        def fetch(url):
            if "bad" in url: raise OSError("down")
            return self.wav(150)
        vm.measure_missing([dict(voice_id="a", preview_url="x/110bad"), dict(voice_id="b", preview_url="y")], fetch)
        self.assertIn("b", vm.load_measurements()); self.assertNotIn("a", vm.load_measurements())

    def test_a_broken_cache_file_is_treated_as_empty(self):
        (Path(self.tmp.name) / "voice_catalog.json").write_text("{not json")
        self.assertEqual(vm.load_measurements(), {})
        vm.save_measurement("a", "u", {"f0_median": 100})
        self.assertEqual(vm.load_measurements()["a"]["f0_median"], 100)


if __name__ == "__main__":
    unittest.main()
