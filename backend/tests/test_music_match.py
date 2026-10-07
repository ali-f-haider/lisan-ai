"""A piece of real sound is laid into a hole only if it sounds like the real sound beside that hole. The closing music of a scene, kept
in the pauses, must never be laid under the speech of the scene (it is not what was there); the hole stays as it is instead."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import music_fill as mf
from test_crowd_not_made_up import crowd, stereo

SR = mf.RATE


def music(n, seed=9):
    """A chord with its overtones and a beat: tonal, steady, nothing like a murmur."""
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    x = 0.004 * rng.standard_normal(n)
    for f in (220.0, 277.2, 330.0, 440.0, 660.0, 880.0, 1320.0, 1760.0):
        x += 0.012 * np.sin(2 * np.pi * f * t) / (1 + f / 800.0)
    return x * (0.7 + 0.3 * np.abs(np.sin(2 * np.pi * 2.0 * t)))


def shape_gap(a, b):
    return float(np.mean(np.abs(mf._band_shape(a) - mf._band_shape(b))))


class MatchTests(unittest.TestCase):
    def test_shapes_tell_a_murmur_from_music_and_a_murmur_from_itself(self):
        self.assertLess(shape_gap(stereo(crowd(5 * SR, 1)), stereo(crowd(5 * SR, 2))), mf.MATCH_MAX_DB - 2)
        self.assertGreater(shape_gap(stereo(crowd(5 * SR, 1)), stereo(music(5 * SR))), mf.MATCH_MAX_DB + 2)

    def test_music_in_the_pauses_is_not_used_for_a_hole_in_a_murmur(self):
        timed = [(0.0, stereo(crowd(12 * SR, 1))), (30.0, stereo(music(8 * SR)))]
        pieces, note = mf._matching_pieces(timed, 14.0, 17.0)
        self.assertTrue(pieces, note)
        murmur = stereo(crowd(5 * SR, 5))
        for p in pieces:
            self.assertLess(shape_gap(p, murmur), mf.MATCH_MAX_DB + 1)
        self.assertLessEqual(sum(len(p) for p in pieces), 12.1 * SR)         # nothing of the 8 s of music

    def test_a_hole_inside_the_music_gets_the_music_back(self):
        timed = [(0.0, stereo(crowd(12 * SR, 1))), (30.0, stereo(music(8 * SR)))]
        pieces, note = mf._matching_pieces(timed, 31.0, 33.0)
        self.assertTrue(pieces, note)
        for p in pieces:
            self.assertLess(shape_gap(p, stereo(music(5 * SR, 3))), mf.MATCH_MAX_DB)

    def test_nothing_near_and_nothing_beside_the_hole_means_no_piece(self):
        timed = [(0.0, stereo(crowd(6 * SR, 1)))]
        pieces, note = mf._matching_pieces(timed, 200.0, 203.0)
        self.assertEqual(pieces, [])
        self.assertTrue(note)

    def test_no_pauses_no_piece(self):
        self.assertEqual(mf._matching_pieces([], 3.0, 5.0)[0], [])


class FillTests(unittest.TestCase):
    def test_the_closing_music_does_not_end_up_under_the_speech(self):
        n = 70 * SR
        bed = np.concatenate([crowd(50 * SR, 3), music(20 * SR)])
        talk = [(5.0, 9.0), (15.0, 19.0), (26.0, 30.0), (36.0, 40.0)]
        muted = bed.copy()
        for a, b in talk:
            muted[int(a * SR):int(b * SR)] = 0.0
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            sf.write(d / 'sep.wav', stereo(muted), SR, subtype='PCM_16')
            sf.write(d / 'ref.wav', stereo(bed), SR, subtype='PCM_16')

            def no_model(*a, **k):
                raise AssertionError('the music model must not be asked for a crowd scene')
            info = mf.fill(d / 'sep.wav', d / 'out.wav', talk, 'key', runner=no_model, reference=d / 'ref.wav')
            self.assertTrue(info['filled'], info)
            out, _ = sf.read(d / 'out.wav', dtype='float32', always_2d=True)
        murmur = stereo(crowd(5 * SR, 5))
        for a, b in talk:
            piece = out[int((a + 0.5) * SR):int((b - 0.5) * SR)]
            if np.abs(piece).max() < 1e-4:
                continue                                   # a hole that stays as it is: allowed
            self.assertLess(shape_gap(piece, murmur), mf.MATCH_MAX_DB + 2, f'{a}-{b}: not the sound of the scene')


if __name__ == '__main__':
    unittest.main()
