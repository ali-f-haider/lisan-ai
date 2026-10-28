"""Sleeping when idle (keep_awake.py) and measuring what the server sends out (net_meter.py)."""
import datetime
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import keep_awake as K
import net_meter as N


class Quiet(unittest.TestCase):
    def setUp(self):
        self.saved = (K._last_request, K._holds, list(K._extra_busy))
        K._extra_busy.clear()
        K._holds = 0

    def tearDown(self):
        K._last_request, K._holds = self.saved[0], self.saved[1]
        K._extra_busy[:] = self.saved[2]

    def test_quiet_only_after_the_quiet_minutes_without_requests_or_jobs(self):
        K.touch()
        now = K._last_request
        self.assertFalse(K.quiet(now + 60))
        self.assertTrue(K.quiet(now + K.QUIET_MIN * 60 + 1))
        with K.hold():
            self.assertFalse(K.quiet(now + K.QUIET_MIN * 60 + 1))      # a running job keeps it awake
        K.add_busy_check(lambda: True)                                   # e.g. a job waiting for its turn
        self.assertFalse(K.quiet(K._last_request + K.QUIET_MIN * 60 + 1))

    def test_a_failing_busy_check_does_not_break_anything(self):
        K.add_busy_check(lambda: 1 / 0)
        self.assertFalse(K.busy())

    def test_sleep_waits_offline_while_quiet(self):
        answers = [True, True, False]
        slept = []
        with mock.patch.object(K, "quiet", lambda: answers.pop(0)), mock.patch.object(K.time, "sleep", slept.append):
            K.sleep(60)
        self.assertEqual(slept, [60.0, K.CHECK_SEC, K.CHECK_SEC])

    def test_the_heartbeat_is_one_small_dns_question(self):
        sent = []

        class S:
            def __init__(self, *a): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def settimeout(self, t): pass
            def sendto(self, data, to): sent.append((data, to))
        with mock.patch.object(K.socket, "socket", S):
            self.assertTrue(K.ping())
        self.assertEqual(sent[0][1], ("1.1.1.1", 53))
        self.assertLess(len(sent[0][0]), 100)
        self.assertIn(b"\x07railway\x03app\x00", sent[0][0])
        self.assertLess(K.HEARTBEAT_SEC, 300)                  # Railway sleeps after about 5 minutes without packets


class Wiring(unittest.TestCase):
    def test_every_job_that_is_measured_also_keeps_the_server_awake(self):
        import resource_meter
        seen = []
        f = resource_meter.metered("x", lambda *a, **k: "j")(lambda: seen.append(K.busy()) or 7)
        self.assertEqual(f(), 7)
        self.assertEqual(seen, [True])

    def test_the_loops_that_go_online_wait_while_the_site_is_quiet(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        for fn in ("def _credit_recovery", "def _longdub_refund_recovery", "def _concurrency_refresher", "def _cleanup_worker"):
            body = main[main.index(fn):][:2500]
            self.assertIn("keep_awake.sleep(", body, fn)
        for f in ("railway_monitor.py", "service_usage_monitor.py", "longdub_service.py"):
            self.assertIn("keep_awake.sleep(", (ROOT / f).read_text(encoding="utf-8"), f)
        self.assertIn("keep_awake.touch()", main)
        self.assertIn("net_meter.install()", main)


class NetMeter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (N.FILE, dict(N._counts), N._proc_last[0])
        N.FILE = Path(self.tmp.name) / "net_meter.json"
        N._counts.clear()
        N._proc_last[0] = None

    def tearDown(self):
        N.FILE = self.saved[0]
        N._counts.clear(); N._counts.update(self.saved[1])
        N._proc_last[0] = self.saved[2]
        self.tmp.cleanup()

    def test_places_get_plain_names(self):
        self.assertEqual(N.label_for_host("generativelanguage.googleapis.com"), "Gemini (Google AI)")
        self.assertEqual(N.label_for_host("abc123.r2.cloudflarestorage.com"), "R2 storage (backups, temporary files)")
        self.assertEqual(N.label_for_host("xyz.supabase.co"), "Supabase (database)")
        self.assertEqual(N.label_for_host("example.org"), "example.org")

    def test_sent_bytes_are_counted_by_place(self):
        N.install()
        a, b = socket.socketpair()
        try:
            with mock.patch.object(N, "_plain_label", lambda s: "test place"):
                a.sendall(b"x" * 5000)
                a.send(b"y" * 10)
        finally:
            a.close(); b.close()
        self.assertEqual(N._counts.get("test place"), 5010)

    def test_the_month_adds_up_by_place_with_the_containers_total(self):
        dev = Path(self.tmp.name) / "dev"
        def write(tx):
            dev.write_text("Inter-|   Receive\n face |bytes\n    lo: 0 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0\n"
                           f"  eth0: 0 0 0 0 0 0 0 0 {tx} 0 0 0 0 0 0 0\n")
        real = N.proc_tx_bytes
        write(1_000_000_000)
        with mock.patch.object(N, "proc_tx_bytes", lambda: real(str(dev))):
            N.flush(datetime.datetime(2026, 10, 28, 12))          # first reading: nothing known yet
            N._add("Gemini (Google AI)", 3_000_000_000)
            N._add(N.VISITORS, 1_000_000_000)
            write(6_000_000_000)
            N.flush(datetime.datetime(2026, 10, 28, 13))
            s = N.summary(datetime.date(2026, 10, 8))
        self.assertEqual(s["total_gb"], 5.0)
        self.assertEqual([p["place"] for p in s["by_place"]], ["Gemini (Google AI)", N.VISITORS])
        self.assertEqual(s["not_attributed_gb"], 1.0)
        self.assertEqual(s["total_usd"], 0.25)
        self.assertEqual(json.loads(N.FILE.read_text())["2026-10-28"]["_total"], 5_000_000_000)
        self.assertEqual(real(str(dev)), 6_000_000_000)          # loopback left out


if __name__ == "__main__":
    unittest.main()
