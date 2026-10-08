"""The measurement tool itself is offline and does not start app workers."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('memory_baseline_tool', ROOT / 'tools/memory_baseline.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    def test_import_plan_follows_real_eager_module_order_without_function_or_conditional_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.py').write_text('import fastapi\nimport local\nimport torch\nif False:\n import stripe\ndef later():\n import av\n', encoding='utf-8')
            (root / 'local.py').write_text('import numpy\nfrom scipy import signal\nimport fastapi\n', encoding='utf-8')
            self.assertEqual([row[0] for row in probe.startup_imports(root)], ['fastapi', 'numpy', 'scipy.signal', 'torch'])

    def test_current_plan_places_main_heavy_dependencies_in_source_order(self):
        names = [name for name, _ in probe.startup_imports()]
        self.assertLess(names.index('numpy'), names.index('torch'))
        self.assertLess(names.index('torch'), names.index('faster_whisper'))
        self.assertLess(names.index('faster_whisper'), names.index('stripe'))
        self.assertNotIn('pyannote.audio', names)
        self.assertNotIn('demucs.separate', names)

    def test_child_environment_drops_credentials_and_user_pythonpath(self):
        with patch.dict(os.environ, {'FAL_API_KEY': 'private', 'SUPABASE_SERVICE_KEY': 'private', 'PYTHONPATH': 'private', 'PATH': 'safe'}):
            env = probe.child_environment()
        self.assertNotIn('FAL_API_KEY', env)
        self.assertNotIn('SUPABASE_SERVICE_KEY', env)
        self.assertNotIn('PYTHONPATH', env)
        self.assertEqual(env['PATH'], 'safe')
        self.assertEqual(env['OMP_NUM_THREADS'], '4')

    def test_offline_guard_blocks_env_file_reads_and_outbound_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('SECRET=private', encoding='utf-8')
            with probe.offline_guard(directory):
                self.assertFalse(path.exists())
                self.assertFalse((Path(directory) / '.env.local').exists())
                self.assertTrue(Path(directory).exists())
                self.assertEqual(os.environ['DATA_DIR'], directory)
                with self.assertRaisesRegex(RuntimeError, 'Network is disabled'):
                    socket.create_connection(('example.invalid', 443))
                with socket.socket() as sock:
                    with self.assertRaisesRegex(RuntimeError, 'Network is disabled'):
                        sock.connect(('127.0.0.1', 9))
            self.assertTrue(path.exists())

    def test_offline_guard_never_starts_app_background_thread(self):
        ran = []
        thread = threading.Thread(target=lambda: ran.append(1))
        with tempfile.TemporaryDirectory() as directory, probe.offline_guard(directory):
            thread.start()
            self.assertFalse(thread.is_alive())
            self.assertEqual(ran, [])

    def test_failed_import_is_reported_without_fabricating_success(self):
        with patch.object(probe, 'rss_bytes', side_effect=[1048576, 2097152]), patch.object(probe.importlib, 'import_module', side_effect=ModuleNotFoundError('Unavailable')):
            row = probe.measure_import('not_installed', 'fixture:1')
        self.assertEqual(row['status'], 'unavailable')
        self.assertEqual(row['delta_mib'], 1)
        self.assertIn('ModuleNotFoundError', row['error'])
        json.dumps(row, allow_nan=False)

    def test_current_rss_is_positive_and_uses_current_residency(self):
        self.assertGreater(probe.rss_bytes(), 0)
        source = (ROOT / 'tools/memory_baseline.py').read_text(encoding='utf-8')
        self.assertNotIn('ru_maxrss', source)
        self.assertNotIn('return int(counters.PeakWorkingSetSize)', source)
