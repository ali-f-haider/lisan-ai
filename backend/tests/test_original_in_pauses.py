"""The original recording is laid in wherever nobody speaks: the separator rewrites even a pure background (a faint metallic
noise, part of a quiet room tone gone), the original sound of a pause is the real thing. Under the speech the separated
background stays."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dub_background as db_mod
import music_fill as mf

SR = mf.RATE
N = 30 * SR


def stereo(x):
    return np.stack([x, x], axis=1).astype(np.float32)


def rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


class OriginalInPausesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        rng = np.random.default_rng(1)
        self.room = 0.02 * rng.standard_normal(N)                       # the real background
        self.spans = [(8.0, 12.0), (16.0, 22.0)]
        t = np.arange(N) / SR
        self.voice = np.zeros(N)
        for a, b in self.spans:
            self.voice[int(a * SR):int(b * SR)] = 0.3 * np.sin(2 * np.pi * 220 * t[int(a * SR):int(b * SR)]) * rng.uniform(0.6, 1.0)
        self.original = self.room + self.voice
        self.base = 0.5 * self.room                                      # what the separator made of it: 6 dB too low
        sf.write(self.d / 'orig.wav', stereo(self.original), SR, subtype='PCM_16')
        sf.write(self.d / 'base.wav', stereo(self.base), SR, subtype='PCM_16')
        sf.write(self.d / 'voice.wav', stereo(self.voice), SR, subtype='PCM_16')

    def tearDown(self):
        self.tmp.cleanup()

    def blend(self, **kw):
        out = self.d / 'out.wav'
        info = db_mod.original_in_pauses(kw.get('base', self.d / 'base.wav'), kw.get('original', self.d / 'orig.wav'),
                                         kw.get('vocals', self.d / 'voice.wav'), kw.get('spans', self.spans), out)
        return info, out

    def test_pauses_are_the_original_and_the_speech_keeps_the_separated_background(self):
        info, out = self.blend()
        self.assertTrue(info['ok'], info)
        y, _ = sf.read(out)
        y = y.mean(axis=1)
        self.assertEqual(abs(len(y) - N) <= 2, True)
        pause = slice(int(2 * SR), int(6 * SR))
        self.assertLess(abs(rms(y[pause]) / rms(self.room[pause]) - 1), 0.03)          # the real room, not the half-level separation
        speech = slice(int(9 * SR), int(11 * SR))
        self.assertLess(abs(rms(y[speech]) / rms(self.base[speech]) - 1), 0.03)        # the separated background under the speaker
        self.assertTrue(0.3 < info['share'] < 0.8, info)

    def test_no_jump_at_the_edges(self):
        info, out = self.blend()
        y, _ = sf.read(out)
        y = y.mean(axis=1)
        for a, b in self.spans:
            for edge in (a - 0.25, b + 0.25):
                seg = y[int(edge * SR) - 400:int(edge * SR) + 400]
                self.assertLess(np.abs(np.diff(seg)).max(), 0.2)

    def test_another_length_is_refused(self):
        sf.write(self.d / 'short.wav', stereo(self.original[:20 * SR]), SR, subtype='PCM_16')
        info, out = self.blend(original=self.d / 'short.wav')
        self.assertFalse(info['ok'])
        self.assertFalse(out.exists())

    def test_a_different_recording_is_refused(self):
        other = 0.02 * np.random.default_rng(77).standard_normal(N) + self.voice * 0.0
        sf.write(self.d / 'other.wav', stereo(other), SR, subtype='PCM_16')
        info, out = self.blend(original=self.d / 'other.wav')
        self.assertFalse(info['ok'], info)
        self.assertFalse(out.exists())

    def test_no_pauses_nothing_to_do(self):
        info, out = self.blend(spans=[(0.0, 30.0)])
        self.assertFalse(info['ok'])

    def test_missing_files_never_raise(self):
        info, _ = self.blend(original=self.d / 'nope.wav')
        self.assertFalse(info['ok'])
        info, _ = self.blend(vocals=self.d / 'nope.wav')
        self.assertFalse(info['ok'])

    def test_a_voice_the_map_missed_is_not_blended_in(self):
        # the speech map misses a speaker at 24-26 s: the separated voices still show it, so the original is not laid there
        t = np.arange(N) / SR
        extra = np.zeros(N)
        extra[24 * SR:26 * SR] = 0.3 * np.sin(2 * np.pi * 330 * t[24 * SR:26 * SR])
        voice = self.voice + extra
        sf.write(self.d / 'orig2.wav', stereo(self.room + voice), SR, subtype='PCM_16')
        sf.write(self.d / 'voice2.wav', stereo(voice), SR, subtype='PCM_16')
        info, out = self.blend(original=self.d / 'orig2.wav', vocals=self.d / 'voice2.wav')
        self.assertTrue(info['ok'], info)
        y, _ = sf.read(out)
        y = y.mean(axis=1)
        self.assertLess(rms(y[int(24.5 * SR):int(25.5 * SR)]), 0.02)      # no 0.3 tone leaked into the background

    def test_prepare_uses_the_original_in_pauses_and_still_works_without_it(self):
        t = np.arange(N) / SR
        dub = np.zeros(N)
        for a, b in self.spans:
            dub[int(a * SR):int(b * SR)] = 0.2 * np.sin(2 * np.pi * 180 * t[int(a * SR):int(b * SR)])
        sf.write(self.d / 'dub.wav', stereo(dub), SR, subtype='PCM_16')
        with_orig = db_mod.prepare(self.d / 'base.wav', self.d / 'voice.wav', self.d / 'dub.wav', self.d, 'a', self.spans,
                                   preserve_music=False, strict=False, original=self.d / 'orig.wav')
        self.assertIn('original_in_pauses', with_orig['music_fill'])
        y, _ = sf.read(with_orig['path'])
        self.assertGreater(len(y), N - 10)
        without = db_mod.prepare(self.d / 'base.wav', self.d / 'voice.wav', self.d / 'dub.wav', self.d, 'b', self.spans,
                                 preserve_music=False, strict=False)
        self.assertNotIn('original_in_pauses', without['music_fill'])
        self.assertTrue(Path(without['path']).exists())


if __name__ == '__main__':
    unittest.main()
