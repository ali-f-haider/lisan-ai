"""A place with life in it (restaurant, street): when the separator takes the sound away under the speech, REAL pieces of the scene's own
pauses are put back, so the sound does not collapse every time somebody speaks (and nothing is made up: wind-like noise)."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import music_fill as mf
import pause_bed as pb
from test_crowd_not_made_up import crowd, stereo

SR = mf.RATE


def level_db(x):
    return 10 * np.log10(float(np.mean(np.asarray(x, dtype=np.float64) ** 2)) + 1e-14)


class PiecesTests(unittest.TestCase):
    def pool(self):
        return [stereo(crowd(12 * SR, 11)), stereo(crowd(12 * SR, 12))]

    def test_every_piece_is_used_once_before_any_is_repeated(self):
        p = pb._Pieces(self.pool())
        n = len(p.grains)
        self.assertGreater(n, 6)
        seen = []
        for _ in range(n):
            p._next()
            seen.append(p.last)
        self.assertEqual(sorted(seen), sorted(set(seen)))
        self.assertEqual(len(seen), n)

    def test_take_gives_exactly_what_was_asked_and_goes_on_for_ever(self):
        p = pb._Pieces(self.pool())
        for m in (1000, 44100, 5 * SR, 60 * SR):
            out = p.take(m)
            self.assertEqual(out.shape, (m, 2))
            self.assertTrue(np.isfinite(out).all())

    def test_an_event_in_the_pool_is_not_laid_under_the_speech(self):
        a = stereo(crowd(12 * SR, 11))
        a[5 * SR:int(5.3 * SR)] *= 40.0                           # a crash / a shout
        p = pb._Pieces([a, stereo(crowd(12 * SR, 12))])
        loud = max(level_db(g) for g in p.grains)
        self.assertLess(loud, 10 * np.log10(p.power) + 1.0)        # every piece is at the usual level

    def test_too_little_sound_is_not_enough_to_lay_under_the_speech(self):
        p = pb._Pieces([stereo(crowd(int(0.8 * SR), 1))])
        self.assertLess(p.seconds(), 2 * pb.CROWD_GRAIN_SEC)          # bed_floor then leaves the background as it is


class CrowdBedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.N = 60 * SR
        t = np.arange(self.N) / SR
        rng = np.random.default_rng(8)
        self.talk = [(4.0, 9.0), (14.0, 20.0), (26.0, 33.0), (40.0, 46.0), (50.0, 56.0)]
        self.voice = np.zeros(self.N)
        for a, b in self.talk:
            s = slice(int(a * SR), int(b * SR))
            self.voice[s] = 0.2 * np.sin(2 * np.pi * 180 * t[s]) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t[s]) ** 2) + 0.02 * rng.standard_normal(int(b * SR) - int(a * SR))
        self.bed = crowd(self.N, 5)
        self.under = np.zeros(self.N)
        for a, b in self.talk:
            self.under[int(a * SR):int(b * SR)] = 1.0
        sf.write(self.d / 'voice.wav', stereo(self.voice), SR, subtype='PCM_16')
        sf.write(self.d / 'orig.wav', stereo(self.bed + self.voice), SR, subtype='PCM_16')
        # the separator took the whole crowd away under the speech
        sf.write(self.d / 'sep.wav', stereo(self.bed * (1.0 - 0.995 * self.under)), SR, subtype='PCM_16')

    def tearDown(self):
        self.tmp.cleanup()

    def run_bed(self, **kw):
        return pb.bed_floor(self.d / 'sep.wav', self.d / 'orig.wav', self.d / 'voice.wav', self.d / 'sep.wav', [(0.0, 60.0)], self.d / 'out.wav', **kw)

    def test_the_sound_does_not_collapse_under_speech(self):
        before, _ = sf.read(self.d / 'sep.wav')
        info = self.run_bed()
        self.assertTrue(info['ok'], info)
        out, _ = sf.read(self.d / 'out.wav')
        out, before = out.mean(axis=1), before.mean(axis=1)
        sp = np.concatenate([np.arange(int((a + 0.5) * SR), int((b - 0.5) * SR)) for a, b in self.talk])
        pa = np.concatenate([np.arange(int(a * SR), int((b) * SR)) for a, b in [(10.5, 13.0), (21.0, 25.0), (34.0, 39.0), (47.0, 49.0)]])
        drop_before = level_db(before[pa]) - level_db(before[sp])
        drop_after = level_db(out[pa]) - level_db(out[sp])
        self.assertGreater(drop_before, 15.0)                       # the problem: it nearly vanished under the speech
        self.assertLess(drop_after, 6.0)                            # a few dB under, not a collapse
        self.assertGreater(drop_after, -1.0)                        # and never louder than the pauses

    def test_the_pauses_are_not_touched(self):
        self.run_bed()
        out, _ = sf.read(self.d / 'out.wav')
        before, _ = sf.read(self.d / 'sep.wav')
        pa = slice(int(10.5 * SR), int(13.0 * SR))
        self.assertLess(float(np.max(np.abs(out[pa] - before[pa]))), 2e-3)

    def test_the_sound_that_is_put_back_is_the_sound_of_the_pauses(self):
        self.run_bed()
        out, _ = sf.read(self.d / 'out.wav')
        before, _ = sf.read(self.d / 'sep.wav')
        added = (out - before).mean(axis=1)
        sp = np.concatenate([np.arange(int((a + 0.5) * SR), int((b - 0.5) * SR)) for a, b in self.talk])
        ref = self.bed[int(10.5 * SR):int(13.0 * SR)]

        def bands(x):
            f = np.abs(np.fft.rfft(x[:len(x) // 4096 * 4096].reshape(-1, 4096) * np.hanning(4096), axis=1)) ** 2
            fr = np.fft.rfftfreq(4096, 1 / SR)
            return np.array([10 * np.log10(f[:, (fr >= lo) & (fr < hi)].mean() + 1e-20) for lo, hi in ((100, 400), (400, 1500), (1500, 5000))])
        d = bands(added[sp]) - bands(ref)
        d = d - d.mean()
        self.assertLess(float(np.max(np.abs(d))), 4.0)              # the same colour as the pauses (it is a piece of them)

    def test_reason_and_numbers_are_in_the_report(self):
        info = self.run_bed()
        self.assertIn('nothing made up', info['reason'])
        self.assertGreater(info['crowd_bed']['sec_laid'], 10)
        import json
        json.dumps(info)                                             # the report goes into the job log: plain numbers only

    def test_a_speech_stretch_the_separator_left_alone_gets_nothing_added(self):
        sf.write(self.d / 'sep.wav', stereo(self.bed), SR, subtype='PCM_16')       # the stem still holds the full crowd
        before, _ = sf.read(self.d / 'sep.wav')
        info = self.run_bed()
        self.assertTrue(info['ok'], info)
        out, _ = sf.read(self.d / 'out.wav')
        sp = slice(int(15 * SR), int(19 * SR))
        self.assertLess(abs(level_db(out[sp]) - level_db(before[sp])), 1.5)


if __name__ == '__main__':
    unittest.main()
