"""Pure parts of the paid API: quote maths, job views, the daily-cap rule, the database store, and the link
between the API price percentage and the website's real price calculators."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api_jobs
import api_money
import api_store
from api_core import Conflict
from api_testkit import FakeDB


class Money(unittest.TestCase):
    def test_estimate_numbers(self):
        self.assertEqual(api_money.numbers_estimate(3, 0)["fixed"], 3)
        self.assertEqual(api_money.numbers_estimate(3, 3)["fixed"], 0)       # already paid: a retry is free

    def test_analysis_numbers_match_the_website(self):
        n = api_money.numbers_analysis({"analysis": 10, "flat": 10, "speaker_check": 2, "total": 100}, {"fee": 3, "analysis": 0})
        self.assertEqual((n["fixed"], n["required_balance"], n["already_paid"]), (22, 97, 3))
        self.assertEqual(api_money.numbers_analysis({"analysis": 10, "flat": 10, "total": 100}, {"fee": 3, "analysis": 20})["fixed"], 0)

    def test_dub_numbers(self):
        price = {"due": 31, "voice": 20, "clones": 10, "merge": 1, "already_paid": 23}
        a = api_money.numbers_dub(price, 20, True)
        self.assertEqual((a["fixed"], a["additional_max"], a["required_balance"]), (31, 20, 51))
        self.assertEqual(api_money.numbers_dub(price, 20, False)["additional_max"], 0)
        lip = dict(price, lipsync={"credits": 7})
        self.assertIn(("lipsync", 7), api_money.numbers_dub(lip, 0, True)["breakdown"])

    def test_bad_amounts_refused(self):
        for bad in (-1, 2 ** 31):
            with self.assertRaises(ValueError):
                api_money.numbers_estimate(bad, 0)
        with self.assertRaises(ValueError):
            api_money.build_quote("q", "j", "clone", api_money.numbers_estimate(3, 0), 0, 0, "v", "t")

    def test_price_version_changes_with_any_price_or_percent(self):
        base = {"fee": 3, "flat": 10, "name": "x"}
        v = api_money.price_version(base, 100)
        self.assertEqual(v, api_money.price_version(dict(base, name="y"), 100))        # non-numbers are ignored
        self.assertNotEqual(v, api_money.price_version(dict(base, fee=4), 100))
        self.assertNotEqual(v, api_money.price_version(base, 101))

    def _stored(self, **kw):
        q = {"quoted_credits": 20, "fixed_credits": 20, "additional_max_credits": 0, "price_version": "v", "revision": 0, "terms_version": "T"}
        q.update(kw)
        return q

    def test_check_accept_order(self):
        live = {"fixed": 20, "additional_max": 0}
        ok = {"quoted_credits": 20, "max_credits": 20, "terms_version": "T"}
        chk = lambda stored=None, body=None, lv=None, pv="v", rev=0, exp=100, now=50: api_money.check_accept(
            stored or self._stored(), dict(ok, **(body or {})), lv or live, pv, rev, exp, now)
        self.assertIsNone(chk())
        self.assertEqual(api_money.check_accept(None, ok, live, "v", 0, 100, 50), "not_found")
        self.assertEqual(chk(now=100), "quote_changed")
        self.assertEqual(chk(body={"terms_version": "X"}), "consent_required")
        self.assertEqual(chk(body={"quoted_credits": 19}), "quote_changed")
        self.assertEqual(chk(lv={"fixed": 25, "additional_max": 0}), "max_credits_exceeded")
        self.assertEqual(chk(lv={"fixed": 10, "additional_max": 0}), "quote_changed")
        self.assertEqual(chk(lv={"fixed": 10, "additional_max": 10}), "quote_changed")      # same total, different split
        self.assertEqual(chk(pv="other"), "quote_changed")
        self.assertEqual(chk(rev=1), "quote_changed")
        self.assertEqual(chk(body={"max_credits": 19}), "max_credits_exceeded")
        self.assertEqual(chk(body={"max_credits": True}), "max_credits_exceeded")

    def test_cap_check(self):
        self.assertTrue(api_money.cap_check(10, 20, 30))
        self.assertFalse(api_money.cap_check(11, 20, 30))
        self.assertFalse(api_money.cap_check(0, 1, None))
        self.assertFalse(api_money.cap_check(0, -1, 5))
        self.assertTrue(api_money.cap_check(0, 0, 0))

    def test_selection_key_is_order_free_and_distinct(self):
        self.assertEqual(api_money.selection_key(True, True, ["b", "a"]), api_money.selection_key(True, True, ["a", "b"]))
        self.assertNotEqual(api_money.selection_key(True, True, []), api_money.selection_key(False, True, []))


class Jobs(unittest.TestCase):
    def job(self, **kw):
        j = {"id": "j", "uid": "u", "status": "estimated", "stage": "estimate", "created": 1.5, "name": "n", "filename": "secret.mp4",
             "paid": {"fee": 3, "analysis": 0, "dub": 0}, "ops": {"fee": {"id": "o", "amount": 3, "refunds": {"x": 1}}},
             "api": {"revision": 2, "op": {"id": "op", "quote_id": "q", "step": "estimate", "maximum_credits": 3}}}
        j.update(kw)
        return j

    def test_view_has_only_allowed_fields(self):
        v = api_jobs.job_view(self.job(), "req", False)
        self.assertNotIn("filename", v)
        self.assertNotIn("uid", v)
        self.assertEqual(v["revision"], 2)
        self.assertEqual((v["current_operation"]["credits_debited"], v["current_operation"]["credits_refunded"]), (3, 1))

    def test_unknown_status_and_stage_fall_back(self):
        v = api_jobs.job_view(self.job(status="weird", stage="lipsync"), "req", False)
        self.assertIn(v["status"], api_jobs.STATUSES)
        self.assertIn(v["stage"], api_jobs.STAGES)
        self.assertEqual(api_jobs.job_view(self.job(status="dubbing", stage="lipsync"), "r", False)["stage"], "mix")

    def test_failed_job_has_closed_error(self):
        v = api_jobs.job_view(self.job(status="failed", error="Traceback inworld secret"), "req", False)
        self.assertNotIn("inworld", str(v["error"]).lower())
        self.assertIn("code", v["error"])

    def test_gone(self):
        for s in api_jobs.GONE:
            self.assertTrue(api_jobs.is_gone({"id": "j", "status": s}))
        self.assertTrue(api_jobs.is_gone(None))
        self.assertFalse(api_jobs.is_gone({"id": "j", "status": "done"}))

    def test_results_view(self):
        r = api_jobs.results_view({"mixed": (5, ".mp4"), "background": (1, ".m4a")})
        self.assertEqual([x["kind"] for x in r["results"]], ["mixed", "background"])
        self.assertEqual(r["results"][0]["media_type"], "video/mp4")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.s = api_store.Store(type("D", (), {"select": self.db.select, "insert": self.db.insert, "update": self.db.update, "delete": self.db.delete})())

    def test_one_operation_per_step(self):
        op = {"id": "a", "quote_id": "q1", "job_id": "j", "step": "dub", "revision": 0, "selection": "s"}
        self.assertTrue(self.s.claim_operation(op))
        self.assertFalse(self.s.claim_operation(dict(op, id="b", quote_id="q2")))         # other quote, same step
        self.assertFalse(self.s.claim_operation(dict(op, id="c")))                       # same quote again
        self.assertTrue(self.s.claim_operation(dict(op, id="d", quote_id="q3", revision=1)))
        self.assertEqual(self.s.get_operation("j", "dub", 0, "s")["id"], "a")
        self.s.release_operation("a")
        self.assertIsNone(self.s.get_operation("j", "dub", 0, "s"))

    def test_spend_is_counted_once_per_step(self):
        self.s.reserve("k", "2026-10-09", "j", "dub", 30)
        self.s.reserve("k", "2026-10-09", "j", "dub", 40)                                # the same step again: refreshed, not added
        self.s.reserve("k", "2026-10-09", "j", "analysis", 5)
        self.assertEqual(self.s.spent_by_others("k", "2026-10-09", "j", "dub"), 5)
        self.assertEqual(self.s.spent_by_others("k", "2026-10-09", "other", "dub"), 45)
        self.assertEqual(self.s.spent_by_others("k", "2026-10-10", "j", "dub"), 0)       # next day starts empty
        self.s.release_spend("k", "j", "dub")
        self.assertEqual(self.s.spent_by_others("k", "2026-10-09", "x", "x"), 5)

    def test_request_records(self):
        self.assertTrue(self.s.claim_request("k", "idem-0000000000000001", "fp", 10.0))
        self.assertFalse(self.s.claim_request("k", "idem-0000000000000001", "fp", 11.0))
        self.assertEqual(self.s.get_request("k", "idem-0000000000000001")["state"], "in_progress")
        self.s.complete_request("k", "idem-0000000000000001", 202, {"a": 1})
        r = self.s.get_request("k", "idem-0000000000000001")
        self.assertEqual((r["state"], r["status"], r["response"]), ("complete", 202, {"a": 1}))
        self.s.drop_request("k", "idem-0000000000000001")
        self.assertIsNone(self.s.get_request("k", "idem-0000000000000001"))


class RealPricing(unittest.TestCase):
    """The API price percentage goes through the website's own calculators."""
    def setUp(self):
        try:
            import longdub_service
        except ImportError as ex:           # a development computer without the audio libraries the server has
            raise unittest.SkipTest("longdub_service needs libraries that are not installed here: %s" % ex)
        self.ls = longdub_service
        self.cfg = {"fee": 3, "analysis_per_min": 2.0, "flat": 10, "clone_credits": 5, "merge_credits": 1, "chars_per_credit": 60,
                    "speaker_check_flat": 0, "speaker_check_per_min": 0.0, "music_fill_credits": 10, "lipsync_per_sec": 40.0, "max_min": 60}
        self.old = self.ls.Hooks.pricing
        self.ls.configure(pricing=lambda: dict(self.cfg))
        from unittest import mock
        self.patch = mock.patch.object(self.ls, "_lip_rate", lambda cfg, res=None: 0)      # lip-sync is not part of the API; other tests may leave the config module changed
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.ls.Hooks.pricing = self.old

    def test_website_jobs_and_100_percent_are_identical(self):
        web = self.ls._job_pricing({"id": "x"})
        self.assertEqual(web, self.cfg)
        self.assertEqual(self.ls._job_pricing({"id": "x", "api": {"price_percent": 100}}), self.cfg)
        self.assertEqual(self.ls._job_pricing({"id": "x", "api": {}}), self.cfg)

    def test_percent_scales_the_estimate_and_never_the_limits(self):
        j = {"id": "x", "api": {"price_percent": 150}}
        c = self.ls._job_pricing(j)
        self.assertEqual((c["fee"], c["flat"], c["clone_credits"]), (5, 15, 8))
        self.assertEqual(c["max_min"], 60)
        base = self.ls.compute_estimate(600, self.cfg, 2)
        up = self.ls.compute_estimate(600, c, 2)
        self.assertGreater(up["total"], base["total"])
        self.assertLessEqual(up["total"], base["total"] * 1.6)
        down = self.ls._job_pricing({"id": "x", "api": {"price_percent": 50}})
        self.assertLess(self.ls.compute_estimate(600, down, 2)["total"], base["total"])
        self.assertGreaterEqual(down["merge_credits"], 1)


if __name__ == "__main__":
    unittest.main()
