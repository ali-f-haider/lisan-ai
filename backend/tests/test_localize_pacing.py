"""Voice localization stays inside the provider's starts-per-minute limit.

Before this, every speaker's localization was started at the same moment, so
with a limit of two a third and fourth speaker were refused and silently fell
back to the plain copied voice. These tests use an invented clock and an
invented provider: nothing waits, nothing is sent anywhere."""
import email.message
import io
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import inworld_service as iw


class Clock:
    """A clock that only moves when somebody sleeps."""
    def __init__(self):
        self.now = 0.0
        self.slept = []
        self._lock = threading.Lock()

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        with self._lock:
            self.slept.append(seconds)
            self.now += seconds


def refused(retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://example.invalid", 429, "Too Many Requests", headers, io.BytesIO(b"{}"))


def server_error():
    return urllib.error.HTTPError("https://example.invalid", 500, "Server Error", email.message.Message(), io.BytesIO(b"{}"))


LOCALIZED = {"draftVoiceId": "draft", "candidates": [{"candidateId": "one"}]}


class Provider:
    """Stands in for iw._request. `script` maps a path suffix to a list of
    replies (a dict answers; an exception is raised), used in order."""
    def __init__(self, script=None):
        self.script = script or {}
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, method, path, key, body=None, timeout=30):
        with self._lock:
            self.calls.append(path)
            for suffix, replies in self.script.items():
                if path.endswith(suffix) and replies:
                    reply = replies.pop(0)
                    if isinstance(reply, Exception):
                        raise reply
                    return reply
        return LOCALIZED if path.endswith(":localize") else {}

    def count(self, suffix):
        return sum(1 for p in self.calls if p.endswith(suffix))


