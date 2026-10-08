"""After a quiet spell the server hands freed memory back once, and says how much that released."""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_shortdub_upgrade import source_functions


class IdleTrimTests(unittest.TestCase):
    def setUp(self):
        self.now = 10_000.0
        self.memory = [1.9, 1.1]
        self.trim = Mock()
        self.ns = dict(IDLE_TRIM_MINUTES=5, _idle_trim_for=[None], _time=NS(time=lambda: self.now),
                       app_state=NS(last_job_activity=self.now - 6 * 60),
                       whisper_service=NS(_transcribe_queue=NS(running=lambda: 0), _trim_memory=self.trim),
                       longdub_service=NS(_RUNNING=set()),
                       resource_meter=NS(read_mem=lambda: (2.0, self.memory.pop(0))))
        source_functions('main.py', ['_idle_memory_trim'], self.ns)

    def run_trim(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = self.ns['_idle_memory_trim']()
        return result, out.getvalue()

    def test_a_quiet_server_trims_once_and_logs_before_and_after(self):
        result, log = self.run_trim()
        self.assertTrue(result)
        self.trim.assert_called_once()
        self.assertIn('[memory] idle trim: real program memory 1946 MB -> 1126 MB', log)

    def test_the_same_quiet_period_is_not_trimmed_twice_but_a_new_one_is(self):
        self.run_trim()
        self.memory[:] = [1.5, 1.2]
        self.assertFalse(self.run_trim()[0])
        self.ns['app_state'].last_job_activity = self.now - 9 * 60       # another job happened, then another quiet spell
        self.memory[:] = [1.5, 1.2]
        self.assertTrue(self.run_trim()[0])

    def test_recent_activity_or_a_running_job_prevents_a_trim(self):
        self.ns['app_state'].last_job_activity = self.now - 60
        self.assertFalse(self.run_trim()[0])
        self.ns['app_state'].last_job_activity = self.now - 6 * 60
        self.ns['whisper_service']._transcribe_queue = NS(running=lambda: 1)
        self.assertFalse(self.run_trim()[0])
        self.ns['whisper_service']._transcribe_queue = NS(running=lambda: 0)
        self.ns['longdub_service']._RUNNING.add('job')
        self.assertFalse(self.run_trim()[0])
        self.trim.assert_not_called()

    def test_missing_memory_figures_still_trim_without_failing(self):
        self.ns['resource_meter'] = NS(read_mem=lambda: (None, None))
        result, log = self.run_trim()
        self.assertTrue(result)
        self.assertIn('memory figures unavailable', log)


if __name__ == '__main__':
    unittest.main()
