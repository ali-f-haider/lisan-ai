"""Short dub uses the same one-level-per-speaker logic as the long dub (voice_level.py)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eleven_service as es


def lines(seed=3):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(40):
        sp = "Speaker 1" if i % 2 == 0 else "Speaker 2"
        usual = -38.0 if sp == "Speaker 1" else -33.0
        o = round(usual + rng.normal(0, 4.5), 1)
        d = round(-34.0 + rng.normal(0, 3.5), 1)
        out.append({"segment_id": f"s{i}", "speaker": sp, "orig_db": o, "dub_db": d,
                    "auto_gain_db": round(max(-10.0, min(10.0, o - d)), 1)})
    return out


class LevelLinesTests(unittest.TestCase):
    def final(self, ms, sp):
        return [m["dub_db"] + m["auto_gain_db"] for m in ms if m["speaker"] == sp]

    def test_each_speaker_gets_steadier_and_stays_at_the_original_level(self):
        ms = lines()
        before = {sp: np.std(self.final(ms, sp)) for sp in ("Speaker 1", "Speaker 2")}
        es._level_lines_meta("job1", ms)
        for sp, usual in (("Speaker 1", -38.0), ("Speaker 2", -33.0)):
            self.assertLess(np.std(self.final(ms, sp)), before[sp] - 0.5)
            self.assertAlmostEqual(np.mean(self.final(ms, sp)), usual, delta=1.5)
        self.assertEqual(sorted(es.VOICE_ANCHORS["job1"]), ["Speaker 1", "Speaker 2"])

    def test_the_limit_of_the_sliders_is_kept(self):
        ms = lines()
        ms[0].update(orig_db=-10.0, dub_db=-60.0)
        es._level_lines_meta("job2", ms)
        self.assertLessEqual(max(abs(m["auto_gain_db"]) for m in ms), es.SHORT_GAIN_MAX_DB)

    def test_lines_without_a_measured_level_are_left_alone(self):
        ms = lines()
        ms[3].update(orig_db=None, auto_gain_db=0.0)
        es._level_lines_meta("job3", ms)
        self.assertEqual(ms[3]["auto_gain_db"], 0.0)

    def test_a_speaker_with_few_lines_follows_the_original_exactly(self):
        ms = [{"segment_id": f"a{i}", "speaker": "S", "orig_db": -30.0 - i, "dub_db": -36.0, "auto_gain_db": 6.0 - i} for i in range(3)]
        es._level_lines_meta("job4", ms)
        self.assertEqual([m["auto_gain_db"] for m in ms], [6.0, 5.0, 4.0])

    def test_garbage_never_raises(self):
        es._level_lines_meta("job5", None)
        es._level_lines_meta("job5", [{"x": 1}])


class ReSpokenLineTests(unittest.TestCase):
    def setUp(self):
        self.saved = (es.resolve_job_speech, es.job_speech_spans, es._orig_level_db, es.measure_loudness_db)
        es.resolve_job_speech = lambda j: "src.wav"
        es.job_speech_spans = lambda j: [(0, 5)]

    def tearDown(self):
        es.resolve_job_speech, es.job_speech_spans, es._orig_level_db, es.measure_loudness_db = self.saved

    def run_line(self, orig, dub, speaker="Speaker 1"):
        es._orig_level_db = lambda *a, **k: orig
        es.measure_loudness_db = lambda *a, **k: dub
        return es._measure_line_loudness("jobR", SimpleNamespace(segment_id="x", start=0.0, speaker=speaker), "f.wav", 2.0)["auto_gain_db"]

    def test_without_a_usual_level_it_follows_the_original(self):
        es.VOICE_ANCHORS.pop("jobR", None)
        self.assertEqual(self.run_line(-30.0, -36.0), 6.0)
        self.assertEqual(self.run_line(-30.0, -55.0), es.SHORT_GAIN_MAX_DB)

    def test_with_a_usual_level_the_line_follows_only_in_part(self):
        es.VOICE_ANCHORS["jobR"] = {"Speaker 1": -40.0}
        g = self.run_line(-30.0, -40.0)                  # the original is 10 dB above the speaker's usual level
        self.assertAlmostEqual(g, 6.0, places=1)         # follows 60 %, within the 6 dB spread
        self.assertEqual(self.run_line(-40.0, -45.0, speaker="Speaker 2"), 5.0)      # another speaker has no usual level here: exact


if __name__ == "__main__":
    unittest.main()
