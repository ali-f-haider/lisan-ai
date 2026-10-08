"""The speech model and the speaker-detection pipeline must not stay loaded after the last job has left.

Functions are extracted from the real whisper_service.py source and run against mocks: no model is loaded."""
import ast
import copy
import sys
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'whisper_service.py').read_text(encoding='utf-8-sig')


def extract(names, namespace):
    tree = ast.parse(SOURCE)
    nodes = [copy.deepcopy(n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert len(nodes) == len(names), [n.name for n in nodes]
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), 'whisper_service.py', 'exec'), namespace)
    return namespace


def release_namespace():
    ns = {'threading': threading, '_may_start_next': lambda running: True, '_model': object(), '_model_lock': threading.Lock(),
          '_release_pending': False, '_trim_memory': Mock(), 'diarization_pipelines': {}, '_diar_release_count': 0}
    extract(['_JobQueue', '_release_model', '_release_diarization_pipeline'], ns)
    ns['_transcribe_queue'] = ns['_JobQueue'](1)
    return ns


class ModelReleaseTests(unittest.TestCase):
    def test_a_single_job_frees_the_model_when_it_finishes(self):
        ns = release_namespace()
        ns['_transcribe_queue'].acquire('a')
        ns['_release_model']()
        self.assertIsNone(ns['_model'])
        ns['_transcribe_queue'].release()
        ns['_trim_memory'].assert_called()

    def test_two_overlapping_jobs_leave_nothing_loaded_after_the_last_one_leaves(self):
        ns = release_namespace()
        queue = ns['_transcribe_queue']
        queue.acquire('a'); queue.acquire('b')
        self.assertEqual(queue.running(), 2)
        ns['_release_model'](); ns['_release_model']()          # both finish decoding while the other still holds a slot
        self.assertIsNotNone(ns['_model'], 'it is still needed by the other job at this moment')
        queue.release()
        self.assertEqual(queue.running(), 1)
        self.assertIsNotNone(ns['_model'], 'one job is still running')
        queue.release()
        self.assertIsNone(ns['_model'])
        self.assertFalse(ns['_release_pending'])

    def test_the_remaining_job_frees_it_itself_when_it_finishes_last(self):
        ns = release_namespace()
        queue = ns['_transcribe_queue']
        queue.acquire('a'); queue.acquire('b')
        ns['_release_model']()                                   # a: skipped, b still running
        queue.release()                                          # a leaves
        self.assertIsNotNone(ns['_model'])
        ns['_release_model']()                                   # b finishes decoding as the only job
        self.assertIsNone(ns['_model'])
        self.assertFalse(ns['_release_pending'])

    def test_a_model_loaded_again_for_a_new_job_is_not_freed_by_an_old_pending_release(self):
        ns = release_namespace()
        queue = ns['_transcribe_queue']
        queue.acquire('a'); queue.acquire('b')
        ns['_release_model']()                                   # pending
        queue.release(); queue.release()                         # everyone gone: freed
        ns['_model'] = object()                                  # a later job loads it again
        queue.acquire('c')
        self.assertTrue(queue.running() == 1 and ns['_model'] is not None)

    def test_a_release_hook_failure_never_breaks_giving_up_the_slot(self):
        ns = release_namespace()
        queue = ns['_transcribe_queue']
        queue.acquire('a'); queue.acquire('b')
        ns['_release_model']()
        ns['_trim_memory'].side_effect = RuntimeError('boom')
        queue.release()
        queue.release()                                          # hook raises inside; slot accounting must still finish
        self.assertEqual(queue.running(), 0)

    def test_transcription_failure_after_the_language_check_still_releases(self):
        source = SOURCE.replace('\r\n', '\n')
        start = source.index('segments_gen, info = _get_model().transcribe(')
        window = source[start - 200:start + 900]
        self.assertIn('try:', window)
        self.assertIn('except BaseException:', window)
        self.assertIn('_release_model()', window[window.index('except BaseException:'):])
        self.assertIn('raise', window[window.index('except BaseException:'):])


class SpeakerPipelineTests(unittest.TestCase):
    def namespace(self, pipeline_factory):
        pipeline = Mock(return_value=NS(speaker_diarization=NS(itertracks=lambda yield_label=False: iter([]))))
        ns = release_namespace()
        ns.update(Pipeline=NS(from_pretrained=lambda *a, **k: pipeline_factory(pipeline)),
                  torch=NS(from_numpy=lambda a: Mock(unsqueeze=lambda i: Mock())),
                  normalize_audio_for_diarization=lambda path: path, _DIAR_RUN_LOCK=threading.Lock())
        extract(['get_speaker_turns'], ns)
        audio = types.ModuleType('soundfile')
        audio.read = lambda path, dtype=None: (NS(shape=(16000,), mean=lambda axis: None), 16000)
        py = types.ModuleType('pyannote'); pa = types.ModuleType('pyannote.audio'); pa.Pipeline = ns['Pipeline']; py.audio = pa
        return ns, {'soundfile': audio, 'pyannote': py, 'pyannote.audio': pa}

    def test_a_pipeline_built_after_the_job_gave_up_is_not_cached_forever(self):
        built, go = threading.Event(), threading.Event()
        def factory(pipeline):
            built.set(); go.wait(5); return pipeline
        ns, modules = self.namespace(factory)
        errors = []
        with patch.dict(sys.modules, modules):
            def work():
                try: ns['get_speaker_turns']('x.wav', 'token', None)
                except Exception as e: errors.append(e)
            thread = threading.Thread(target=work); thread.start()
            self.assertTrue(built.wait(5))
            ns['_release_diarization_pipeline']('token')         # the job timed out and cleaned up while the pipeline was loading
            go.set(); thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(ns['diarization_pipelines'], {})

    def test_an_ordinary_run_still_caches_its_pipeline_for_the_next_job_until_released(self):
        ns, modules = self.namespace(lambda pipeline: pipeline)
        with patch.dict(sys.modules, modules):
            ns['get_speaker_turns']('x.wav', 'token', None)
        self.assertIn('token', ns['diarization_pipelines'])
        ns['_release_diarization_pipeline']('token')
        self.assertEqual(ns['diarization_pipelines'], {})


if __name__ == '__main__':
    unittest.main()
