"""The background under the voices is never a loop of the film's own sound, never louder than it was, and the scene times are
read in seconds (Air Force One clip, October 2026: the scene answer came back in minutes, and from 1:45 to 3:00 the same few
pieces of the background, taken from up to a minute away, were laid under every line at -35 dB where the film had -50 dB)."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import music_fill as mf
import scene_context as sc
import language_prompts

SR = mf.RATE


def stereo(x):
    return np.stack([x, 0.97 * x], axis=1).astype(np.float32)


def noise(sec, seed, level=0.02):
    return stereo(level * np.random.default_rng(seed).standard_normal(int(sec * SR)))


def db(x):
    return 10 * np.log10(np.mean((np.asarray(x, np.float64)) ** 2) + 1e-20)


class SceneTimes(unittest.TestCase):
    AF1 = [(0, 0.8, "vehicle"), (0.8, 1.4, "medium_room"), (1.4, 2.4, "large_room"), (2.4, 2.8, "vehicle"), (2.8, 3.1, "large_room"),
           (3.1, 3.6, "vehicle"), (3.6, 4.6, "large_room"), (4.6, 5.2, "vehicle"), (5.2, 7.35, "large_room")]

    def test_an_answer_in_minutes_is_converted(self):
        note = {}
        raw = [{"t0": a, "t1": b, "setting": k, "confidence": "high", "place": "x"} for a, b, k in self.AF1]
        out = sc._clean_scenes(raw, 441.2, note)
        self.assertTrue(note.get("minutes"))
        self.assertEqual([(round(s["t0"]), round(s["t1"])) for s in out][:3], [(0, 48), (48, 84), (84, 144)])
        self.assertEqual(len(out), 9)                    # before: nine places in the first 5 s, the last one stretched to 7:21
        self.assertEqual(out[-1]["t1"], 441.2)

    def test_a_right_answer_is_kept_as_it_is(self):
        note = {}
        out = sc._clean_scenes([{"t0": 0, "t1": 200, "setting": "vehicle"}, {"t0": 200, "t1": 441, "setting": "large_room"}], 441.2, note)
        self.assertEqual(note, {})
        self.assertEqual([round(s["t1"]) for s in out], [200, 441])

    def test_clock_times_are_read(self):
        out = sc._clean_scenes([{"t0": "0:00", "t1": "1:24", "setting": "vehicle"}, {"t0": "1:24", "t1": "7:21", "setting": "studio"}], 441.2)
        self.assertEqual(round(out[0]["t1"]), 84)

    def test_an_answer_that_does_not_fit_is_not_stretched_over_the_video(self):
        note = {}
        self.assertEqual(sc._clean_scenes([{"t0": 0, "t1": 100, "setting": "vehicle"}], 441.2, note), [])
        self.assertIn("rejected", note)
        # the 83 s dining room case: one place in a short video is that video's place, so it is stretched (test_room_ceiling.py)
        self.assertEqual([(s["t0"], s["t1"]) for s in sc._clean_scenes([{"t0": 0, "t1": 1, "setting": "medium_room"}], 83.0)], [(0.0, 83.0)])
        # several places that all end in the first minute of a 7-minute video are not stretched over it
        self.assertEqual(sc._clean_scenes([{"t0": 0, "t1": 20, "setting": "vehicle"}, {"t0": 20, "t1": 60, "setting": "large_room"}], 441.2), [])

    def test_the_question_uses_seconds_everywhere(self):
        self.assertEqual(sc._transcript([{"start": 105.3, "text": "Get me TAC command right now please"}]),
                         "[105 s] Get me TAC command right now please")
        for prompt in (sc._prompt(441.2, True), language_prompts.scene_prompt(441.2, True, "en")):
            self.assertIn('"t0" and "t1" are SECONDS', prompt)
            self.assertIn("Never minutes", prompt)
        self.assertEqual(sc._prompt(441.2, False), language_prompts.scene_prompt(441.2, False, "en"))


class NoRepeats(unittest.TestCase):
    def test_a_piece_laid_into_one_hole_is_never_laid_into_another(self):
        pool = [(10.0, noise(3, 1)), (20.0, noise(3, 2))]
        taken = []
        pcm = np.zeros((40 * SR, 2), np.int16)
        ok1, _ = mf._texture_fill(pcm, 30.0, 32.0, -40.0, pool, allow_synth=False, taken=taken)
        self.assertTrue(ok1)
        self.assertTrue(taken)
        first = pcm[30 * SR:32 * SR].astype(np.float32).copy()
        ok2, note2 = mf._texture_fill(pcm, 34.0, 36.0, -40.0, pool, allow_synth=False, taken=taken)
        if ok2:
            second = pcm[34 * SR:36 * SR].astype(np.float32)
            c = np.corrcoef(first[:, 0], second[:, 0])[0, 1]
            self.assertLess(abs(c), 0.3)                 # not the same sound again
        free = sum(len(x) for t, x in pool for _, x in mf._free_parts(t, x, taken)) / SR
        self.assertLess(free, 6.0)                       # what was laid is no longer free

    def test_free_parts_leave_out_what_was_used(self):
        parts = mf._free_parts(10.0, noise(4, 3), [(11.0, 12.0)])
        self.assertEqual([round(t, 2) for t, _ in parts], [10.0, 12.0])
        self.assertEqual([round(len(x) / SR, 2) for _, x in parts], [1.0, 2.0])

    def test_pieces_only_come_from_near_the_hole_and_only_once(self):
        timed = [(5.0, noise(3, 4)), (100.0, noise(3, 5))]          # one pause next to the hole, one 90 s away
        pieces, _ = mf._matching_pieces(timed, 9.0, 11.0, timed_out=True)
        self.assertTrue(pieces)
        self.assertTrue(all(t < 20 for t, _ in pieces))              # never the far one
        pieces, note = mf._matching_pieces(timed, 9.0, 11.0, taken=[(0.0, 50.0)], timed_out=True)
        self.assertEqual(pieces, [])
        self.assertIn("within", note)


class NeverLouderThanItWas(unittest.TestCase):
    def test_a_local_fill_is_held_at_the_level_the_background_had(self):
        pcm = np.zeros((20 * SR, 2), np.int16)
        ok, note = mf._texture_fill(pcm, 5.0, 7.0, -30.0, [noise(8, 6, 0.05)], cap_db=-50.0)
        self.assertTrue(ok, note)
        self.assertLess(db(pcm[5 * SR:7 * SR].astype(np.float32) / 32768.0), -49.0)

    def test_the_model_result_is_held_too(self):
        self.assertIn("cap_db", mf._fill_one.__code__.co_varnames)

    def _track(self, d, hole_ceiling):
        """Music-like sound at -26 dB with one silenced hole 10-14 s; the separated background had hole_ceiling there."""
        x = noise(30, 7, 0.07)
        muted = x.copy()
        muted[10 * SR:14 * SR] = 0
        ceil = x.copy()
        ceil[10 * SR:14 * SR] *= 10 ** ((hole_ceiling - db(x[10 * SR:14 * SR])) / 20)
        for name, y in (("muted", muted), ("ceil", ceil), ("ref", x)):
            sf.write(str(d / f"{name}.wav"), y, SR, subtype="PCM_16")
        return [(10.0, 14.0)]

    def test_a_hole_that_was_silent_gets_nothing_and_is_not_missing(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            spans = self._track(d, -70.0)
            calls = []
            info = mf.fill(d / "muted.wav", d / "out.wav", spans, "key", runner=lambda *a: calls.append(a),
                           allow=lambda: True, ceiling=d / "ceil.wav")
            self.assertEqual(calls, [])                  # the model is not asked (nothing is charged)
            gaps = [g for g in info["gaps"] if 9 <= g["start"] <= 11]
            self.assertTrue(gaps and all(g.get("optional") and not g["ok"] for g in gaps), info)
            self.assertEqual(info["found"], 0)

    def test_the_model_is_not_asked_where_the_film_had_the_music_down(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            spans = self._track(d, -50.0)
            calls = []
            with unittest.mock.patch.object(mf, "LOCAL_FILL", False):
                info = mf.fill(d / "muted.wav", d / "out.wav", spans, "key", runner=lambda *a: calls.append(a),
                               allow=lambda: True, ceiling=d / "ceil.wav", prompt="instrumental")
            self.assertEqual(calls, [])
            self.assertTrue(any("not asked" in g["note"] for g in info["gaps"]), info)


class Wiring(unittest.TestCase):
    def test_the_background_before_the_silencing_is_passed_to_the_repair(self):
        src = (ROOT / "dub_background.py").read_text(encoding="utf-8")
        self.assertIn("ceiling=bg", src)
        sec = (ROOT / "section_background.py").read_text(encoding="utf-8")
        self.assertIn("local['ceiling'] = ceil", sec)
        fill_src = (ROOT / "music_fill.py").read_text(encoding="utf-8")
        self.assertIn("taken=taken, cap_db=cap", fill_src)


def music(sec, seed=1):
    """Notes that change every 0.4 s over a little noise: music, not a steady engine."""
    rng = np.random.default_rng(seed)
    n = int(sec * SR)
    t = np.arange(n) / SR
    x = np.zeros(n)
    step = int(0.4 * SR)
    for k in range(0, n, step):
        seg = slice(k, min(n, k + step))
        tt = t[seg] - t[k]
        for f in rng.choice([220, 247, 262, 294, 330, 349, 392, 440, 494, 523], 3):
            x[seg] += 0.03 * np.sin(2 * np.pi * f * tt) * np.exp(-tt * 3)
    x += 0.003 * rng.standard_normal(n)
    return stereo(x)


class PriceMatchesTheRepair(unittest.TestCase):
    """The price shown before the dub counts only the holes the music model will really be asked about."""

    def _files(self, d, cap):
        x = music(30)
        muted = x.copy()
        muted[10 * SR:14 * SR] = 0
        ceil = x.copy()
        if cap is not None:
            ceil[int(9.5 * SR):int(14.5 * SR)] *= 10 ** ((cap - db(x[10 * SR:14 * SR])) / 20)
        sf.write(str(d / "muted.wav"), muted, SR, subtype="PCM_16")
        sf.write(str(d / "ceil.wav"), ceil, SR, subtype="PCM_16")

    def _count_and_calls(self, cap):
        import dub_background
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            self._files(d, cap)
            spans = [(10.0, 14.0)]
            count = dub_background.count_repairs(d / "muted.wav", spans, d / "raw.pcm", original=d / "ceil.wav")
            calls = []
            with unittest.mock.patch.object(mf, "LOCAL_FILL", False), \
                 unittest.mock.patch.object(mf, "_fill_one", lambda *a, **k: (calls.append(a[1:3]) or (True, "mock"))):
                mf.fill(d / "muted.wav", d / "out.wav", spans, "key", allow=lambda: True, ceiling=d / "ceil.wav", prompt="instrumental")
            return count, len(calls)

    def test_a_hole_with_music_under_the_voice_is_priced_and_repaired(self):
        self.assertEqual(self._count_and_calls(None), (1, 1))

    def test_a_hole_where_the_film_had_the_music_down_is_neither_priced_nor_repaired(self):
        self.assertEqual(self._count_and_calls(-50.0), (0, 0))

    def test_a_silent_hole_is_neither_priced_nor_repaired(self):
        self.assertEqual(self._count_and_calls(-70.0), (0, 0))


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
