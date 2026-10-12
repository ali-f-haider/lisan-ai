"""Compressed completed-project backgrounds must work like the decoded PCM reference."""
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

import dub_background
import section_background as sections
import speech_removal


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is required')
class BackgroundReferenceFormatsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.rate = sections.RATE
        n = 8 * self.rate
        t = np.arange(n) / self.rate
        x = .01 * np.random.default_rng(37).standard_normal((n, 2))
        spoken = (t >= 3) & (t < 3.8)
        x[spoken] += .1 * np.sin(2 * np.pi * 1700 * t[spoken])[:, None]
        self.wav = self.work / 'original.wav'
        self.m4a = self.work / 'saved_final_effects.m4a'
        self.decoded = self.work / 'decoded.wav'
        sf.write(self.wav, x, self.rate, subtype='PCM_16')
        self.ffmpeg('-i', self.wav, '-c:a', 'aac', '-b:a', '192k', self.m4a)
        self.ffmpeg('-i', self.m4a, '-ar', self.rate, '-ac', 2, '-c:a', 'pcm_s16le', self.decoded)
        self.clean = self.work / 'restored.wav'
        self.voice = self.work / 'voices.wav'
        data, _ = sf.read(self.decoded, dtype='float32', always_2d=True)
        sf.write(self.clean, data * .5, self.rate, subtype='PCM_16')
        sf.write(self.voice, np.zeros_like(data), self.rate, subtype='PCM_16')
        self.digest = hashlib.sha256(self.m4a.read_bytes()).digest()

    @staticmethod
    def ffmpeg(*args):
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', *map(str, args)],
                       check=True, capture_output=True, timeout=30)

    def assert_no_conversion_leftovers(self):
        self.assertEqual(list(self.work.glob('level_ref_*')), [])
        self.assertEqual(hashlib.sha256(self.m4a.read_bytes()).digest(), self.digest)

    def test_m4a_reference_matches_the_manually_decoded_reference(self):
        expected = self.work / 'expected.wav'
        actual = self.work / 'actual.wav'
        sections.level_reference(self.decoded, self.clean, expected, [(3, 3.8)])
        sections.level_reference(self.m4a, self.clean, actual, [(3, 3.8)])
        a, _ = sf.read(actual, dtype='int16')
        b, _ = sf.read(expected, dtype='int16')
        np.testing.assert_array_equal(a, b)
        self.assert_no_conversion_leftovers()

    def test_matching_wav_references_need_no_conversion(self):
        with patch('subprocess.run', side_effect=AssertionError('unnecessary WAV conversion')):
            sections.level_reference(self.decoded, self.clean, self.work / 'fast.wav', [(3, 3.8)])
        self.assert_no_conversion_leftovers()

    def test_decode_is_removed_when_the_reference_duration_is_wrong(self):
        short = self.work / 'short.wav'
        sf.write(short, np.zeros((self.rate, 2)), self.rate, subtype='PCM_16')
        with self.assertRaisesRegex(ValueError, 'references do not match'):
            sections.level_reference(self.m4a, short, self.work / 'failed.wav', [])
        self.assertFalse((self.work / 'failed.wav').exists())
        self.assert_no_conversion_leftovers()

    def test_unreadable_audio_has_a_plain_message_and_no_partial_reference(self):
        bad = self.work / 'damaged.m4a'
        bad.write_bytes(b'not an audio file')
        with self.assertRaisesRegex(ValueError, 'background audio could not be read'):
            sections.level_reference(bad, self.clean, self.work / 'failed.wav', [])
        self.assertFalse((self.work / 'failed.wav').exists())
        self.assertEqual(bad.read_bytes(), b'not an audio file')
        self.assert_no_conversion_leftovers()

    def test_full_local_removal_from_a_saved_m4a_keeps_duration_and_removes_speech(self):
        ranges = [[3, 3.8]]
        result = dub_background.prepare(self.m4a, None, self.voice, self.work, 'saved',
            speech_removal.SpeechSpans(ranges, ranges), original=self.m4a, key='', strict=False, scenes=[])
        self.assertIn('steady_bed', result['music_fill'], 'the restored-reference branch must be exercised')
        clean, rate = sf.read(result['path'], dtype='float32')
        self.assertEqual(rate, self.rate)
        self.assertEqual(len(clean), sf.info(self.decoded).frames)
        times = np.arange(len(clean)) / rate
        mask = (times >= 3.1) & (times < 3.7)
        carrier = np.sin(2 * np.pi * 1700 * times[mask])
        self.assertLess(abs(2 * float(np.mean(clean[mask, 0] * carrier))), .001)
        self.assert_no_conversion_leftovers()


if __name__ == '__main__':
    unittest.main()
