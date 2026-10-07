"""A voice is copied only from enough clear speech; otherwise a same-gender voice is borrowed and the fee returned."""
import ast, os, shutil, sys, tempfile, unittest, wave
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(); DATA = Path(TEMP.name)
config = ModuleType('config'); config.DATA_DIR = DATA; config.OUTPUT_DIR = DATA / 'outputs'; config.UPLOAD_DIR = DATA / 'uploads'
config.OUTPUT_DIR.mkdir(); config.UPLOAD_DIR.mkdir()
config.__getattr__ = lambda name: ''
for n in ast.parse((ROOT / 'config.py').read_text(encoding='utf-8')).body:
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name) and t.id in ('CANONICAL_EMOTIONS', 'EMOTION_SYNONYMS', 'GEMINI_MODELS'):
                try: setattr(config, t.id, ast.literal_eval(n.value))
                except ValueError: pass
sys.modules.setdefault('config', config); os.environ['RESOURCE_METER'] = '0'
import longdub_service as ld


class Stop(BaseException):
    """Stops _run_dubbing right after the voices are settled (not caught by `except Exception`)."""


def write_tone(path, seconds, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    data = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(data.tobytes())


def row(sid, spk, a, b, gender='male', text='hello there', ar='مرحبا'):
    return dict(segment_id=sid, speaker_id=spk, start=a, end=b, text=text, arabic_text=ar, gender=gender, emotion='neutral')


class DonorTests(unittest.TestCase):
    def test_same_gender_lends_voice_even_if_another_gender_is_busier(self):
        rows = [row(f'm{i}', 'a', i, i + .5) for i in range(6)] + [row(f'f{i}', 'b', 20 + i, 20.5 + i, 'female') for i in range(2)] \
            + [row('x', 'c', 40, 41, 'female')]
        self.assertEqual(ld._pick_donor('c', ['a', 'b'], rows), 'b')

    def test_no_same_gender_falls_back_to_busiest(self):
        rows = [row(f'm{i}', 'a', i, i + .5) for i in range(4)] + [row('m9', 'b', 30, 31)] + [row('x', 'c', 40, 41, 'female')]
        self.assertEqual(ld._pick_donor('c', ['a', 'b'], rows), 'a')

    def test_unknown_gender_and_ties_are_deterministic(self):
        rows = [row('1', 'a', 0, 1), row('2', 'b', 2, 3), row('3', 'c', 4, 5, gender='')]
        self.assertEqual(ld._pick_donor('c', ['a', 'b'], rows), 'a')
        self.assertEqual(ld._pick_donor('c', ['b', 'a'], rows), 'b')

    def test_gender_is_the_majority_of_the_speakers_lines(self):
        rows = [row('1', 'a', 0, 1, 'female'), row('2', 'a', 2, 3, 'male'), row('3', 'a', 4, 5, 'male')]
        self.assertEqual(ld._speaker_gender(rows, 'a'), 'male')
        self.assertIsNone(ld._speaker_gender(rows, 'zzz'))


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.job = dict(id='22222222-2222-4222-8222-222222222222', uid='u', analysis={'audio_duration': 60})
        self.wd = ld._wd(self.job); self.wd.mkdir(parents=True, exist_ok=True)
        write_tone(self.wd / 'vocals_mono.wav', 60)
        self.out = self.wd / 'o'; self.out.mkdir(exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.wd, ignore_errors=True)

    def test_heard_lines_are_enough_so_unheard_lines_are_not_used(self):
        rows = [row('h1', 's', 2, 6), row('h2', 's', 10, 14), row('u1', 's', 20, 30)]
        with patch.object(ld, 'unheard_ids', lambda j, r: {'u1'}):
            wav, secs = ld._clone_sample(self.job, 's', rows, self.out)
        self.assertGreaterEqual(secs, ld.CLONE_MIN_REFERENCE_S)
        self.assertLess(secs, 12)          # two heard lines (~4.7 s each with padding), not the 10 s unheard one

    def test_unheard_lines_top_up_a_short_reference(self):
        rows = [row('h1', 's', 2, 3), row('u1', 's', 20, 24)]
        with patch.object(ld, 'unheard_ids', lambda j, r: {'u1'}):
            wav, secs = ld._clone_sample(self.job, 's', rows, self.out)
        self.assertIsNotNone(wav)
        self.assertGreaterEqual(secs, ld.CLONE_MIN_REFERENCE_S)

    def test_a_speaker_with_little_speech_stays_short(self):
        rows = [row('h1', 's', 2, 2.5)]
        with patch.object(ld, 'unheard_ids', lambda j, r: set()):
            wav, secs = ld._clone_sample(self.job, 's', rows, self.out)
        self.assertLess(secs, ld.CLONE_MIN_REFERENCE_S)


class RunTests(unittest.TestCase):
    """_run_dubbing up to the point where the voices are settled."""
    def setUp(self):
        self.job = dict(id='33333333-3333-4333-8333-333333333333', uid='u', status='confirmed', analysis={'audio_duration': 60},
                        speaker_list=[{'id': 'a', 'name': 'Anna'}, {'id': 'b', 'name': 'Ben'}, {'id': 'c', 'name': 'Cara'}],
                        dub_plan={'speakers': ['a', 'b', 'c'], 'clone_each': 5}, paid={'dub': 100}, created=1, filename='t.mp4', name='t')
        self.wd = ld._wd(self.job); self.wd.mkdir(parents=True, exist_ok=True)
        write_tone(self.wd / 'vocals_mono.wav', 60)
        self.rows = [row('a1', 'a', 1, 9, 'female'), row('a2', 'a', 11, 19, 'female'),
                     row('b1', 'b', 21, 29), row('b2', 'b', 31, 39),
                     row('c1', 'c', 41, 42, 'female')]
        ld._write_segments(self.job, self.rows)
        ld._save(self.job)
        self.cloned, self.refunds = [], []

    def tearDown(self):
        ld._JOBS.clear(); shutil.rmtree(self.wd, ignore_errors=True)

    def run_it(self):
        def clone(name, wav):
            self.cloned.append(name.rsplit('-', 1)[-1]); return 'voice-' + name.rsplit('-', 1)[-1], ''
        def refund(job, uid, amount, slot, key):
            self.refunds.append((amount, key)); return True
        def stop(*a, **k):
            raise Stop()
        with patch.object(ld, 'INWORLD_API_KEY', 'k'), patch.object(ld, 'unheard_ids', lambda j, r: set()), \
                patch.object(ld, '_clone_with_retry', clone), patch.object(ld, '_refund', refund), \
                patch.object(ld, '_tts_with_retry', stop), patch.object(ld, '_remaining', lambda j, s: 100):
            with self.assertRaises(Stop):
                ld._run_dubbing(self.job)
        return self.job['dub']

    def test_short_speaker_borrows_same_gender_voice_and_fee_is_returned(self):
        dub = self.run_it()
        self.assertEqual(sorted(self.cloned), ['a', 'b'])                  # the 1-second speaker is never cloned
        self.assertEqual(dub['fallback']['c'], 'voice-a')                  # female voice, not the (equally busy) male one
        self.assertEqual(self.refunds, [(5, 'clone:c')])
        self.assertTrue(any('Cara' in w and 'Anna' in w for w in self.job['warnings']))

    def test_a_video_where_nobody_has_enough_speech_still_gets_voices(self):
        self.rows = [row('a1', 'a', 1, 1.8, 'female'), row('b1', 'b', 5, 6.4), row('c1', 'c', 9, 9.9, 'female')]
        ld._write_segments(self.job, self.rows)
        dub = self.run_it()
        self.assertEqual(sorted(self.cloned), ['a', 'b', 'c'])             # copied from what they have: nothing to borrow
        self.assertEqual(self.refunds, [])
        self.assertEqual(dub['fallback'], {})

    def test_restart_after_settling_does_not_clone_or_refund_again(self):
        self.run_it()
        self.cloned.clear(); self.refunds.clear()
        self.job['status'] = 'dubbing'
        with patch.object(ld, '_refund', lambda *a, **k: self.refunds.append(a) or True):
            self.run_it_again()
        self.assertEqual(self.cloned, [])
        self.assertEqual(self.refunds, [])

    def run_it_again(self):
        def stop(*a, **k):
            raise Stop()
        with patch.object(ld, 'INWORLD_API_KEY', 'k'), patch.object(ld, 'unheard_ids', lambda j, r: set()), \
                patch.object(ld, '_clone_with_retry', lambda *a: self.cloned.append('x') or ('v', '')), \
                patch.object(ld, '_tts_with_retry', stop), patch.object(ld, '_remaining', lambda j, s: 100):
            with self.assertRaises(Stop):
                ld._run_dubbing(self.job)


if __name__ == '__main__':
    unittest.main()
