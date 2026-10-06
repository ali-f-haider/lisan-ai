"""A quiet steady background (a buzz, a room tone) that the separator takes away together with the voice comes back under the
speech, so it does not pulse with the original speaking ("ghost voice before his words"). Never for a changing background."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pause_bed as pb
import music_fill as mf

SR = mf.RATE
N = 30 * SR


def stereo(x):
    return np.stack([x, 0.97 * x], axis=1).astype(np.float32)


def db(x):
    return 10 * np.log10(np.mean(np.asarray(x, dtype=np.float64) ** 2) + 1e-14)


def sec(x, a, b):
    return x[int(a * SR):int(b * SR)]


class BedFloorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        rng = np.random.default_rng(4)
        t = np.arange(N) / SR
        self.buzz = 0.01 * rng.standard_normal(N)
        for k, f in enumerate((220, 441, 586, 880, 1320)):
            self.buzz += 0.004 / (1 + 0.3 * k) * np.sin(2 * np.pi * f * t + k)
        self.talk = [(2.0, 6.0), (9.0, 14.0), (18.0, 22.0), (25.0, 28.0)]
        self.voice = np.zeros(N)
        for a, b in self.talk:
            seg = slice(int(a * SR), int(b * SR))
            self.voice[seg] = 0.2 * np.sin(2 * np.pi * 180 * t[seg]) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t[seg]) ** 2) \
                + 0.02 * rng.standard_normal(int(b * SR) - int(a * SR))
        self.original = self.buzz + self.voice
        under = np.zeros(N)
        for a, b in self.talk:
            under[int(a * SR):int(b * SR)] = 1.0
        self.sep = self.buzz * (1.0 - 0.7 * under)                  # what the separator leaves: the buzz is 10 dB weaker under the voice
        self.spans = [(0.0, 30.0)]                                    # loose transcript: covers the pauses too
        for name, x in (('orig', self.original), ('sep', self.sep), ('voice', self.voice)):
            sf.write(self.d / f'{name}.wav', stereo(x), SR, subtype='PCM_16')

    def tearDown(self):
        self.tmp.cleanup()

    def run_floor(self, matched=None, **kw):
        out = self.d / 'out.wav'
        info = pb.bed_floor(matched or self.d / 'sep.wav', kw.get('original', self.d / 'orig.wav'), kw.get('vocals', self.d / 'voice.wav'),
                            self.d / 'sep.wav', self.spans, out)
        return info, out

    def test_the_buzz_is_held_under_the_speech(self):
        info, out = self.run_floor()
        self.assertTrue(info['ok'], info)
        y, _ = sf.read(out)
        y = y.mean(axis=1)
        pause = db(sec(self.buzz, 6.8, 8.4))
        for a, b in self.talk:
            under = db(sec(y, a + 0.6, b - 0.6))
            self.assertLess(abs(under - (pause + pb.FLOOR_DB)), 3.0, (a, b, under, pause))
        before = db(sec(self.sep, 9.7, 13.3))
        self.assertLess(before, pause - 8)                              # without the fix the buzz was ~10 dB down under the voice

    def test_pauses_and_a_background_that_is_loud_enough_are_not_touched(self):
        info, out = self.run_floor(matched=self.d / 'orig.wav')           # nothing missing: the original itself
        # (the original also holds the voice: it is only a "loud enough" background for this test)
        self.assertTrue(info['ok'], info)
        y, _ = sf.read(out)
        y = y.mean(axis=1)
        self.assertLess(abs(db(sec(y, 9.7, 13.3)) - db(sec(self.original, 9.7, 13.3))), 0.5)
        self.assertLess(abs(db(sec(y, 6.8, 8.4)) - db(sec(self.original, 6.8, 8.4))), 0.2)

    def test_a_changing_background_is_not_rebuilt(self):
        t = np.arange(N) / SR
        beat = (np.sin(2 * np.pi * 2 * t) > 0.5).astype(float)
        music = 0.1 * beat * np.sin(2 * np.pi * (200 + 300 * (np.floor(t * 4) % 3)) * t) + 0.003 * np.random.default_rng(2).standard_normal(N)
        sf.write(self.d / 'orig2.wav', stereo(music + self.voice), SR, subtype='PCM_16')
        sf.write(self.d / 'sep2.wav', stereo(music * (1.0 - 0.7 * (self.voice != 0))), SR, subtype='PCM_16')
        out = self.d / 'out2.wav'
        info = pb.bed_floor(self.d / 'sep2.wav', self.d / 'orig2.wav', self.d / 'voice.wav', self.d / 'sep2.wav', self.spans, out)
        self.assertFalse(info['ok'], info)
        self.assertFalse(out.exists())

    def test_another_recording_is_refused_and_missing_files_never_raise(self):
        other = 0.01 * np.random.default_rng(99).standard_normal(N)
        sf.write(self.d / 'other.wav', stereo(other), SR, subtype='PCM_16')
        info, out = self.run_floor(original=self.d / 'other.wav')
        self.assertFalse(info['ok'])
        info, out = self.run_floor(original=self.d / 'nope.wav')
        self.assertFalse(info['ok'])
        info, out = self.run_floor(vocals=self.d / 'nope.wav')
        self.assertFalse(info['ok'])

    def test_prepare_holds_the_buzz_and_reports_it(self):
        import dub_background
        dub = np.zeros(N)
        sf.write(self.d / 'dub.wav', stereo(dub), SR, subtype='PCM_16')
        r = dub_background.prepare(self.d / 'sep.wav', self.d / 'voice.wav', self.d / 'dub.wav', self.d, 'bf', self.spans,
                                   preserve_music=False, strict=False, original=self.d / 'orig.wav')
        self.assertIn('steady_bed', r['music_fill'])
        y, _ = sf.read(r['path'])
        y = y.mean(axis=1)
        pause = db(sec(y, 6.8, 8.4))
        self.assertLess(abs(db(sec(y, 9.7, 13.3)) - pause), 4.0)         # no pulse any more


if __name__ == '__main__':
    unittest.main()
