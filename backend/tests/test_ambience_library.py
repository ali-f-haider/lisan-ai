"""Library integrity, conservative matching, seamless bounded long exports."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

import ambience_library as lib
import ambience_match as am
import pause_bed as pb
import section_background as sections


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        x = np.random.default_rng(7).standard_normal((9 * lib.RATE, 2)).astype(np.float32) * 0.01
        sf.write(self.root / 'bed.flac', x, lib.RATE, subtype='PCM_16')
        self.asset = {'id': 'room', 'file': 'bed.flac', 'license': 'CC0-1.0', 'environments': ['medium_room'],
            'auto_approved': True, 'tags': ['quiet'], 'sha256': hashlib.sha256((self.root / 'bed.flac').read_bytes()).hexdigest(),
            **lib.inspect(self.root / 'bed.flac')}
        (self.root / 'catalogue.json').write_text(json.dumps({'schema': 1, 'assets': [self.asset]}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_installed_assets_have_verified_licence_hash_format_and_finite_features(self):
        assets = lib.catalogue()
        self.assertGreaterEqual(len(assets), 8)
        for asset in assets:
            with self.subTest(asset=asset['id']):
                info = lib.inspect(lib.asset_path(asset))
                self.assertLessEqual(info['duration_sec'], 600)
                self.assertEqual(info['duration_sec'], asset['duration_sec'])
                json.dumps(info, allow_nan=False)
                self.assertTrue(asset['source_page'].startswith('https://'))
        for name in ('indoor_hallway', 'large_studio'):
            approved = next(a for a in assets if a['id'] == name)
            self.assertTrue(approved['auto_approved'])
            self.assertEqual(approved['listening_approval']['by'], 'Ali')

    def test_unknown_location_silence_bad_match_and_unapproved_asset_are_not_chosen(self):
        ref = self.asset['characteristics']
        scene = {'key': 'medium_room', 'conf': 'high'}
        self.assertEqual(lib.select(ref, scene, self.root)['id'], 'room')
        for setting in (None, {'key': 'medium_room', 'conf': 'low'}, {'key': 'unknown', 'conf': 'high'}):
            self.assertIsNone(lib.select(ref, setting, self.root))
        self.assertIsNone(lib.select({**ref, 'rms_db': -90}, scene, self.root))
        self.assertIsNone(lib.select({**ref, 'bands': [1, 0, 0, 0, 0]}, scene, self.root))
        self.asset['auto_approved'] = False
        (self.root / 'catalogue.json').write_text(json.dumps({'schema': 1, 'assets': [self.asset]}))
        self.assertIsNone(lib.select(ref, scene, self.root))

    def test_path_escape_and_corrupt_audio_are_rejected(self):
        with self.assertRaises(ValueError):
            lib.asset_path({**self.asset, 'file': '../secret.flac'}, self.root)
        with self.assertRaises(ValueError):
            lib.asset_path({**self.asset, 'sha256': '0' * 64}, self.root)

    def test_loop_is_identical_across_read_sizes_and_crosses_multiple_seams(self):
        a = lib.LoopReader(self.root / 'bed.flac', seed='job')
        b = lib.LoopReader(self.root / 'bed.flac', seed='job')
        try:
            for _ in range(4):
                whole = a.take(lib.BLOCK)
                pieces = np.concatenate([b.take(17), b.take(lib.RATE), b.take(lib.BLOCK - lib.RATE - 17)])
                np.testing.assert_array_equal(whole, pieces)
        finally:
            a.close(); b.close()

    def test_correlated_overlap_does_not_have_a_three_db_swell(self):
        # Identical noise at the tail/head is the worst ordinary correlated seam.
        x = np.random.default_rng(11).normal(0, 0.01, (9 * lib.RATE, 2)).astype(np.float32)
        x[-lib.RATE:] = x[:lib.RATE]
        sf.write(self.root / 'same.flac', x, lib.RATE, subtype='PCM_16')
        reader = lib.LoopReader(self.root / 'same.flac', fade_sec=1)
        try:
            relative = 10 * np.log10(np.mean(reader.seam ** 2) / np.mean(x[:lib.RATE] ** 2))
            self.assertLess(abs(relative), 0.25)
        finally:
            reader.close()

    def test_one_hour_reader_retains_only_the_seam_and_one_block(self):
        reader = lib.LoopReader(self.root / 'bed.flac')
        try:
            self.assertLessEqual(reader.seam.nbytes, 2 * lib.RATE * 2 * 4)
            for _ in range(360):
                block = reader.take(lib.BLOCK)
                self.assertEqual(block.shape, (lib.BLOCK, 2))
            self.assertLessEqual(reader.seam.nbytes, 2 * lib.RATE * 2 * 4)
            with self.assertRaises(ValueError):
                reader.take(lib.BLOCK + 1)
        finally:
            reader.close()

    def test_tiny_original_reference_uses_library_and_never_short_grains(self):
        n = 12 * lib.RATE
        rng = np.random.default_rng(6)
        original = np.rint(rng.normal(0, 300, (n, 2))).astype('<i2')
        free = np.zeros(1200, bool); free[1000:1050] = True
        held = original.copy(); held[~np.repeat(free, am.FRAME)] = 0
        with patch.object(lib, 'select', return_value=self.asset), patch.object(lib, 'asset_path', return_value=self.root / 'bed.flac'), patch.object(am, 'Pieces', side_effect=AssertionError('tiny grain looped')):
            result = am.restore(held, original, ~free, free, n, self.root / 'out.wav', self.root / 'part.wav',
                                scenes=[{'t0': 0, 't1': 12, 'key': 'medium_room', 'conf': 'high'}])
        self.assertTrue(result['ok'])
        self.assertTrue(result['ambience_scenes'][0]['replacement'])
        y = sf.read(self.root / 'out.wav', dtype='int16')[0]
        self.assertEqual(len(y), n)
        np.testing.assert_array_equal(y[1000 * am.FRAME:1050 * am.FRAME], held[1000 * am.FRAME:1050 * am.FRAME])

    def test_music_onset_splits_even_one_visual_scene(self):
        x = np.random.default_rng(13).normal(0, 200, (12 * lib.RATE, 2)).astype('<i2')
        t = np.arange(6 * lib.RATE) / lib.RATE
        x[6 * lib.RATE:] = (np.sin(2 * np.pi * 440 * t) * 5000)[:, None]
        ranges, explicit = am.regions([{'t0': 0, 't1': 12}], np.ones(1200, bool), x)
        self.assertTrue(explicit)
        self.assertGreater(len(ranges), 1)
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], 1200)

    def test_unused_library_does_not_subtract_original_donor_seconds(self):
        n = 24 * lib.RATE
        original = np.random.default_rng(91).normal(0, 300, (n, 2)).astype('<i2')
        free = np.zeros(2400, bool); free[1000:1050] = True; free[2200:2250] = True
        held = original.copy()
        held[12 * lib.RATE:] = 0
        held[2200 * am.FRAME:2250 * am.FRAME] = original[2200 * am.FRAME:2250 * am.FRAME]
        with patch.object(lib, 'select', return_value=self.asset), patch.object(lib, 'asset_path', return_value=self.root / 'bed.flac'):
            result = am.restore(held, original, ~free, free, n, self.root / 'out.wav', self.root / 'part.wav',
                                scenes=[{'t0': 0, 't1': 12}, {'t0': 12, 't1': 24}])
        self.assertTrue(result['ok'])
        self.assertEqual(result['crowd_bed']['sec_real'], 0)
        self.assertEqual(result['ambience_scenes'][0]['filled_sec'], 0)

    def test_bad_or_unlabelled_boundaries_cover_each_sample_once(self):
        ranges = sections.bounds([{'start': 2, 'end': 4}, {'start': float('nan'), 'end': 6}], 10 * lib.RATE)
        self.assertEqual(ranges, [(0, 2 * lib.RATE), (2 * lib.RATE, 4 * lib.RATE), (4 * lib.RATE, 10 * lib.RATE)])

    def test_section_repairs_use_separate_references_and_global_billing_times(self):
        source = self.root / 'source.wav'
        sf.write(source, np.zeros((10 * lib.RATE, 2)), lib.RATE, subtype='PCM_16')
        calls, charges = [], []
        def fill(src, target, spans, key, **kwargs):
            calls.append((sf.info(src).duration, sf.info(kwargs['reference']).duration, spans))
            self.assertTrue(kwargs['allow']())
            kwargs['on_filled'](1, 2)
            sf.write(target, np.full((5 * lib.RATE, 2), 0.01), lib.RATE, subtype='PCM_16')
            return {'filled': True, 'found': 1, 'gaps': [{'start': 1, 'end': 2, 'ok': True}], 'sent_sec': 1, 'filled_sec': 1}
        with patch.object(sections.music_fill, 'fill', side_effect=fill):
            result = sections.fill(source, self.root / 'fixed.wav', [(1, 2), (6, 7)], 'key',
                sections=[{'start': 0, 'end': 5}, {'start': 5, 'end': 10}], reference=source,
                allow=lambda: True, on_filled=lambda a, b: charges.append((a, b)))
        self.assertEqual(charges, [(1, 2), (6, 7)])
        self.assertEqual([c[0:2] for c in calls], [(5.0, 5.0), (5.0, 5.0)])
        self.assertEqual(sf.info(self.root / 'fixed.wav').frames, 10 * lib.RATE)
        self.assertEqual([g['start'] for g in result['gaps']], [1, 6])

    def test_free_library_match_precedes_quote_and_never_invokes_or_charges_paid_repair(self):
        import dub_background as background
        n = 12 * lib.RATE
        rng = np.random.default_rng(14)
        bed = rng.normal(0, 0.01, (n, 2)).astype(np.float32)
        voice = np.zeros_like(bed)
        t = np.arange(n) / lib.RATE
        voice[:int(9.8 * lib.RATE)] = (0.2 * np.sin(2 * np.pi * 227 * t[:int(9.8 * lib.RATE)]))[:, None]
        voice[int(11.2 * lib.RATE):] = (0.2 * np.sin(2 * np.pi * 227 * t[int(11.2 * lib.RATE):]))[:, None]
        separated = bed.copy()
        separated[np.any(voice != 0, axis=1)] = 0  # separator lost the room bed beneath speech
        for name, x in [('bg', separated), ('original', bed + voice), ('voice', voice), ('dub', voice)]:
            sf.write(self.root / (name + '.wav'), x, lib.RATE, subtype='PCM_16')
        scene = [{'t0': 0, 't1': 12, 'key': 'medium_room', 'conf': 'high'}]
        spans = [(0, 9.8), (11.2, 12)]
        # Match the separated source to the clean reference and preserve the same scene cache in both paths.
        with patch.object(lib, 'select', return_value=self.asset), patch.object(lib, 'asset_path', return_value=self.root / 'bed.flac'), \
             patch.object(background.music_fill, '_fal_run', side_effect=AssertionError('paid repair called')):
            clean = background.mute(self.root / 'bg.wav', self.root / 'voice.wav', self.root / 'mute.wav', spans)
            quoted = background.count_repairs(clean, spans, self.root / 'price.pcm', original=self.root / 'bg.wav',
                recording=self.root / 'original.wav', vocals=self.root / 'voice.wav', scenes=scene)
            self.assertEqual(quoted, 0)
            with patch.object(background.pause_bed, 'bed_floor', wraps=background.pause_bed.bed_floor) as restore:
                result = background.prepare(self.root / 'bg.wav', self.root / 'voice.wav', self.root / 'dub.wav',
                    self.root, 'render', spans, key='not-used', original=self.root / 'original.wav', scenes=scene,
                    allow=lambda: self.fail('paid approval requested'), on_filled=lambda *args: self.fail('charged'))
                self.assertEqual(restore.call_count, 1, 'the same library bed must not be mixed in twice')
        self.assertTrue(any(s.get('replacement') for s in result['music_fill']['ambience_scenes']))
        self.assertEqual(result['music_fill'].get('gaps'), [])
        audio = sf.read(result['path'], dtype='float32')[0]
        level = 10 * np.log10(np.mean(audio[2 * lib.RATE:8 * lib.RATE] ** 2) + 1e-14)
        self.assertLess(level, -39.0, 'replacement ambience must not exceed the original measured bed')
        self.assertGreater(level, -47.5 + pb.BED_VOLUME_DB - 3.0, 'level matching must not erase the restored ambience')

    def test_quote_finds_the_same_short_job_scene_cache_as_generation(self):
        job = 'b' * 32
        scene = [{'t0': 0, 't1': 10, 'key': 'medium_room', 'conf': 'high'}]
        (self.root / (job + '_scenes.json')).write_text(json.dumps({'scenes': scene}))
        self.assertEqual(am.cached_scenes(self.root, job + '_price', self.root / 'background.wav'), scene)
        self.assertEqual(am.cached_scenes(self.root, 'merge_' + job, self.root / 'background.wav'), scene)

    def test_pcm_section_seek_preserves_exact_samples_far_from_start(self):
        from unittest.mock import patch
        source = self.root / 'pcm.wav'
        x = np.random.default_rng(10).integers(-1000, 1000, (20 * lib.RATE, 2), dtype=np.int16)
        sf.write(source, x, lib.RATE, subtype='PCM_16')
        import ffmpeg_utils
        with patch.object(ffmpeg_utils, 'run_ffmpeg', side_effect=AssertionError('unnecessary whole-file decode')):
            sections._clip(source, self.root / 'clip.wav', 17 * lib.RATE + 13, 19 * lib.RATE + 29)
        y = sf.read(self.root / 'clip.wav', dtype='int16')[0]
        np.testing.assert_array_equal(y, x[17 * lib.RATE + 13:19 * lib.RATE + 29])


if __name__ == '__main__':
    unittest.main()
