"""Job 3 review proofs: these regression tests FAIL until the protected code is fixed.

All audio is synthetic and all provider requests are mocked. No production
jobs, credentials, or paid services are used. Each test asserts the intended
behavior rather than asserting that the current bug is present.
"""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arabic_waqf as waqf
import eleven_service as es
import gemini_service as gs
import longdub_service as ld
import pause_bed as pb
import voice_level as vl


def tone(seconds, rate, db=-24.0):
    t = np.arange(round(seconds * rate)) / rate
    return (np.sqrt(2.0) * 10 ** (db / 20.0) * np.sin(2 * np.pi * 200 * t)).astype(np.float32)


class BackgroundReviewTests(unittest.TestCase):
    def test_pause_pool_respects_its_seconds_limit_inside_a_long_run(self):
        # Small reproduction of a long-video pause: the limit must bound a
        # single run as well as the number of runs, BEFORE converting samples.
        n = 10 * pb.RATE
        pcm = np.full((n, 2), 100, dtype=np.int16)
        free = np.ones(n // pb.FRAME, dtype=bool)
        pieces = pb._pool(pcm, free, n, max_sec=1.0)
        self.assertTrue(pieces)
        self.assertLessEqual(sum(len(s) for s in pieces), pb.RATE,
                             'A single pause bypassed the sound-pool memory limit')

    def test_crowd_restoration_handles_a_short_final_block(self):
        # The last block is 20 frames (0.2 s), shorter than its smoother.
        n = round((pb.BLOCK_SEC + 0.2) * pb.RATE)
        original = np.full((n, 2), 200, dtype=np.int16)
        matched = np.zeros_like(original)
        speech = np.zeros(n // pb.FRAME, dtype=bool)
        speech[-20:] = True
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'bed.wav'
            try:
                info = pb._crowd_bed(matched, original, speech, ~speech, n,
                                     output, Path(folder) / 'part.wav', {'ok': False})
            except ValueError as exc:
                self.fail('A short final block must not discard background restoration: ' + str(exc))
            self.assertTrue(info['ok'], info)
            self.assertEqual(sf.info(output).frames, n)


class VoiceMeasurementReviewTests(unittest.TestCase):
    def test_audible_short_word_in_a_long_slot_is_still_measured(self):
        rate = 16000
        spoken = tone(0.5, rate)
        padded = np.concatenate([spoken, np.zeros(round(9.5 * rate), dtype=np.float32)])
        baseline = vl.active_level(spoken, rate)
        self.assertIsNotNone(baseline)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'original.wav'
            sf.write(path, padded, rate, subtype='PCM_16')
            level, peak = ld._speech_levels(path, 0.0, 10.0)
        self.assertIsNotNone(level, 'An audible 0.5-second word disappeared from the loudness measurement')
        self.assertIsNotNone(peak)
        self.assertAlmostEqual(level, baseline[0], delta=1.0)

    def test_short_dub_gain_is_unchanged_by_silence_after_the_same_voice(self):
        rate = 16000
        spoken = tone(1.0, rate)
        padded = np.concatenate([spoken, np.zeros(3 * rate, dtype=np.float32)])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sf.write(root / 'original.wav', padded, rate, subtype='PCM_16')
            sf.write(root / 'clean.wav', spoken, rate, subtype='PCM_16')
            sf.write(root / 'padded.wav', padded, rate, subtype='PCM_16')
            seg = SimpleNamespace(segment_id='line', start=0.0, speaker='Speaker 1')
            with patch.object(es, 'resolve_job_speech', return_value=root / 'original.wav'), \
                    patch.object(es, 'job_speech_spans', return_value=[(0.0, 1.0)]), \
                    patch.object(es, 'VOICE_ANCHORS', {}):
                clean = es._measure_line_loudness('review', seg, root / 'clean.wav', 4.0)
                silent = es._measure_line_loudness('review', seg, root / 'padded.wav', 4.0)
        self.assertIsNotNone(clean)
        self.assertIsNotNone(silent)
        self.assertAlmostEqual(clean['auto_gain_db'], silent['auto_gain_db'], delta=1.0,
                               msg='Silence after the same voice must not make its words 6 dB louder')


class WaqfReviewTests(unittest.TestCase):
    def test_join_keeps_an_internal_stop_before_latin_text(self):
        # Join controls the line ending, not an internal sentence boundary.
        self.assertEqual(waqf.pausal('قَالَ لَهُ. OK', mode='join'), 'قَالَ لَهْ. OK')


class RequestReviewTests(unittest.TestCase):
    def test_thinking_fallback_is_sent_after_two_transient_errors(self):
        sent = []

        def response(request, timeout=0):
            payload = json.loads(request.data)
            sent.append(payload)
            if len(sent) <= 2:
                raise urllib.error.HTTPError(request.full_url, 503, 'busy', {}, io.BytesIO(b'busy'))
            if 'thinkingConfig' in payload['generationConfig']:
                raise urllib.error.HTTPError(request.full_url, 400, 'unsupported', {},
                                             io.BytesIO(b'thinking level is not supported'))
            return io.BytesIO(b'{"ok": true}')

        with patch.object(gs, 'GEMINI_MODELS', ['review-model']), \
                patch.object(gs.urllib.request, 'urlopen', side_effect=response), \
                patch.object(gs.time, 'sleep'):
            data, error = gs.call_gemini('not-a-real-key', {
                'contents': [], 'generationConfig': {'thinkingConfig': {'thinkingLevel': 'low'}}})
        self.assertEqual(data, {'ok': True}, error)
        self.assertIsNone(error)
        self.assertEqual(len(sent), 4)
        self.assertNotIn('thinkingConfig', sent[-1]['generationConfig'])


if __name__ == '__main__':
    unittest.main()
