"""Offline checks for the admin-only, read-only runtime inventory."""
import gc
import io
import json
from functools import lru_cache
from pathlib import Path
import queue
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_diagnostics as md
from test_shortdub_upgrade import source_functions, Response


class InventoryTests(unittest.TestCase):
    def inventory(self, modules=None, namespace=None, objects=()):
        with patch.object(md.gc, 'get_objects', return_value=list(objects)), patch.object(md.Path, 'read_text', side_effect=OSError):
            return md.snapshot(namespace or {}, modules if modules is not None else {})

    def test_absent_modules_are_unknown_and_never_imported(self):
        before = set(sys.modules)
        out = self.inventory()
        self.assertEqual(set(sys.modules), before)
        self.assertIsNone(out['models']['transcription_loaded'])
        self.assertIsNone(out['models']['speaker_detection_loaded'])
        self.assertIsNone(out['models']['activity_detection_loaded'])
        self.assertIsNone(out['container_entries']['long_projects'])
        self.assertIsNone(out['models']['separation_model_loaded'])
        self.assertEqual(out['models']['separation_model_location'], 'separate_process')

    def test_empty_loaded_caches_are_distinguished_from_missing_modules(self):
        out = self.inventory({'whisper_service': NS(_model=None), 'app_state': NS(diarization_pipelines={})})
        self.assertIs(out['models']['transcription_loaded'], False)
        self.assertIs(out['models']['speaker_detection_loaded'], False)
        self.assertEqual(out['container_entries']['speaker_detection_cache'], 0)

    def test_loaded_flags_never_call_model_or_vad_loader(self):
        model = Mock()
        @lru_cache()
        def vad():
            return model
        vad()
        out = self.inventory({'whisper_service': NS(_model=model),
                              'app_state': NS(diarization_pipelines={'secret-token': model}),
                              'faster_whisper.vad': NS(get_vad_model=vad)})
        self.assertIs(out['models']['transcription_loaded'], True)
        self.assertIs(out['models']['speaker_detection_loaded'], True)
        self.assertIs(out['models']['activity_detection_loaded'], True)
        self.assertEqual(vad.cache_info().hits, 0)
        self.assertEqual(vad.cache_info().misses, 1)
        model.assert_not_called()

    def test_job_counts_never_disclose_keys_values_or_session_tokens(self):
        gains = {'private-job-id': {'private-line-id': 9.5}}
        modules = {'eleven_service': NS(USER_GAINS=gains, VOICE_ANCHORS={}, ROOM_SETTINGS={}, ROOM_LAST={}),
                   'longdub_service': NS(_JOBS={'private-project': {'text': 'private-script'}})}
        namespace = {'_valid_tokens': {'secret-cookie': 'secret-access-token'}, '_job_charges': {'private-job-id': 7}}
        out = self.inventory(modules, namespace)
        self.assertEqual(out['container_entries']['voice_gain_jobs'], 1)
        self.assertEqual(out['container_entries']['long_projects'], 1)
        self.assertEqual(out['container_entries']['session_tokens'], 1)
        encoded = json.dumps(out, allow_nan=False)
        for secret in ('private-job-id', 'private-line-id', 'private-script', 'secret-cookie', 'secret-access-token'):
            self.assertNotIn(secret, encoded)
        self.assertEqual(gains, {'private-job-id': {'private-line-id': 9.5}})
        self.assertEqual(namespace['_job_charges'], {'private-job-id': 7})

    def test_progress_counts_cover_all_existing_prefixes(self):
        progress = {'a' * 32: {}, 'emotions_private': {}, 'generate_private': {}, 'lipsync_private': {}, 'x': {}}
        out = self.inventory({'app_state': NS(jobs_progress=progress)})
        self.assertEqual(out['progress_entries_by_kind'], dict(transcription=1, emotion=1, generation=1, lip_sync=1, other=1))
        self.assertEqual(out['container_entries']['progress_entries'], 5)
        self.assertEqual(len(progress), 5)

    def test_nested_caches_and_active_work_are_counts_only(self):
        events = queue.Queue()
        events.put({'private-event': 5})
        modules = {'voice_match_service': NS(_voices_cache={'voices': [1, 2, 3]}),
                   'inworld_service': NS(_library={'voices': [1, 2]}),
                   'assistant_service': NS(_today={'per': {'private-account': 1}}),
                   'whisper_service': NS(_model=None, _transcribe_queue=NS(running=lambda: 2, waiting_count=lambda: 3))}
        out = self.inventory(modules, {'_rate_buckets': {'a': {'secret-a': []}, 'b': {'secret-b': []}}, '_ld_event_q': events})
        self.assertEqual(out['container_entries']['rate_groups'], 2)
        self.assertEqual(out['container_entries']['rate_callers'], 2)
        self.assertEqual(out['container_entries']['pending_project_events'], 1)
        self.assertEqual(out['container_entries']['matching_library_voices'], 3)
        self.assertEqual(out['container_entries']['voice_library_voices'], 2)
        self.assertEqual(out['container_entries']['assistant_daily_accounts'], 1)
        self.assertEqual(out['models']['processing_jobs'], 2)
        self.assertEqual(out['models']['waiting_jobs'], 3)
        self.assertEqual(events.qsize(), 1)

    def test_top_five_counts_use_types_without_sizing_or_representing_objects(self):
        class Private:
            def __repr__(self):
                raise AssertionError('Do not inspect object values')
            def __sizeof__(self):
                raise AssertionError('Do not size the heap')
        objects = [Private(), {}, {}, [], (), set(), object()]
        with patch.object(gc, 'collect', side_effect=AssertionError('Do not collect')):
            out = self.inventory(objects=objects)
        stats = out['python_objects']
        self.assertEqual(stats['gc_tracked_count'], 7)
        self.assertEqual(len(stats['top_types_by_count']), 5)
        self.assertEqual(stats['top_types_by_count'][0], {'type': 'dict', 'count': 2})
        self.assertIs(stats['includes_native_allocations'], False)

    def test_linux_rollup_and_os_threads_are_read_without_scanning_maps(self):
        def read(path):
            if path.as_posix() == '/proc/self/smaps_rollup':
                return 'Rss: 2048 kB\nPss: 1024 kB\nPrivate_Dirty: 512 kB\nAnonymous: 256 kB\n'
            if path.as_posix() == '/proc/self/status':
                return 'Name: server\nThreads: 12\n'
            raise AssertionError(path)
        with patch.object(md.Path, 'read_text', read), patch.object(md.gc, 'get_objects', return_value=[]):
            out = md.snapshot({}, {})
        self.assertEqual(out['process_rollup_mib']['rss_mib'], 2)
        self.assertEqual(out['process_rollup_mib']['anonymous_mib'], 0.25)
        self.assertEqual(out['threads']['os_live'], 12)
        self.assertGreaterEqual(out['threads']['python_live'], 1)


