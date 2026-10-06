"""Holes in a steady engine / wind / room tone are rebuilt locally from the whole track (its pauses), for free, never by the
music model (the Airplane clip: three holes went to the model and each sounded different)."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import music_fill as mf

SR = mf.RATE


def engine(seconds, seed=3):
    rng = np.random.default_rng(seed)
    t = np.arange(int(SR * seconds)) / SR
    x = 0.004 * rng.standard_normal(len(t))
    for k, f in enumerate((105, 148, 232, 311, 463)):
        x += 0.006 / (1 + k * 0.4) * np.sin(2 * np.pi * f * t + k)
    return x


def stereo(x):
    return np.stack([x, 0.95 * x], axis=1).astype(np.float32)


def silence(x, spans, pad=0.1):
    y = x.copy()
    for a, b in spans:
        y[int((a - pad) * SR):int((b + pad) * SR)] = 0
    return y


def db(x):
    return 10 * np.log10(np.mean(np.asarray(x, dtype=np.float64) ** 2) + 1e-14)


class EngineFillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.real = engine(30)
        # a loud event (a plane passing) at 3-5 s: it must neither unsettle the judgement nor set the level
        self.real[3 * SR:5 * SR] += 0.08 * np.random.default_rng(9).standard_normal(2 * SR)
        # people speak most of the time; the pauses between them are short
        self.spans = [(6.0, 12.0), (12.6, 18.0), (18.7, 19.0), (19.6, 27.0)]
        self.muted_pcm = silence(self.real, self.spans)
        sf.write(self.dir / 'real.wav', stereo(self.real), SR, subtype='PCM_16')
        sf.write(self.dir / 'muted.wav', stereo(self.muted_pcm), SR, subtype='PCM_16')
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, *a, **k):
        self.calls.append(a)
        raise RuntimeError('the model must not be asked')

    def fill(self, **kw):
        kw.setdefault('reference', self.dir / 'real.wav')
        return mf.fill(self.dir / 'muted.wav', self.dir / 'out.wav', self.spans, 'key', runner=self.runner, **kw)

    def test_every_hole_is_rebuilt_locally_without_calling_the_model(self):
        info = self.fill()
        self.assertTrue(info['filled'], info['reason'])
        self.assertEqual(self.calls, [])
        self.assertTrue(all(g['ok'] and g['local'] for g in info['gaps']), info['gaps'])
        out, _ = sf.read(self.dir / 'out.wav')
        out = out.mean(axis=1)
        for a, b in self.spans:
            if b - a < 2:
                continue
            self.assertLess(abs(db(out[int((a + 0.5) * SR):int((b - 0.5) * SR)]) - (db(self.real[int(20 * SR):int(21 * SR)]) + mf.LOCAL_FILL_DB)), 3.0)      # laid LOCAL_FILL_DB under the real engine

    def test_a_blip_in_the_pool_does_not_become_a_steady_tone(self):
        # a short beep in the clean sound (heard in the TestVideo5 clip as a never-ending whistle) must not be repeated for the whole hole
        pool = []
        for k in range(3):
            seg = engine(3, seed=20 + k)
            if k == 1:
                t = np.arange(int(0.25 * SR)) / SR
                seg[int(1.0 * SR):int(1.0 * SR) + len(t)] += 0.05 * np.sin(2 * np.pi * 3300 * t)
            pool.append(stereo(seg))
        out = mf._synth_sound(pool, int(6 * SR))
        x = out.mean(axis=1)
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        f = np.fft.rfftfreq(len(x), 1 / SR)
        band = spec[(f > 3100) & (f < 3500)]
        self.assertLess(10 * np.log10(band.max() / np.median(band)), 14.0)

    def test_a_hole_longer_than_the_model_takes_is_still_filled(self):
        self.spans = [(6.0, 29.0)]          # 23 s of speech: too long for the model (MAX_GAP_SEC), fine for the local rebuild
        sf.write(self.dir / 'muted.wav', stereo(silence(self.real, self.spans)), SR, subtype='PCM_16')
        info = self.fill()
        self.assertTrue(info['filled'], info['reason'])
        self.assertTrue(all(g['ok'] and g['local'] for g in info['gaps']), info['gaps'])
        self.assertEqual(self.calls, [])

    def test_steady_engine_check_and_billable_count(self):
        import dub_background
        self.assertTrue(mf.steady_engine(self.dir / 'muted.wav', self.dir / 'real.wav', self.spans)['ok'])
        self.assertEqual(dub_background.count_repairs(self.dir / 'muted.wav', self.spans, self.dir / 'c.pcm', original=self.dir / 'real.wav'), 0)

    def test_a_long_piece_is_made_in_chunks_without_repeating(self):
        pool = [stereo(engine(3, seed=40 + k)) for k in range(3)]
        old = mf.SYNTH_CHUNK_SEC
        mf.SYNTH_CHUNK_SEC = 5.0
        try:
            out = mf._synth_long(pool, int(14 * SR))
        finally:
            mf.SYNTH_CHUNK_SEC = old
        self.assertEqual(out.shape, (int(14 * SR), 2))
        a, b = out[int(1 * SR):int(3 * SR), 0], out[int(8 * SR):int(10 * SR), 0]
        self.assertLess(abs(np.corrcoef(a, b)[0, 1]), 0.5)      # the engine's own steady tones correlate a little, noise does not
        self.assertTrue(np.isfinite(out).all())

    def test_keeping_the_real_background_under_speech_is_an_opt_in(self):
        import dub_background
        from unittest.mock import patch
        self.assertFalse(dub_background.KEEP_UNDER_SPEECH)           # off unless DUB_BG_KEEP is set
        seen = {}

        def fake(bg, vocals, dest, **kw):
            seen.update(kw)
            return {'muted': True, 'reason': ''}
        with patch.object(dub_background.bg_duck, 'mute_speech', fake):
            dub_background.mute('a', 'b', 'c', [(0, 1)])
            self.assertFalse(seen['keep'])
            with patch.object(dub_background, 'KEEP_UNDER_SPEECH', True):
                dub_background.mute('a', 'b', 'c', [(0, 1)])
            self.assertTrue(seen['keep'])

    def test_nothing_is_charged(self):
        charged = []
        info = self.fill(on_filled=lambda a, b: charged.append((a, b)))
        self.assertTrue(info['filled'])
        self.assertEqual(charged, [])

    def test_no_dip_at_the_seams(self):
        self.fill()
        out, _ = sf.read(self.dir / 'out.wav')
        out = out.mean(axis=1)
        w = int(0.05 * SR)
        lv = np.array([db(out[i:i + w]) for i in range(int(6.5 * SR), int(26.5 * SR) - w, w)])
        self.assertGreater(lv.min(), np.median(lv) - 9.0)

    def test_short_hole_is_closed_locally_and_is_optional(self):
        self.spans = [(6.0, 11.0), (14.0, 14.7), (17.0, 23.0)]      # the middle hole is 0.7 s: too short for the model
        self.muted_pcm = silence(self.real, self.spans)
        sf.write(self.dir / 'muted.wav', stereo(self.muted_pcm), SR, subtype='PCM_16')
        info = self.fill()
        short = [g for g in info['gaps'] if g.get('optional')]
        self.assertTrue(short, info['gaps'])
        self.assertTrue(all(g['ok'] and g['local'] for g in short))
        self.assertEqual(self.calls, [])

    def test_without_a_reference_the_old_behaviour_still_works(self):
        info = self.fill(reference=None)
        self.assertIn('filled', info)      # never raises; what is left of the track is used

    def test_a_hole_that_is_not_steady_music_still_goes_to_the_model(self):
        t = np.arange(int(SR * 30)) / SR
        beat = (np.sin(2 * np.pi * 2 * t) > 0.6).astype(float)
        music = 0.2 * beat * np.sin(2 * np.pi * (200 + 300 * (np.floor(t * 4) % 3)) * t) + 0.004 * np.random.default_rng(2).standard_normal(len(t))
        sf.write(self.dir / 'real.wav', stereo(music), SR, subtype='PCM_16')
        sf.write(self.dir / 'muted.wav', stereo(silence(music, self.spans)), SR, subtype='PCM_16')
        self.fill(prompt='pop music, drums, bass, no vocals')
        self.assertTrue(self.calls)          # asked (and failed here on purpose)

    def test_ambience_the_model_would_invent_is_left_alone(self):
        t = np.arange(int(SR * 30)) / SR
        rng = np.random.default_rng(5)
        gusts = 0.01 * rng.standard_normal(len(t)) * (1 + 0.9 * np.sin(2 * np.pi * 0.7 * t) ** 2 * np.sin(2 * np.pi * 0.23 * t))
        sf.write(self.dir / 'real.wav', stereo(gusts), SR, subtype='PCM_16')
        sf.write(self.dir / 'muted.wav', stereo(silence(gusts, self.spans)), SR, subtype='PCM_16')
        info = self.fill(prompt='wind, gusts, continuous background sound only, no music, no melody')
        self.assertEqual(self.calls, [], 'a non-music sound is never sent to the music model')
        self.assertFalse(any(g['ok'] and not g['local'] for g in info['gaps']))


if __name__ == '__main__':
    unittest.main()
