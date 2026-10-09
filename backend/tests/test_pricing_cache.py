"""A slow or unreachable database must never swap the admin's saved prices and switches for the built-in defaults."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
import pricing_cache  # noqa: E402
from test_shortdub_upgrade import source_functions  # noqa: E402

SAVED = {"freeCredits": 250, "cloneCredits": 9, "charsPerCredit": 45, "packs": [{"credits": 1000, "price_usd": 10.0}],
         "siteGateEnabled": False, "assistant": {"enabled": True}}


class Clock:
    now = 5000.0

    def __call__(self):
        return self.now


class LastGoodTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "pricing_last_good.json"
        self.clock = Clock()

    def tearDown(self):
        self.dir.cleanup()

    def make(self):
        return pricing_cache.LastGood(self.path, clock=self.clock, log=lambda *_: None)

    def test_nothing_known_yet(self):
        self.assertEqual(self.make().recall(), (None, None))

    def test_remembers_and_returns_a_copy_that_cannot_change_the_saved_one(self):
        keep = self.make()
        keep.remember(SAVED)
        got, age = keep.recall()
        self.assertEqual(got, SAVED)
        got["cloneCredits"] = 1
        got["packs"].append({"credits": 1})
        self.assertEqual(keep.recall()[0], SAVED)

    def test_survives_a_restart(self):
        self.make().remember(SAVED)
        self.clock.now += 90
        got, age = self.make().recall()
        self.assertEqual(got, SAVED)
        self.assertGreaterEqual(age, 89)

    def test_a_newer_good_read_replaces_the_old_one(self):
        keep = self.make()
        keep.remember(SAVED)
        keep.remember(dict(SAVED, cloneCredits=12))
        self.assertEqual(self.make().recall()[0]["cloneCredits"], 12)

    def test_an_unreadable_disk_copy_is_ignored(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.make().recall(), (None, None))

    def test_no_temporary_files_are_left_behind(self):
        keep = self.make()
        keep.remember(SAVED)
        keep.remember(dict(SAVED, cloneCredits=3))
        self.assertEqual([p.name for p in Path(self.dir.name).iterdir()], ["pricing_last_good.json"])


class RealLoaderTests(unittest.TestCase):
    """The real _get_pricing_config from main.py, with a database that answers once and then times out."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.keep = pricing_cache.LastGood(Path(self.dir.name) / "p.json", log=lambda *_: None)
        self.defaults_packs = [{"credits": 1, "price_usd": 1.0}]
        self.env = source_functions("main.py", ["_get_pricing_config"], dict(
            SUPABASE_URL="https://example.test", SUPABASE_SERVICE_KEY="synthetic", DEFAULT_PACKS=self.defaults_packs,
            DEFAULT_SUBSCRIPTION_PLANS=[], json=json, _assistant_cfg=lambda v: v, _conc_cfg=lambda v: v,
            _gemini_cpc=lambda v: v, _pricing_last_good=self.keep))

    def tearDown(self):
        self.dir.cleanup()

    def row(self):
        return [{"free_credits": 250, "clone_credits": 9, "chars_per_credit": 45, "site_gate_enabled": False,
                 "packs": [{"credits": 1000, "price_usd": 10.0}]}]

    def test_a_timeout_after_a_good_read_keeps_the_saved_prices_and_switches(self):
        load = self.env["_get_pricing_config"]
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(self.row()).encode())):
            good = load()
        self.assertEqual(good["cloneCredits"], 9)
        with patch("urllib.request.urlopen", side_effect=TimeoutError("The read operation timed out")):
            during = load()
        self.assertEqual(during["cloneCredits"], 9, "the timeout must not bring back the default clone price")
        self.assertEqual(during["charsPerCredit"], 45)
        self.assertEqual(during["freeCredits"], 250)
        self.assertEqual(during["packs"], [{"credits": 1000, "price_usd": 10.0}])
        self.assertIs(during["siteGateEnabled"], False)

    def test_after_a_restart_during_an_outage_the_disk_copy_is_used(self):
        load = self.env["_get_pricing_config"]
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(self.row()).encode())):
            load()
        self.keep = pricing_cache.LastGood(Path(self.dir.name) / "p.json", log=lambda *_: None)     # a new process
        self.env["_pricing_last_good"] = self.keep
        with patch("urllib.request.urlopen", side_effect=TimeoutError("down")):
            self.assertEqual(load()["cloneCredits"], 9)

    def test_never_read_and_down_still_answers_with_the_defaults(self):
        load = self.env["_get_pricing_config"]
        with patch("urllib.request.urlopen", side_effect=TimeoutError("down")):
            got = load()
        self.assertEqual(got["packs"], self.defaults_packs)
        self.assertIs(got["siteGateEnabled"], True)

    def test_a_setting_added_after_the_saved_copy_falls_back_to_its_default(self):
        load = self.env["_get_pricing_config"]
        self.keep.remember({"cloneCredits": 9})              # an older copy without most keys
        with patch("urllib.request.urlopen", side_effect=TimeoutError("down")):
            got = load()
        self.assertEqual(got["cloneCredits"], 9)
        self.assertEqual(got["mergeCredits"], 1)             # the default

    def test_an_empty_table_is_still_the_defaults_not_an_old_copy(self):
        load = self.env["_get_pricing_config"]
        self.keep.remember({"cloneCredits": 9})
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b"[]")):
            self.assertEqual(load()["cloneCredits"], 5)


if __name__ == "__main__":
    unittest.main()
