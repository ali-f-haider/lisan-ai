"""The "Under Construction" gate must not come back because the database was slow for a moment.

On 2026-10-09 a settings read timed out once; the old code treated that as "use the defaults", and the default is gate ON,
so for about 20 seconds testers were locked out although the admin switch was off."""
import ast
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
import site_gate  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Reader:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


def make(reader, path, clock, **kw):
    return site_gate.GateSwitch(reader, path, ttl=20, retry=10, clock=clock, background=False, log=lambda *_: None, **kw)


class GateSwitchTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "site_gate_state.json"
        self.clock = Clock()

    def tearDown(self):
        self.dir.cleanup()

    def test_a_failed_read_keeps_a_gate_that_was_switched_off(self):
        reader = Reader(False, TimeoutError("The read operation timed out"), False)
        gate = make(reader, self.path, self.clock)
        self.assertFalse(gate.enabled())
        self.clock.now += 21
        self.assertFalse(gate.enabled(), "the timeout must not turn the gate back on")
        self.clock.now += 11
        self.assertFalse(gate.enabled())

    def test_a_failed_read_keeps_a_gate_that_is_on(self):
        reader = Reader(True, TimeoutError("x"))
        gate = make(reader, self.path, self.clock)
        self.assertTrue(gate.enabled())
        self.clock.now += 21
        self.assertTrue(gate.enabled())

    def test_after_a_restart_the_last_known_value_is_used_even_if_the_database_is_down(self):
        make(Reader(False), self.path, self.clock).enabled()                    # first run reads "off" and saves it
        restarted = make(Reader(TimeoutError("down")), self.path, Clock())
        self.assertFalse(restarted.enabled())

    def test_never_read_and_cannot_read_closes_the_gate(self):
        gate = make(Reader(TimeoutError("down")), self.path, self.clock)
        self.assertTrue(gate.enabled())

    def test_no_saved_value_means_the_default_on(self):
        gate = make(Reader(None), self.path, self.clock)
        self.assertTrue(gate.enabled())

    def test_a_change_made_in_admin_is_picked_up_after_the_refresh_time(self):
        reader = Reader(True, False)
        gate = make(reader, self.path, self.clock)
        self.assertTrue(gate.enabled())
        self.clock.now += 5
        self.assertTrue(gate.enabled())
        self.assertEqual(reader.calls, 1, "no database call inside the refresh time")
        self.clock.now += 20
        self.assertFalse(gate.enabled())

    def test_after_a_failure_it_retries_soon_but_not_on_every_request(self):
        reader = Reader(False, TimeoutError("x"), False)
        gate = make(reader, self.path, self.clock)
        gate.enabled()
        self.clock.now += 21
        gate.enabled()                                   # fails
        calls = reader.calls
        self.clock.now += 3
        gate.enabled()
        self.assertEqual(reader.calls, calls, "too early to try again")
        self.clock.now += 8
        gate.enabled()
        self.assertEqual(reader.calls, calls + 1)

    def test_saving_in_admin_applies_at_once_and_is_kept_for_a_restart(self):
        gate = make(Reader(True), self.path, self.clock)
        self.assertTrue(gate.enabled())
        gate.apply(False)
        self.assertFalse(gate.enabled())
        self.assertFalse(make(Reader(TimeoutError("down")), self.path, Clock()).enabled())

    def test_an_unreadable_saved_file_is_ignored(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertTrue(make(Reader(TimeoutError("down")), self.path, self.clock).enabled())
        self.path.write_text(json.dumps({"enabled": "no"}), encoding="utf-8")
        self.assertTrue(make(Reader(TimeoutError("down")), self.path, self.clock).enabled())

    def test_a_slow_refresh_never_holds_up_a_request(self):
        release = threading.Event()
        started = threading.Event()

        def slow():
            started.set()
            release.wait(5)
            return True

        self.path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
        gate = site_gate.GateSwitch(slow, self.path, ttl=20, retry=10, clock=self.clock, background=True, log=lambda *_: None)
        answer = gate.enabled()                          # returns at once with the saved value
        self.assertFalse(answer)
        self.assertTrue(started.wait(2))
        self.assertFalse(gate.enabled(), "still the old value while the refresh is running, and no second refresh")
        release.set()
        for _ in range(100):
            if gate.enabled():
                break
            threading.Event().wait(0.02)
        self.assertTrue(gate.enabled())


class WiringTests(unittest.TestCase):
    """main.py is not imported (heavy); its source is read."""

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((BACKEND / "main.py").read_text(encoding="utf-8"))

    def _fn(self, name):
        return next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == name)

    @staticmethod
    def _names(fn):
        """Every name the code of the function uses (comments and docstrings do not count)."""
        return {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}

    def test_the_gate_check_no_longer_goes_through_the_defaults_loader(self):
        names = self._names(self._fn("_site_gate_active"))
        self.assertNotIn("_get_pricing_config", names)
        self.assertIn("_gate_switch", names)

    def test_the_gate_reader_raises_instead_of_answering_with_defaults(self):
        fn = self._fn("_read_site_gate_flag")
        self.assertFalse([n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)], "must let a failure through")
        self.assertNotIn("_get_pricing_config", self._names(fn))

    def test_saving_the_settings_applies_the_switch_at_once(self):
        source = (BACKEND / "main.py").read_text(encoding="utf-8")
        route = next(n for n in self.tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "admin_save_pricing")
        self.assertIn("_gate_switch.apply(", ast.get_source_segment(source, route))


if __name__ == "__main__":
    unittest.main()
