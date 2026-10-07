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

    def test_pauses_are_found_from_the_voices_not_from_the_transcript(self):
        # a transcript segment covering the whole clip (loose timing) must not hide the real pauses between the words
        info, out = self.blend(spans=[(0.0, 30.0)])
        self.assertTrue(info['ok'], info)
        y, _ = sf.read(out)
        pause = slice(int(2 * SR), int(6 * SR))
        self.assertLess(abs(rms(y.mean(axis=1)[pause]) / rms(self.room[pause]) - 1), 0.03)

    def test_no_pauses_nothing_to_do(self):
        t = np.arange(N) / SR
        talk = 0.3 * np.sin(2 * np.pi * 220 * t)                       # somebody speaks all the time
        sf.write(self.d / 'talk_orig.wav', stereo(self.room + talk), SR, subtype='PCM_16')
        sf.write(self.d / 'talk_voice.wav', stereo(talk), SR, subtype='PCM_16')
        info, out = self.blend(original=self.d / 'talk_orig.wav', vocals=self.d / 'talk_voice.wav', spans=[(0.0, 30.0)])
        self.assertFalse(info['ok'])

    def test_a_voice_the_separator_missed_is_not_laid_in(self):
        # the separated voices are silent at 24-26 s but the original has a loud voice there: the original is much louder than the
        # separated background, so it is not a pause
        t = np.arange(N) / SR
        extra = np.zeros(N)
        extra[24 * SR:26 * SR] = 0.3 * np.sin(2 * np.pi * 330 * t[24 * SR:26 * SR])
        sf.write(self.d / 'orig3.wav', stereo(self.original + extra), SR, subtype='PCM_16')
        info, out = self.blend(original=self.d / 'orig3.wav', spans=self.spans + [(24.0, 26.0)])
        self.assertTrue(info['ok'], info)
        y, _ = sf.read(out)
        self.assertLess(rms(y.mean(axis=1)[int(24.5 * SR):int(25.5 * SR)]), 0.02)

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
        self.assertIn('share of pauses per 30 s', with_orig['music_fill']['pause_note'])       # the job log says where the original was used
        y, _ = sf.read(with_orig['path'])
        self.assertGreater(len(y), N - 10)
        without = db_mod.prepare(self.d / 'base.wav', self.d / 'voice.wav', self.d / 'dub.wav', self.d, 'b', self.spans,
                                 preserve_music=False, strict=False)
        self.assertNotIn('original_in_pauses', without['music_fill'])
        self.assertIn('no original recording', without['music_fill']['pause_note'])
        self.assertTrue(Path(without['path']).exists())


class DirtyStemTests(unittest.TestCase):
    """A restaurant: the separator files the crowd and the dishes under "voices", so its voices sound like voice all the time and
    its background is nearly empty. The pauses must then be found from the transcribed words."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        rng = np.random.default_rng(3)
        t = np.arange(N) / SR
        self.crowd = 0.01 * rng.standard_normal(N)
        self.clink = np.zeros(N)
        k = int(5.0 * SR)                                                  # a clink in the pause between the two lines
        self.clink[k:k + 2000] = 0.2 * rng.standard_normal(2000) * np.exp(-np.arange(2000) / 400.0)
        self.spans = [(8.0, 12.0), (16.0, 22.0)]
        self.voice = np.zeros(N)
        for a, b in self.spans:
            self.voice[int(a * SR):int(b * SR)] = 0.3 * np.sin(2 * np.pi * 220 * t[int(a * SR):int(b * SR)])
        self.original = self.crowd + self.clink + self.voice
        sf.write(self.d / 'orig.wav', stereo(self.original), SR, subtype='PCM_16')
        sf.write(self.d / 'base.wav', stereo(0.02 * self.crowd), SR, subtype='PCM_16')              # an almost empty background
        sf.write(self.d / 'voice.wav', stereo(self.original), SR, subtype='PCM_16')                 # everything is in the voices

    def tearDown(self):
        self.tmp.cleanup()

    def test_pauses_come_from_the_words_and_the_crowd_and_clink_return(self):
        out = self.d / 'out.wav'
        info = db_mod.original_in_pauses(self.d / 'base.wav', self.d / 'orig.wav', self.d / 'voice.wav', self.spans, out)
        self.assertTrue(info['ok'], info)
        self.assertTrue(info.get('dirty'), info)
        y, _ = sf.read(out)
        y = y.mean(axis=1)
        pause = slice(int(2 * SR), int(4.5 * SR))
        self.assertLess(abs(20 * np.log10(rms(y[pause]) / rms(self.original[pause]))), 1.0)
        clink = slice(int(5.0 * SR), int(5.0 * SR) + 2000)
        self.assertGreater(rms(y[clink]), 0.5 * rms(self.original[clink]))
        speech = slice(int(9 * SR), int(11 * SR))
        self.assertLess(rms(y[speech]), 0.1 * rms(self.original[speech]))       # the English voice does not come back

    def test_a_clean_separation_is_not_called_dirty(self):
        rng = np.random.default_rng(4)
        room = 0.02 * rng.standard_normal(N)
        t = np.arange(N) / SR
        voice = np.zeros(N)
        for a, b in self.spans:
            voice[int(a * SR):int(b * SR)] = 0.3 * np.sin(2 * np.pi * 220 * t[int(a * SR):int(b * SR)])
        sf.write(self.d / 'o2.wav', stereo(room + voice), SR, subtype='PCM_16')
        sf.write(self.d / 'b2.wav', stereo(0.5 * room), SR, subtype='PCM_16')
        sf.write(self.d / 'v2.wav', stereo(voice), SR, subtype='PCM_16')
        info = db_mod.original_in_pauses(self.d / 'b2.wav', self.d / 'o2.wav', self.d / 'v2.wav', self.spans, self.d / 'out2.wav')
        self.assertTrue(info['ok'], info)
        self.assertFalse(info.get('dirty'), info)

    def test_speech_the_transcript_missed_is_not_a_crowd(self):
        t = np.arange(N) / SR
        voice = np.zeros(N)
        voice[int(8 * SR):int(28 * SR)] = 0.3 * np.sin(2 * np.pi * 220 * t[int(8 * SR):int(28 * SR)])      # as loud outside the spans as inside
        rng = np.random.default_rng(5)
        room = 0.01 * rng.standard_normal(N)
        sf.write(self.d / 'o3.wav', stereo(room + voice), SR, subtype='PCM_16')
        sf.write(self.d / 'b3.wav', stereo(0.5 * room), SR, subtype='PCM_16')
        sf.write(self.d / 'v3.wav', stereo(voice), SR, subtype='PCM_16')
        info = db_mod.original_in_pauses(self.d / 'b3.wav', self.d / 'o3.wav', self.d / 'v3.wav', [(8.0, 9.0)], self.d / 'out3.wav')
        self.assertFalse(info.get('dirty'), info)


if __name__ == '__main__':
    unittest.main()
