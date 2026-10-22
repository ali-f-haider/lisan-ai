"""Railway memory monitor: retries on temporary Railway errors, quick re-poll
after a failure, clear admin messages."""
import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("RAILWAY_API_TOKEN", "x")

import railway_monitor as rm  # noqa: E402

_ADMIN = open(os.path.join(os.path.dirname(__file__), "..", "admin.html"), encoding="utf-8").read()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http(code, text=b"upstream connect error"):
    return urllib.error.HTTPError("u", code, "Err", {}, io.BytesIO(text))


GOOD = (b'{"data":{"metrics":[{"measurement":"MEMORY_USAGE_GB","values":[{"ts":1,"value":0.5}]},'
        b'{"measurement":"MEMORY_LIMIT_GB","values":[{"ts":1,"value":8}]}]}}')


class RetryTests(unittest.TestCase):
    def setUp(self):
        self._p = [
            mock.patch.object(rm, "RAILWAY_API_TOKEN", "tok"),
            mock.patch.object(rm, "RAILWAY_ENVIRONMENT_ID", "env"),
            mock.patch.object(rm, "RAILWAY_SERVICE_ID", "svc"),
            mock.patch.object(rm._time, "sleep", lambda s: None),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()

    def test_recovers_after_temporary_503(self):
        seq = [_http(503), _http(503), _Resp(GOOD)]
        def fake(req, timeout=0):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        with mock.patch.object(rm.urllib.request, "urlopen", fake):
            out = rm.fetch_memory_usage()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["percent"], 6.2)

    def test_connection_drop_is_retried(self):
        seq = [ConnectionResetError("reset"), _Resp(GOOD)]
        def fake(req, timeout=0):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        with mock.patch.object(rm.urllib.request, "urlopen", fake):
            self.assertTrue(rm.fetch_memory_usage()["ok"])

    def test_gives_up_after_four_attempts(self):
        calls = []
        def fake(req, timeout=0):
            calls.append(1)
            raise _http(503)
        with mock.patch.object(rm.urllib.request, "urlopen", fake):
            out = rm.fetch_memory_usage()
        self.assertFalse(out["ok"])
        self.assertEqual(len(calls), 4)
        self.assertIn("HTTP 503", out["error"])
        self.assertIn("tried 4 times", out["error"])

    def test_auth_style_errors_are_not_retried(self):
        calls = []
        def fake(req, timeout=0):
            calls.append(1)
            raise _http(403, b"forbidden")
        with mock.patch.object(rm.urllib.request, "urlopen", fake):
            out = rm.fetch_memory_usage()
        self.assertFalse(out["ok"])
        self.assertEqual(len(calls), 1)

    def test_graphql_not_authorized_is_reported_not_retried(self):
        calls = []
        def fake(req, timeout=0):
            calls.append(1)
            return _Resp(b'{"errors":[{"message":"Not Authorized"}]}')
        with mock.patch.object(rm.urllib.request, "urlopen", fake):
            out = rm.fetch_memory_usage()
        self.assertFalse(out["ok"])
        self.assertIn("Not Authorized", out["error"])
        self.assertEqual(len(calls), 1)


class SleepTests(unittest.TestCase):
    def test_short_wait_after_failure_long_after_success(self):
        self.assertEqual(rm._next_sleep_seconds({"ok": True}), rm.MONITOR_INTERVAL_MIN * 60)
        self.assertEqual(rm._next_sleep_seconds({"ok": False, "error": "HTTP 503"}), rm.RETRY_AFTER_FAILURE_MIN * 60)
        self.assertEqual(rm._next_sleep_seconds(None), rm.MONITOR_INTERVAL_MIN * 60)

    def test_missing_token_does_not_spin(self):
        self.assertEqual(rm._next_sleep_seconds({"ok": False, "error": "RAILWAY_API_TOKEN not set"}),
                         rm.MONITOR_INTERVAL_MIN * 60)


class AdminMessageTests(unittest.TestCase):
    def test_token_hint_only_when_token_missing(self):
        self.assertIn("/not set/i.test(rmErr)", _ADMIN)
        self.assertNotIn("textContent = 'Unavailable: ' + (d.error || 'unknown error') +\r\n      '. Set RAILWAY_API_TOKEN", _ADMIN)


if __name__ == "__main__":
    unittest.main()