class EndpointTests(unittest.TestCase):
    def endpoint(self, authorized=True):
        namespace = dict(_admin_check=lambda req: authorized, JSONResponse=Response,
                         _read_cgroup_memory=lambda: {'used_mb': 42},
                         _time=NS(time=lambda: 1000), app_state=NS(last_job_activity=400),
                         whisper_service=NS(_model_cache_dirs=lambda: []),
                         os=NS(listdir=lambda path: []), MODEL_CACHE_IDLE_MINUTES=20)
        return source_functions('main.py', ['admin_mem_diag'], namespace)['admin_mem_diag']

    def test_unauthorized_request_returns_before_inventory_or_proc_reads(self):
        with patch.object(md, 'snapshot') as inventory, patch('builtins.open', side_effect=AssertionError('Unauthorized read')):
            out = self.endpoint(False)(None)
        self.assertEqual(out.status_code, 401)
        inventory.assert_not_called()

    def test_authorized_request_keeps_existing_fields_and_adds_inventory(self):
        inventory = {'models': {'transcription_loaded': False}}
        with patch.object(md, 'snapshot', return_value=inventory), patch('builtins.open', return_value=io.StringIO('')):
            out = self.endpoint()(None)
        self.assertEqual(out['cgroup'], {'used_mb': 42})
        self.assertEqual(out['idle_minutes'], 10)
        self.assertEqual(out['model_cache_idle_threshold_minutes'], 20)
        self.assertEqual(out['runtime'], inventory)
        self.assertIn('model_cache_dirs', out)
        self.assertIn('processes', out)
        json.dumps(out, allow_nan=False)

    def test_inventory_failure_is_neutral_and_preserves_basic_diagnostic(self):
        with patch.object(md, 'snapshot', side_effect=RuntimeError('private-token')), patch('builtins.open', return_value=io.StringIO('')):
            out = self.endpoint()(None)
        self.assertEqual(out['cgroup']['used_mb'], 42)
        self.assertEqual(out['runtime'], {'error': 'The runtime inventory is temporarily unavailable.'})
        self.assertNotIn('private-token', json.dumps(out))
