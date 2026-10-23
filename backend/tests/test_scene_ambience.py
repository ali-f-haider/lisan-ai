"""Ambience continuity, sparse references, scene isolation and source-speech safety."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ambience_match as am
import pause_bed as pb

SR = am.RATE


def db(x):
    return float(10 * np.log10(np.mean(np.asarray(x, dtype=np.float64) ** 2) + 1e-14))


def ambience(seconds, seed=8):
    n = int(seconds * SR)
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    # A diffuse, moving background: neither a steady hum nor a melody.
    x = rng.standard_normal((n, 2)).astype(np.float32)
    envelope = 0.2 + 0.8 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.8 * t)) ** 2
    return x * (0.015 * envelope[:, None]).astype(np.float32)


class AmbienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, bed, spans, scenes=None, held=None, dirty=False):
        n = len(bed)
        t = np.arange(n) / SR
        voice = np.zeros((n, 2), np.float32)
        for a, b in spans:
            s = slice(int(a * SR), int(b * SR))
            voice[s] = (0.2 * np.sin(2 * np.pi * 227 * t[s]))[:, None]
        original = bed + voice
        if held is None:
            held = bed.copy()
            for a, b in spans:
                held[int(a * SR):int(b * SR)] *= 0.002
        for name, audio in [('orig', original), ('voice', original if dirty else voice), ('base', held), ('matched', held)]:
            sf.write(self.d / f'{name}.wav', audio, SR, subtype='PCM_16')
        result = pb.bed_floor(self.d / 'matched.wav', self.d / 'orig.wav', self.d / 'voice.wav',
                              self.d / 'base.wav', spans, self.d / 'out.wav', scenes=scenes)
        y = sf.read(self.d / 'out.wav', dtype='float32')[0] if result['ok'] else None
        return result, y, voice

    def test_first_minute_does_not_repeat_a_tiny_unmatched_reference(self):
        bed = ambience(64)
        with patch.object(am, 'Pieces', side_effect=AssertionError('tiny sample repeated')):
            result, y, _ = self.render(bed, [(0, 61), (62.2, 64)], [{'t0': 0, 't1': 64}])
        self.assertFalse(result['ok'])
        self.assertIsNone(y)
        self.assertLess(result['ambience_scenes'][0]['reference_sec'], 1.2)
        self.assertTrue(result['ambience_scenes'][0]['review'])

    def test_changing_ambience_is_not_rejected_by_the_steady_noise_gate(self):
        bed = ambience(16)
        # A stronger, slow variation used to fail the global steadiness gate.
        t = np.arange(len(bed)) / SR
        bed *= (0.07 + 0.93 * np.sin(2 * np.pi * 0.3 * t) ** 4)[:, None]
        result, y, _ = self.render(bed, [(2, 7), (9, 14)], dirty=True)
        self.assertTrue(result['ok'], result)
        self.assertIn('real ambience', result['reason'])
        self.assertGreater(db(y[3 * SR:6 * SR]), -65 + pb.BED_VOLUME_DB - 3.0)       # audible, at the deliberate 30 % level

    def test_a_future_reference_in_another_room_is_not_borrowed(self):
        bed = ambience(16)
        result, y, _ = self.render(bed, [(0, 5), (12, 16)], [{'t0': 0, 't1': 5}, {'t0': 5, 't1': 16}])
        self.assertTrue(result['ok'], result)
        before, _ = sf.read(self.d / 'matched.wav', dtype='float32')
        np.testing.assert_array_equal(y[:5 * SR], before[:5 * SR])
        self.assertGreater(db(y[13 * SR:14 * SR]), db(before[13 * SR:14 * SR]) + 25)
        self.assertEqual(result['ambience_scenes'][0]['filled_sec'], 0)

    def test_clean_pauses_are_bit_identical_and_effects_are_not_lowered(self):
        bed = ambience(12)
        spans = [(2, 9)]
        held = bed.copy()
        held[2 * SR:9 * SR] *= 0.002
        # A surviving short impact under speech stays at its recorded time and level.
        held[5 * SR:5 * SR + 1000] += 0.3
        result, y, _ = self.render(bed, spans, held=held)
        self.assertTrue(result['ok'], result)
        before, _ = sf.read(self.d / 'matched.wav', dtype='float32')
        np.testing.assert_array_equal(y[:SR], before[:SR])
        np.testing.assert_array_equal(y[10 * SR:], before[10 * SR:])
        self.assertLess(abs(db(y[5 * SR:5 * SR + 1000]) - db(before[5 * SR:5 * SR + 1000])), 0.1)

    def test_music_is_not_looped_as_ambience(self):
        n = 12 * SR
        t = np.arange(n) / SR
        melody = 0.08 * (np.sin(2 * np.pi * 2 * t) > 0.5) * np.sin(2 * np.pi * (220 + 110 * (np.floor(t * 4) % 3)) * t)
        bed = np.stack((melody, melody), axis=1).astype(np.float32)
        result, _, _ = self.render(bed, [(2, 8)], [{'t0': 0, 't1': 6}, {'t0': 6, 't1': 12}])
        self.assertFalse(result['ok'], result)
        self.assertFalse((self.d / 'out.wav').exists())

    def test_no_clean_reference_leaves_the_sound_unchanged(self):
        result, _, _ = self.render(ambience(5), [(0, 5)])
        self.assertFalse(result['ok'], result)
        self.assertFalse((self.d / 'out.wav').exists())

    def test_a_loud_enough_existing_background_is_not_boosted(self):
        bed = ambience(12)
        result, y, _ = self.render(bed, [(2, 9)], held=bed)
        # A changing bed may have quiet frames filled, but its existing louder frames stay.
        if result['ok']:
            before, _ = sf.read(self.d / 'matched.wav', dtype='float32')
            self.assertLess(db(y[3 * SR:8 * SR]) - db(before[3 * SR:8 * SR]), 2.0)

    def test_fractional_last_frame_keeps_exact_duration_and_samples(self):
        bed = ambience(8.003)
        result, y, _ = self.render(bed, [(2, 6)])
        self.assertTrue(result['ok'], result)
        before, _ = sf.read(self.d / 'matched.wav', dtype='float32')
        self.assertEqual(y.shape, before.shape)
        np.testing.assert_array_equal(y[-100:], before[-100:])

    def test_reference_level_changes_are_followed_within_the_scene(self):
        bed = np.random.default_rng(9).standard_normal((48 * SR, 2)).astype(np.float32) * 0.01
        bed[26 * SR:] *= 4
        result, y, _ = self.render(bed, [(3, 22), (29, 44)], [{'t0': 0, 't1': 48}])
        self.assertTrue(result['ok'], result)
        self.assertGreater(db(y[35 * SR:40 * SR]) - db(y[7 * SR:12 * SR]), 8)

    def test_prepare_uses_cached_scene_boundaries_without_an_extra_request(self):
        import dub_background as background
        bed = ambience(16)
        spans = [(0, 5), (12, 16)]
        self.render(bed, spans)
        sf.write(self.d / 'dub.wav', np.zeros_like(bed), SR, subtype='PCM_16')
        job = 'a' * 32
        (self.d / f'{job}_scenes.json').write_text(json.dumps({'scenes': [{'t0': 0, 't1': 5}, {'t0': 5, 't1': 16}]}))
        with patch.object(background.music_fill, 'fill', side_effect=AssertionError('paid fill called')):
            result = background.prepare(self.d / 'base.wav', self.d / 'voice.wav', self.d / 'dub.wav', self.d,
                                        f'merge_{job}', spans, preserve_music=False, strict=False, original=self.d / 'orig.wav')
        self.assertEqual(result['music_fill']['ambience_scenes'][0]['filled_sec'], 0)
        self.assertGreater(result['music_fill']['ambience_scenes'][1]['filled_sec'], 0)

    def test_local_fill_does_not_call_or_charge_an_ai_service(self):
        import dub_background as background
        bed = ambience(10)
        spans = [(0, 5)]
        self.render(bed, spans)
        sf.write(self.d / 'dub.wav', np.zeros_like(bed), SR, subtype='PCM_16')
        with patch.object(background.music_fill, 'fill', side_effect=AssertionError('paid fill called')):
            result = background.prepare(self.d / 'base.wav', self.d / 'voice.wav', self.d / 'dub.wav', self.d,
                                        'free', spans, preserve_music=False, strict=False, original=self.d / 'orig.wav',
                                        allow=lambda: self.fail('paid approval called'), on_filled=lambda *a: self.fail('charged'))
        self.assertIn('ambience_samples', result['music_fill'])
        self.assertGreater(result['music_fill']['ambience_samples']['sec_laid'], 4)
        json.dumps(result['music_fill'], allow_nan=False)


class ReferenceTests(unittest.TestCase):
    def test_short_stereo_reference_preserves_spatial_character(self):
        rng = np.random.default_rng(7)
        x = rng.standard_normal(int(0.7 * SR)).astype(np.float32) * 0.01
        sampler = am.Pieces([np.stack((x, -0.5 * x), axis=1)])
        y = sampler.take(3 * SR)
        self.assertGreater(db(y), -60)
        np.testing.assert_allclose(y[:, 1], -0.5 * y[:, 0], atol=1e-7)

    def test_stream_is_identical_when_read_in_different_block_sizes(self):
        chunks = [ambience(1, 4), ambience(1.4, 5)]
        one = am.Pieces(chunks).take(4 * SR)
        sampler = am.Pieces(chunks)
        many = np.concatenate([sampler.take(count) for count in (17, SR, 2 * SR, SR - 17)])
        np.testing.assert_array_equal(one, many)

    def test_events_and_music_are_not_repeated_donors(self):
        pcm = np.rint(ambience(6) * 32768).astype('<i2')
        pcm[2 * SR:2 * SR + 1000] = 20000
        free = np.ones(600, bool)
        power = am._power_frames(pcm, 600)
        chunks, _, _ = am._references(pcm, free, power, 0, 600)
        self.assertTrue(chunks)
        self.assertLess(max(float(np.max(np.abs(c))) for c in chunks), 0.1)

    def test_one_hour_length_does_not_create_a_whole_file_audio_buffer(self):
        # A lazy PCM source rejects large reads, proving the scan copies at most ten seconds.
        class Source:
            def __getitem__(self, selection):
                count = selection.stop - selection.start
                if count > 10 * SR:
                    raise AssertionError('whole-file audio read')
                return np.zeros((count, 2), dtype='<i2')
        levels = am._power_frames(Source(), 360000)
        self.assertEqual(len(levels), 360000)
        self.assertEqual(int(np.count_nonzero(levels)), 0)

    def test_explicit_gaps_and_invalid_numbers_do_not_join_scenes(self):
        pcm = np.zeros((10 * SR, 2), '<i2')
        spans, explicit = am.regions([{'t0': 0, 't1': 3}, {'t0': 6, 't1': 10},
                                     {'t0': float('nan'), 't1': float('inf')}], np.zeros(1000, bool), pcm)
        self.assertTrue(explicit)
        self.assertEqual(spans, [(0, 300), (300, 600), (600, 1000)])

    def test_without_scene_cache_acoustic_changes_split_the_reference_pool(self):
        rng = np.random.default_rng(8)
        white = rng.standard_normal((10 * SR, 2)).astype(np.float32) * 0.01
        from scipy.signal import butter, sosfilt
        white[5 * SR:] = sosfilt(butter(4, 350, fs=SR, output='sos'), white[5 * SR:], axis=0)
        pcm = np.rint(white * 32768).astype('<i2')
        ranges, explicit = am.regions(None, np.ones(1000, bool), pcm)
        self.assertFalse(explicit)
        self.assertGreater(len(ranges), 1)

    def test_scene_cache_is_loaded_only_for_the_current_job(self):
        job = 'a' * 32
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p / f'{"b" * 32}_scenes.json').write_text(json.dumps({'scenes': [{'t0': 0, 't1': 99}]}))
            self.assertIsNone(am.cached_scenes(p, f'merge_{job}', p / 'background.wav'))
            expected = [{'t0': 0, 't1': 5}]
            (p / f'{job}_scenes.json').write_text(json.dumps({'scenes': expected}))
            self.assertEqual(am.cached_scenes(p, f'merge_{job}', p / 'background.wav'), expected)
            (p / f'{job}_scenes.json').write_text('damaged')
            self.assertIsNone(am.cached_scenes(p, f'merge_{job}', p / 'background.wav'))


if __name__ == '__main__':
    unittest.main()
