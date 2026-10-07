"""The dubbed voice keeps one level per speaker: every line is measured the same way as the original line it replaces, and follows the original
only in part, so the lines of one speaker do not jump up and down."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import longdub_service as ld

SR = 44100


def voice(sec, db, seed=1, pause=0.0, murmur_db=None):
    """A speech-like signal at about `db` (RMS of the speech), with an optional pause in the middle and a murmur under it."""
    rng = np.random.default_rng(seed)
    n = int(sec * SR)
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t) ** 2) + 0.3 * rng.standard_normal(n)
    x = x / np.sqrt(np.mean(x ** 2)) * 10 ** (db / 20.0)
    if pause:
        a = int((sec - pause) / 2 * SR)
        x[a:a + int(pause * SR)] = 0
    if murmur_db is not None:
        x = x + rng.standard_normal(n) * 10 ** (murmur_db / 20.0)
    return x.astype(np.float32)


class MeasureTests(unittest.TestCase):
    def test_level_is_that_of_the_speech_not_of_the_pauses(self):
        a = ld._active_level(voice(3.0, -30.0), SR)
        b = ld._active_level(voice(3.0, -30.0, pause=1.2), SR)
        self.assertAlmostEqual(a[0], -30.0, delta=1.0)
        self.assertAlmostEqual(b[0], a[0], delta=1.0)                   # the same voice with a long pause in it has the same level

    def test_a_murmur_under_the_words_does_not_set_the_level(self):
        clean = ld._active_level(voice(3.0, -30.0, pause=1.0), SR)[0]
        dirty = ld._active_level(voice(3.0, -30.0, pause=1.0, murmur_db=-48.0), SR)[0]
        self.assertAlmostEqual(dirty, clean, delta=1.0)

    def test_silence_and_a_blip_are_no_speech(self):
        self.assertIsNone(ld._active_level(np.zeros(SR, dtype=np.float32), SR))
        self.assertIsNone(ld._active_level(np.zeros(100, dtype=np.float32), SR))

    def test_file_and_slice(self):
        with tempfile.TemporaryDirectory() as d:
            x = np.concatenate([voice(2.0, -40.0, 1), voice(2.0, -25.0, 2)])
            sf.write(Path(d) / 'a.wav', x, SR, subtype='PCM_16')
            lo, pk = ld._speech_levels(Path(d) / 'a.wav', 0.0, 2.0)
            hi, _ = ld._speech_levels(Path(d) / 'a.wav', 2.0, 2.0)
            self.assertAlmostEqual(lo, -40.0, delta=1.5)
            self.assertAlmostEqual(hi, -25.0, delta=1.5)
            self.assertLess(pk, 0.0)
            self.assertEqual(ld._speech_levels(Path(d) / 'missing.wav', 0, 1), (None, None))


class GainTests(unittest.TestCase):
    def test_without_a_usual_level_the_line_follows_the_original(self):
        self.assertAlmostEqual(ld._voice_gain(-30, -36), 6.0)
        self.assertAlmostEqual(ld._voice_gain(-30, -50), ld.GAIN_MAX_DB)           # the limit
        self.assertAlmostEqual(ld._voice_gain(-50, -30), -ld.GAIN_MAX_DB)
        self.assertAlmostEqual(ld._voice_gain(-30, -36, d_peak=-4.0), 3.0)          # a boost never clips
        self.assertEqual(ld._voice_gain(None, -30), 0.0)
        self.assertEqual(ld._voice_gain(-30, None), 0.0)

    def test_with_a_usual_level_the_line_follows_only_in_part(self):
        f = ld.LEVEL_FOLLOW
        # original 5 dB louder than the speaker's usual level: the dub is f * 5 dB above it
        g = ld._voice_gain(-35.0, -40.0, anchor=-40.0)                  # target = -40 + f*5, the dub is at -40
        self.assertAlmostEqual(g, f * 5.0, places=6)
        self.assertAlmostEqual(ld._voice_gain(-45.0, -40.0, anchor=-40.0), -f * 5.0, places=6)
        # far away: never beyond the spread
        self.assertAlmostEqual(ld._voice_gain(-10.0, -40.0, anchor=-40.0), min(ld.LEVEL_SPREAD_DB, ld.GAIN_MAX_DB))

    def test_anchors_need_enough_lines(self):
        a = ld._speaker_anchors({'s1': [-40, -38, -42, -41, -39], 's2': [-30, -31, -29], 's3': [None, -100, -35, -36, -34, -35]})
        self.assertEqual(sorted(a), ['s1', 's3'])
        self.assertAlmostEqual(a['s1'], -40.0)
        self.assertAlmostEqual(a['s3'], -35.0)                          # None and silence are not counted


class LevelVoicesTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(5)
        self.rows, self.lines = [], {}
        k = 0
        for sp, usual, n in (('s1', -38.0, 30), ('s2', -33.0, 25), ('s3', -36.0, 3)):
            for _ in range(n):
                sid = f'seg_{k}'
                k += 1
                o = usual + self.rng.normal(0, 4.5)                    # the original's own ups and downs and measuring noise
                d = -34.0 + self.rng.normal(0, 3.5)                    # each line generated on its own
                self.rows.append({'segment_id': sid, 'speaker_id': sp})
                self.lines[sid] = {'original_mean_db': o, 'dub_level_db': d, 'dub_peak_db': d + 12.0, 'gain_db': round(ld._voice_gain(o, d, d + 12.0), 1)}
        self.job = {'speaker_list': [{'id': 's1', 'name': 'Speaker 1'}, {'id': 's2', 'name': 'Speaker 2'}, {'id': 's3', 'name': 'Speaker 3'}]}
        self.events = []
        self.saved = ld._ev
        ld._ev = lambda job, step, status, detail, **k: self.events.append((step, status, detail))

    def tearDown(self):
        ld._ev = self.saved

    def final(self, sp):
        by = {r['segment_id']: r['speaker_id'] for r in self.rows}
        return [m['dub_level_db'] + m['gain_db'] for sid, m in self.lines.items() if by[sid] == sp]

    def test_the_lines_of_a_speaker_end_up_closer_together(self):
        before = {sp: np.std(self.final(sp)) for sp in ('s1', 's2')}
        ld._level_voices(self.job, {'lines': self.lines}, self.rows)
        for sp in ('s1', 's2'):
            after = np.std(self.final(sp))
            self.assertLess(after, before[sp] - 0.5, (sp, before[sp], after))
            self.assertLess(after, ld.LEVEL_FOLLOW * 4.5 * 1.6)         # about LEVEL_FOLLOW of the original's spread (plus what the gain limit leaves)

    def test_each_speaker_stays_at_the_level_of_the_original(self):
        ld._level_voices(self.job, {'lines': self.lines}, self.rows)
        self.assertAlmostEqual(np.mean(self.final('s1')), -38.0, delta=1.5)
        self.assertAlmostEqual(np.mean(self.final('s2')), -33.0, delta=1.5)
        self.assertEqual(sorted(self.job['voice_anchor']), ['s1', 's2'])

    def test_a_speaker_with_few_lines_keeps_the_exact_following(self):
        old = [m['gain_db'] for sid, m in self.lines.items() if sid in ('seg_55', 'seg_56', 'seg_57')]
        ld._level_voices(self.job, {'lines': self.lines}, self.rows)
        self.assertEqual([m['gain_db'] for sid, m in self.lines.items() if sid in ('seg_55', 'seg_56', 'seg_57')], old)

    def test_it_is_logged_and_never_raises(self):
        ld._level_voices(self.job, {'lines': self.lines}, self.rows)
        self.assertEqual(self.events[0][0], 'voice_levels')
        self.assertIn('Speaker 1', self.events[0][2])
        ld._level_voices({}, {'lines': None}, self.rows)                # garbage in: nothing happens


if __name__ == '__main__':
    unittest.main()
