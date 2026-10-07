"""A crowd, a restaurant, a street: the engine never makes up sound for it (made-up noise with the average spectrum of a murmur sounds like
wind). Only real pieces of its own sound are laid under the speech. A steady machine-like background (engine, hum, wind) is still rebuilt."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import music_fill as mf
import pause_bed as pb

SR = mf.RATE
N = 40 * SR


def stereo(x):
    return np.stack([x, 0.97 * x], axis=1).astype(np.float32)


def crowd(n, seed=3):
    """Murmur: noise whose level follows the rhythm of speech (3-6 Hz) and drifts, with an occasional clink."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    env = 0.35 + 0.65 * np.abs(np.sin(2 * np.pi * 3.7 * t + 3 * np.sin(2 * np.pi * 0.31 * t))) ** 1.5
    env *= 0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 0.9 * t + 1.0))
    x = np.convolve(rng.standard_normal(n), np.ones(5) / 5, 'same') * env * 0.02
    for k in range(0, n, int(2.3 * SR)):
        L = int(0.08 * SR)
        if k + L < n:
            x[k:k + L] += 0.05 * np.sin(2 * np.pi * 3100 * t[:L]) * np.exp(-t[:L] * 60)
    return x


def machine(n, seed=4):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    x = 0.01 * rng.standard_normal(n)
    for k, f in enumerate((120, 240, 360, 720)):
        x += 0.004 / (1 + 0.3 * k) * np.sin(2 * np.pi * f * t + k)
    return x


class TextureTests(unittest.TestCase):
    def test_a_crowd_is_told_from_a_machine(self):
        self.assertIs(mf.texture_segs([stereo(crowd(20 * SR))])['ok'], False)
        self.assertIs(mf.texture_segs([stereo(machine(20 * SR))])['ok'], True)
        rng = np.random.default_rng(1)
        self.assertIs(mf.texture_segs([stereo(0.05 * rng.standard_normal(20 * SR))])['ok'], True)      # steady wind / white noise

    def test_too_little_sound_is_not_judged(self):
        self.assertIsNone(mf.texture_segs([stereo(crowd(int(1.5 * SR)))])['ok'])
        self.assertIsNone(mf.texture_segs([])['ok'])


class NothingIsMadeUpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        t = np.arange(N) / SR
        rng = np.random.default_rng(8)
        self.talk = [(2.0, 6.0), (9.0, 14.0), (18.0, 22.0), (28.0, 33.0)]
        self.voice = np.zeros(N)
        for a, b in self.talk:
            s = slice(int(a * SR), int(b * SR))
            self.voice[s] = 0.2 * np.sin(2 * np.pi * 180 * t[s]) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t[s]) ** 2) + 0.02 * rng.standard_normal(int(b * SR) - int(a * SR))
        self.spans = [(0.0, 40.0)]
        sf.write(self.d / 'voice.wav', stereo(self.voice), SR, subtype='PCM_16')

    def tearDown(self):
        self.tmp.cleanup()

    def files(self, bed):
        under = np.zeros(N)
        for a, b in self.talk:
            under[int(a * SR):int(b * SR)] = 1.0
        sf.write(self.d / 'orig.wav', stereo(bed + self.voice), SR, subtype='PCM_16')
        sf.write(self.d / 'sep.wav', stereo(bed * (1.0 - 0.7 * under)), SR, subtype='PCM_16')

    def test_bed_floor_makes_up_no_noise_for_a_crowd_but_still_does_for_a_machine(self):
        self.files(crowd(N))
        info = pb.bed_floor(self.d / 'sep.wav', self.d / 'orig.wav', self.d / 'voice.wav', self.d / 'sep.wav', self.spans, self.d / 'o1.wav')
        self.assertFalse(info['ok'], info)
        self.assertIn('crowd', info['reason'])
        self.assertFalse((self.d / 'o1.wav').exists())
        self.files(machine(N))
        info = pb.bed_floor(self.d / 'sep.wav', self.d / 'orig.wav', self.d / 'voice.wav', self.d / 'sep.wav', self.spans, self.d / 'o2.wav')
        self.assertTrue(info['ok'], info)

    def test_texture_fill_uses_only_real_pieces_for_a_crowd(self):
        ctx = [stereo(crowd(6 * SR, 1)), stereo(crowd(6 * SR, 2)), stereo(crowd(6 * SR, 3))]
        pcm = np.zeros((20 * SR, 2), dtype=np.int16)
        ok, msg = mf._texture_fill(pcm, 2.0, 5.0, -40.0, ctx, allow_synth=False)
        self.assertTrue(ok, msg)
        self.assertIn('from the sound beside it', msg)
        self.assertNotIn('made new', msg)
        # a hole that is longer than the real sound can cover: it stays as it is, nothing is made up
        pcm2 = np.zeros((40 * SR, 2), dtype=np.int16)
        ok, msg = mf._texture_fill(pcm2, 2.0, 30.0, -40.0, ctx, allow_synth=False)
        self.assertFalse(ok)
        self.assertEqual(int(np.abs(pcm2).max()), 0)
        # the same long hole in a machine is rebuilt (made new), as before
        ok, msg = mf._texture_fill(pcm2, 2.0, 30.0, -40.0, [stereo(machine(6 * SR, 1))], allow_synth=True)
        self.assertTrue(ok, msg)
        self.assertIn('made new', msg)

    def test_fill_leaves_the_holes_of_a_crowd_alone_and_never_asks_the_model(self):
        bed = crowd(N)
        sf.write(self.d / 'orig.wav', stereo(bed), SR, subtype='PCM_16')
        held = bed.copy()
        for a, b in self.talk:
            held[int(a * SR):int(b * SR)] = 0.0              # the separator took the sound away under every speaker: holes
        sf.write(self.d / 'muted.wav', stereo(held), SR, subtype='PCM_16')
        called = []
        info = mf.fill(self.d / 'muted.wav', self.d / 'out.wav', self.talk, 'key', runner=lambda *a, **k: called.append(a), reference=self.d / 'orig.wav',
                       reference_pad=0.4)
        self.assertEqual(called, [], info)                    # the music model is never asked for a crowd
        self.assertIs(info['texture']['ok'], False, info)
        self.assertTrue(info['gaps'], info)                   # the holes were found and looked at
        self.assertIn('real pieces only', info['reason'])
        for g in info['gaps']:
            self.assertNotIn('made new', g['note'])


if __name__ == '__main__':
    unittest.main()