class PacerTests(unittest.TestCase):
    def test_only_the_limit_may_start_in_one_window(self):
        clock = Clock()
        pacer = iw.StartPacer(2, 62, clock=clock, sleep=clock.sleep)
        starts = []
        for _ in range(5):
            pacer.wait()
            starts.append(clock.now)
        self.assertEqual(starts, [0, 0, 62, 62, 124])

    def test_nothing_waits_while_under_the_limit(self):
        clock = Clock()
        pacer = iw.StartPacer(3, 62, clock=clock, sleep=clock.sleep)
        self.assertEqual([pacer.wait() for _ in range(3)], [0.0, 0.0, 0.0])
        self.assertEqual(clock.slept, [])

    def test_a_window_that_has_passed_frees_the_slots(self):
        clock = Clock()
        pacer = iw.StartPacer(2, 62, clock=clock, sleep=clock.sleep)
        pacer.wait(); pacer.wait()
        clock.now = 100
        self.assertEqual(pacer.wait(), 0.0)
        self.assertEqual(clock.slept, [])

    def test_the_reported_wait_matches_the_time_spent(self):
        clock = Clock()
        pacer = iw.StartPacer(1, 60, clock=clock, sleep=clock.sleep)
        pacer.wait()
        clock.now = 10
        self.assertAlmostEqual(pacer.wait(), 50.0, places=3)

    def test_threads_sharing_a_pacer_never_exceed_the_limit(self):
        clock = Clock()
        pacer = iw.StartPacer(2, 62, clock=clock, sleep=clock.sleep)
        sleeps_when_not_full = []
        real_sleep = pacer._sleep

        def checked_sleep(seconds):
            if len(pacer._starts) < pacer.limit:
                sleeps_when_not_full.append(seconds)
            real_sleep(seconds)
        pacer._sleep = checked_sleep
        grants = []
        done = []

        def start():
            pacer.wait()
            assert len(pacer._starts) <= pacer.limit
            grants.append(1)
            done.append(True)
        threads = [threading.Thread(target=start) for _ in range(7)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(len(done), 7)
        self.assertEqual(sleeps_when_not_full, [])       # it only ever waited while full
        self.assertGreaterEqual(clock.now, 3 * 62)       # 7 starts at 2 a minute need three full windows

    def test_defaults_match_the_documented_limit(self):
        self.assertEqual(iw.LOCALIZE_STARTS_PER_MINUTE, 2)
        self.assertGreater(iw.LOCALIZE_WINDOW_S, 60)
        self.assertEqual(iw._localize_pacer.limit, iw.LOCALIZE_STARTS_PER_MINUTE)


class RetryAfterTests(unittest.TestCase):
    def test_a_usable_header_is_followed(self):
        self.assertEqual(iw._retry_after(refused(7), 20), 7.0)

    def test_a_missing_or_unusable_header_uses_the_default(self):
        self.assertEqual(iw._retry_after(refused(), 20), 20)
        self.assertEqual(iw._retry_after(refused("soon"), 20), 20)
        self.assertEqual(iw._retry_after(refused(0), 20), 20)
        self.assertEqual(iw._retry_after(refused(-5), 20), 20)
        self.assertEqual(iw._retry_after(ValueError("no headers at all"), 20), 20)

    def test_a_very_long_request_is_capped_at_two_minutes(self):
        self.assertEqual(iw._retry_after(refused(900), 20), 120.0)


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.pacer = iw.StartPacer(2, 62, clock=self.clock, sleep=self.clock.sleep)
        self.waits = []
        patches = [
            mock.patch.object(iw, "_localize_pacer", self.pacer),
            mock.patch.object(iw.time, "sleep", lambda s: self.waits.append(s)),
            mock.patch.object(iw.random, "uniform", lambda a, b: 0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def use(self, provider):
        p = mock.patch.object(iw, "_request", provider)
        p.start()
        self.addCleanup(p.stop)
        return provider


class LocalizeManyTests(Base):
    def test_four_speakers_all_succeed_and_starts_are_spread_out(self):
        provider = self.use(Provider())
        out = iw.localize_many({f"s{i}": f"v{i}" for i in range(4)}, "key")
        self.assertEqual(sorted(out), ["s0", "s1", "s2", "s3"])
        self.assertTrue(all(r["ok"] for r in out.values()), out)
        self.assertEqual(provider.count(":localize"), 4)
        self.assertEqual(provider.count(":approveLocalization"), 4)
        self.assertEqual(self.waits, [])                  # no provider refusal at all
        self.assertGreaterEqual(self.clock.now, 62)       # the third and fourth began a window later

    def test_two_speakers_start_at_once(self):
        self.use(Provider())
        out = iw.localize_many({"a": "v1", "b": "v2"}, "key")
        self.assertTrue(all(r["ok"] for r in out.values()))
        self.assertEqual(self.clock.now, 0)

    def test_no_voices_does_nothing(self):
        provider = self.use(Provider())
        self.assertEqual(iw.localize_many({}, "key"), {})
        self.assertEqual(provider.calls, [])

    def test_a_failing_speaker_does_not_stop_the_others(self):
        provider = self.use(Provider({":localize": [server_error()]}))
        out = iw.localize_many({"a": "v1", "b": "v2", "c": "v3"}, "key")
        self.assertEqual(sorted(r["ok"] for r in out.values()), [False, True, True])
        failed = [r for r in out.values() if not r["ok"]][0]
        self.assertIn("500", failed["error"])
        self.assertEqual(provider.count(":localize"), 3)  # a server error is not repeated


class RefusalTests(Base):
    def test_a_refusal_waits_as_long_as_the_provider_asked_then_succeeds(self):
        provider = self.use(Provider({":localize": [refused(7)]}))
        res = iw.localize_voice("v1", "key")
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.waits, [7.0])
        self.assertEqual(provider.count(":localize"), 2)

    def test_a_refusal_without_a_header_uses_the_old_waits(self):
        self.use(Provider({":localize": [refused(), refused()]}))
        res = iw.localize_voice("v1", "key")
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.waits, [20, 40])

    def test_every_attempt_goes_through_the_pacer(self):
        self.use(Provider({":localize": [refused(1)] * 3}))
        res = iw.localize_voice("v1", "key")
        self.assertTrue(res["ok"], res)
        # four attempts at two starts a minute: the third and fourth wait for a window
        self.assertGreaterEqual(self.clock.now, 62)

    def test_it_gives_up_after_the_last_attempt_and_reports_why(self):
        provider = self.use(Provider({":localize": [refused(1)] * 10}))
        res = iw.localize_voice("v1", "key")
        self.assertFalse(res["ok"])
        self.assertEqual(provider.count(":localize"), 4)
        self.assertEqual(provider.count(":approveLocalization"), 0)

    def test_a_refused_approval_is_repeated_without_localizing_again(self):
        provider = self.use(Provider({":approveLocalization": [refused(3)]}))
        res = iw.localize_voice("v1", "key")
        self.assertTrue(res["ok"], res)
        self.assertEqual(provider.count(":localize"), 1)
        self.assertEqual(provider.count(":approveLocalization"), 2)
        self.assertEqual(self.waits, [3.0])

    def test_an_approval_that_keeps_being_refused_fails_cleanly(self):
        provider = self.use(Provider({":approveLocalization": [refused(1)] * 10}))
        res = iw.localize_voice("v1", "key")
        self.assertFalse(res["ok"])
        self.assertEqual(provider.count(":approveLocalization"), 4)

    def test_other_errors_are_not_repeated(self):
        provider = self.use(Provider({":approveLocalization": [server_error()]}))
        res = iw.localize_voice("v1", "key")
        self.assertFalse(res["ok"])
        self.assertEqual(provider.count(":approveLocalization"), 1)
        self.assertEqual(self.waits, [])

    def test_without_a_key_nothing_is_sent_and_nothing_waits(self):
        provider = self.use(Provider())
        res = iw.localize_voice("v1", "")
        self.assertFalse(res["ok"])
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.clock.now, 0)


class CallSiteTests(unittest.TestCase):
    def test_both_callers_still_go_through_localize_many(self):
        """Pacing lives in localize_voice, so every caller gets it; this keeps
        a future caller from bypassing it by calling the provider directly."""
        text = (ROOT / "longdub_service.py").read_text(encoding="utf-8") + (ROOT / "inworld_service.py").read_text(encoding="utf-8")
        self.assertEqual(text.count(":localize\""), 1)    # the one provider call, inside localize_voice


if __name__ == "__main__":
    unittest.main()
