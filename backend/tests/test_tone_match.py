"""Tone match (tone_match.py): the dubbed voices get the tonal colour of the original. Invented signals, offline (needs ffmpeg)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tone_match as tm

ROOT = Path(__file__).resolve().parents[1]
RATE = tm.RATE


def speechlike(seconds, tilt=0.0, seed=1, burst=1.0, gap=0.5):
    """Bursts of noise with a spectral tilt (positive: brighter), separated by silence. Returns (signal, windows)."""
    from scipy.signal import butter, sosfilt
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    x = rng.standard_normal(n)
    lo = sosfilt(butter(2, 1500 / (RATE / 2), "low", output="sos"), x)
    hi = x - lo
    y = lo + (10 ** (tilt / 20.0)) * hi
    env = np.zeros(n)
    wins, t = [], 0.2
    while t + burst < seconds:
        env[int(t * RATE):int((t + burst) * RATE)] = 1.0
        wins.append((t, t + burst))
        t += burst + gap
    y = y * env
    return (0.1 * y / np.max(np.abs(y))).astype(np.float32), wins


def write(path, x):
    import soundfile as sf
    sf.write(str(path), x, RATE, subtype="PCM_16")


class Pure(unittest.TestCase):
    def test_a_pure_level_difference_is_not_tone(self):
        base = np.linspace(-30, -50, len(tm.CENTERS))
        off, raw = tm.plan_offsets(base + 6.0, base)
        self.assertEqual(off, [0.0] * len(tm.CENTERS))

    def test_a_brighter_original_brightens_the_dub_gently_and_never_beyond_the_limit(self):
        base = np.linspace(-30, -50, len(tm.CENTERS))
        tilt = np.linspace(-6, 12, len(tm.CENTERS))              # the original is 18 dB brighter from bottom to top
        off, raw = tm.plan_offsets(base + tilt, base)
        self.assertLess(off[0], 0)
        self.assertGreater(off[5], -1.0)
        self.assertLessEqual(max(off), tm.MAX_DB)
        self.assertGreaterEqual(min(off), -tm.MAX_DB)
        self.assertLessEqual(max(off[i] for i, c in enumerate(tm.CENTERS) if c >= tm.HIGH_FROM_HZ), tm.HIGH_BOOST_MAX_DB)
        self.assertEqual(off[:4], sorted(off[:4]))                    # rises smoothly towards the bright end
        self.assertGreater(raw[-1], raw[0])

    def test_a_band_the_separation_emptied_is_not_copied(self):
        base = np.full(len(tm.CENTERS), -40.0)
        orig = base.copy()
        orig[-3:] = -100.0                                        # nothing left up there in the original's separated voices
        off, _raw = tm.plan_offsets(orig, base)
        self.assertTrue(all(abs(x) < 1.5 for x in off[-3:]))      # only the smoothing of the neighbour can touch the edge, never a -4 dB cut

    def test_small_differences_are_left_alone(self):
        base = np.full(len(tm.CENTERS), -40.0)
        off, _ = tm.plan_offsets(base + np.array([0.5, -0.4, 0.6, 0, 0, 0, 0, 0, 0, 0, 0.5]), base)
        self.assertEqual(off, [0.0] * len(tm.CENTERS))

    def test_filter_chain(self):
        self.assertEqual(tm.filter_chain([0.0] * len(tm.CENTERS)), "")
        off = [0.0] * len(tm.CENTERS)
        off[5] = 2.5
        off[2] = -1.5
        chain = tm.filter_chain(off, pre_gain_db=-1.2)
        self.assertEqual(chain, "volume=-1.20dB,equalizer=f=250:t=o:w=0.8:g=-1.5,equalizer=f=1000:t=o:w=0.8:g=2.5")


@unittest.skipUnless(os.system("ffmpeg -version > /dev/null 2>&1") == 0, "ffmpeg not available")
class WithAudio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_measure_sees_the_tilt_and_ignores_the_pauses(self):
        flat, wins = speechlike(30, tilt=0.0)
        bright, _ = speechlike(30, tilt=9.0)
        write(self.d / "flat.wav", flat)
        write(self.d / "bright.wav", bright)
        mf, mb = tm.measure(self.d / "flat.wav", wins), tm.measure(self.d / "bright.wav", wins)
        self.assertAlmostEqual(mf["seconds"], 20.0, delta=2.0)               # 14 bursts of 1 s, in the windows
        top, mid = len(tm.CENTERS) - 1, 1
        # (both are scaled to the same peak, so compare the shape: the top against the bottom)
        shape = (mb["bands"][top] - mb["bands"][mid]) - (mf["bands"][top] - mf["bands"][mid])
        self.assertGreater(shape, 6.0)

    def test_too_little_speech_gives_nothing(self):
        flat, wins = speechlike(6, tilt=0.0)
        write(self.d / "a.wav", flat)
        self.assertIsNone(tm.measure(self.d / "a.wav", wins))
        self.assertIsNone(tm.measure(self.d / "a.wav", []))

    def test_the_dub_moves_towards_the_original_and_nothing_else_changes(self):
        orig, wins = speechlike(40, tilt=9.0, seed=1)
        dub, _ = speechlike(40, tilt=0.0, seed=2)
        write(self.d / "orig.wav", orig)
        write(self.d / "dub.wav", dub)
        lines = [{"start": a, "allowed": z - a, "orig_start": a, "end": z} for a, z in wins]
        res = tm.run(self.d / "orig.wav", self.d / "dub.wav", self.d / "out.wav", lines)
        self.assertTrue(res["applied"], res["why"])
        self.assertTrue((self.d / "out.wav").exists())
        import soundfile as sf
        a, _ = sf.read(str(self.d / "dub.wav"))
        b, rate = sf.read(str(self.d / "out.wav"))
        self.assertEqual(rate, RATE)
        self.assertEqual(len(a), len(b))                                       # same length, to the sample
        before = tm.measure(self.d / "dub.wav", wins)["bands"]
        after = tm.measure(self.d / "out.wav", wins)["bands"]
        target = tm.measure(self.d / "orig.wav", wins)["bands"]

        def gap(x):
            d = np.array(target) - np.array(x)
            d = d - np.median(d[4:8])
            return float(np.sum(np.abs(d)))
        self.assertLess(gap(after), gap(before))
        self.assertLessEqual(float(np.max(np.abs(b))), 1.0)
        self.assertIn("changes in dB", res["why"])

    def test_a_click_stays_exactly_where_it_was(self):
        orig, wins = speechlike(40, tilt=9.0, seed=1)
        dub, _ = speechlike(40, tilt=0.0, seed=2)
        dub = dub.copy()
        dub[int(2.0 * RATE) - 5000:int(2.0 * RATE) + 5000] = 0.0              # a quiet stretch with one click in it
        dub[int(2.0 * RATE)] = 0.5
        write(self.d / "orig.wav", orig)
        write(self.d / "dub.wav", dub)
        lines = [{"start": a, "allowed": z - a, "orig_start": a, "end": z} for a, z in wins]
        res = tm.run(self.d / "orig.wav", self.d / "dub.wav", self.d / "out.wav", lines)
        self.assertTrue(res["applied"], res["why"])
        import soundfile as sf
        b, _ = sf.read(str(self.d / "out.wav"))
        seg = np.abs(b[int(2.0 * RATE) - 3000:int(2.0 * RATE) + 3000])
        self.assertLess(abs(int(np.argmax(seg)) - 3000), 20)                   # a few samples at most: no delay

    def test_a_dub_that_already_matches_is_left_alone(self):
        orig, wins = speechlike(40, tilt=0.0, seed=1)
        dub, _ = speechlike(40, tilt=0.0, seed=2)
        write(self.d / "orig.wav", orig)
        write(self.d / "dub.wav", dub)
        lines = [{"start": a, "allowed": z - a, "orig_start": a, "end": z} for a, z in wins]
        res = tm.run(self.d / "orig.wav", self.d / "dub.wav", self.d / "out.wav", lines)
        self.assertFalse(res["applied"])
        self.assertFalse((self.d / "out.wav").exists())
        self.assertIn("already has", res["why"])

    def test_loud_boosts_never_clip(self):
        orig, wins = speechlike(40, tilt=12.0, seed=1)
        dub, _ = speechlike(40, tilt=0.0, seed=2)
        dub = dub / np.max(np.abs(dub)) * 0.99
        write(self.d / "orig.wav", orig)
        write(self.d / "dub.wav", dub.astype(np.float32))
        lines = [{"start": a, "allowed": z - a, "orig_start": a, "end": z} for a, z in wins]
        res = tm.run(self.d / "orig.wav", self.d / "dub.wav", self.d / "out.wav", lines)
        self.assertTrue(res["applied"], res["why"])
        import soundfile as sf
        b, _ = sf.read(str(self.d / "out.wav"))
        self.assertLess(float(np.max(np.abs(b))), 0.9999)

    def test_bad_input_never_raises(self):
        res = tm.run(self.d / "missing.wav", self.d / "missing2.wav", self.d / "o.wav", [{"start": 0, "allowed": 2, "orig_start": 0, "end": 2}])
        self.assertFalse(res["applied"])
        self.assertTrue(res["why"])
        self.assertFalse(tm.run(self.d / "a", self.d / "b", self.d / "c", [])["applied"])
        self.assertFalse(tm.run(self.d / "a", self.d / "b", self.d / "c", [{"x": 1}, {}])["applied"])


class Wiring(unittest.TestCase):
    def test_the_long_dub_matches_the_tone_before_adding_the_room(self):
        src = (ROOT / "longdub_service.py").read_text(encoding="utf-8").replace("\r\n", "\n")
        i = src.index("tone_match.run(wd / \"vocals_mono.wav\", dub_full, _tone_out, kept)")
        j = src.index("room_acoustics.apply_to_file(")
        c = src.index("_concat_wavs(mixed, dub_full)")
        self.assertLess(c, i)
        self.assertLess(i, j)
        self.assertIn('_ev(job, "tone_match"', src[i:i + 900])

    def test_the_short_dub_is_not_touched(self):
        for p in ROOT.glob("*.py"):
            if p.name in ("longdub_service.py", "tone_match.py"):
                continue
            self.assertNotIn("import tone_match", p.read_text(encoding="utf-8", errors="ignore"), p.name)


class Switch(unittest.TestCase):
    def test_switched_off(self):
        old = tm.ENABLED
        tm.ENABLED = False
        try:
            self.assertEqual(tm.run("a", "b", "c", [{"start": 0, "allowed": 1, "orig_start": 0, "end": 1}])["why"][:12], "switched off")
        finally:
            tm.ENABLED = old


if __name__ == "__main__":
    unittest.main()
