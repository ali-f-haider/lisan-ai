"""What a finished minute costs: the figure on the Pricing page and the line on the dubbing page."""
import io
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cost_per_minute as cpm
import job_usage

BACKEND = Path(__file__).resolve().parents[1]


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def gen(job, uid="u1", credits=60, secs=120.0, action="generate"):
    return {"job_id": job, "uid": uid, "action": action, "credits": credits, "generated_seconds": secs}


class BuildProjectsTests(unittest.TestCase):
    def test_short_dub_counts_every_credit_but_the_minutes_once(self):
        rows = [gen("j1", credits=40, secs=120), gen("j1", credits=40, secs=120),   # the voice was made twice
                {"job_id": "j1", "uid": "u1", "action": "transcribe", "credits": 10, "generated_seconds": None},
                {"job_id": "j1", "uid": "u1", "action": "refund", "credits": -10, "generated_seconds": None}]
        (p,) = cpm.build_projects(rows)
        self.assertEqual((p["credits"], p["seconds"]), (80, 120.0))      # 80 credits for ONE two-minute dub = 40 a minute

    def test_long_dub_uses_the_project_length_and_folds_corrections_into_the_project(self):
        rows = [gen("L1", credits=300, secs=None, action="long_dub_dub"),
                gen("L1", credits=20, secs=None, action="long_dub_estimate"),
                gen("C1", credits=30, secs=None, action="long_dub_correction")]
        meta = {"L1": {"duration": 600, "edit_of": None}, "C1": {"duration": 600, "edit_of": "L1"}}
        (p,) = cpm.build_projects(rows, meta)
        self.assertEqual((p["credits"], p["seconds"]), (350, 600))       # 350 credits / 10 minutes = 35 a minute

    def test_projects_with_unknown_or_tiny_length_or_no_net_cost_are_left_out(self):
        rows = [gen("short", secs=5), gen("nolen", secs=None), gen("free", credits=0, secs=60),
                gen("L9", credits=50, secs=None, action="long_dub_dub")]        # long dub whose file is gone: no meta
        self.assertEqual(cpm.build_projects(rows, {}), [])

    def test_bad_rows_do_not_break_it(self):
        rows = ["x", None, {"job_id": None, "credits": 5}, {"job_id": "a", "credits": "7"}, {"job_id": "a", "credits": 1.5},
                {"job_id": "a", "credits": True}, gen("ok", credits=30, secs=60)]
        self.assertEqual([p["credits"] for p in cpm.build_projects(rows)], [30])


class SummaryTests(unittest.TestCase):
    def projects(self, n, people=6, per_min=40.0):
        return [{"uid": f"u{i % people}", "credits": per_min * (i % 5 + 1) / 2, "seconds": 30.0 * (i % 5 + 1)} for i in range(n)]

    def test_typical_is_the_median_with_the_middle_half_as_range(self):
        ps = [{"uid": f"u{i}", "credits": c, "seconds": 60.0} for i, c in enumerate([10, 20, 30, 40, 50, 60, 70, 80, 90, 100] * 2)]
        s = cpm.summarize(ps)
        self.assertEqual(s["median"], 55.0)
        self.assertLess(s["low"], s["median"]); self.assertGreater(s["high"], s["median"])
        self.assertEqual(s["projects"], 20)

    def test_one_huge_project_cannot_move_it(self):
        base = [{"uid": f"u{i}", "credits": 40, "seconds": 60.0} for i in range(30)]
        self.assertEqual(cpm.summarize(base + [{"uid": "u99", "credits": 1_000_000, "seconds": 60.0}])["median"], 40.0)

    def test_hidden_until_enough_projects_and_enough_different_people(self):
        self.assertIsNone(cpm.summarize(self.projects(19)))
        self.assertIsNone(cpm.summarize(self.projects(40, people=4)))
        self.assertIsNotNone(cpm.summarize(self.projects(20, people=5)))
        self.assertIsNone(cpm.summarize([]))
        self.assertIsNone(cpm.summarize(None))

    def test_the_summary_has_only_totals(self):
        s = cpm.summarize(self.projects(30))
        self.assertEqual(set(s), {"projects", "people", "median", "low", "high"})


class MoneyTests(unittest.TestCase):
    PACKS = [{"credits": 1500, "price_usd": 15.0, "bonus_pct": 0}, {"credits": 25000, "price_usd": 150.0, "bonus_pct": 66}]
    PLANS = [{"credits_per_month": 2000, "price_usd": 19.0}]

    def test_rate_range_uses_bonus_credits(self):
        lo, hi = cpm.usd_per_credit_range(self.PACKS, self.PLANS)
        self.assertAlmostEqual(lo, 150.0 / 41500, places=6)
        self.assertAlmostEqual(hi, 0.0100, places=6)

    def test_bad_price_rows_are_ignored(self):
        self.assertIsNone(cpm.usd_per_credit_range([{"credits": 0, "price_usd": 5}, {"credits": "x"}, "y", None], [{}]))
        self.assertIsNone(cpm.usd_per_credit_range(None, None))

    def test_public_figure(self):
        s = {"projects": 30, "people": 8, "median": 50.0, "low": 40.0, "high": 70.0}
        f = cpm.public_figure(s, self.PACKS, self.PLANS)
        self.assertTrue(f["ok"]); self.assertEqual(f["days"], 90)
        self.assertEqual(f["usd_low"], round(50 * 150 / 41500, 2)); self.assertEqual(f["usd_high"], 0.5)
        self.assertEqual(cpm.public_figure(None, self.PACKS, self.PLANS), {"ok": False})
        self.assertNotIn("usd_low", cpm.public_figure(s, [], []))


class ReadingTests(unittest.TestCase):
    def test_pages_are_read_until_a_short_page_and_new_style_keys_are_not_sent_as_bearer(self):
        seen = []
        pages = [[gen("a")] * cpm.PAGE_SIZE, [gen("b")] * 3]
        def opener(request, timeout=0):
            seen.append(request)
            return Response(json.dumps(pages[len(seen) - 1]).encode())
        rows = cpm.fetch_rows("https://db", "sb_secret_abc", "2026-07-01T00:00:00Z", opener)
        self.assertEqual(len(rows), cpm.PAGE_SIZE + 3); self.assertEqual(len(seen), 2)
        self.assertIn("offset=1000", seen[1].full_url); self.assertIn("job_id=not.is.null", seen[0].full_url)
        self.assertIsNone(seen[0].get_header("Authorization")); self.assertEqual(seen[0].get_header("Apikey"), "sb_secret_abc")

    def test_unreadable_history_is_none_and_compute_raises_so_it_is_retried_soon(self):
        def boom(request, timeout=0):
            raise OSError("down")
        self.assertIsNone(cpm.fetch_rows("https://db", "k", "2026-07-01T00:00:00Z", boom))
        self.assertIsNone(cpm.fetch_rows("", "k", "x"))
        with self.assertRaises(RuntimeError):
            cpm.compute("https://db", "k", lambda j: None, opener=boom)

    def test_compute_reads_project_files_only_for_long_dub_jobs(self):
        rows = [gen(f"s{i}", uid=f"u{i % 6}", credits=40, secs=60) for i in range(25)] + [gen("L1", credits=100, secs=None, action="long_dub_dub")]
        asked = []
        def meta(job_id):
            asked.append(job_id)
            return {"duration": 300, "edit_of": None}
        out = cpm.compute("https://db", "k", meta, opener=lambda r, timeout=0: Response(json.dumps(rows).encode()))
        self.assertEqual(asked, ["L1"])
        self.assertEqual(out["projects"], 26)

    def test_a_missing_or_broken_project_file_just_drops_that_project(self):
        rows = [gen(f"s{i}", uid=f"u{i % 6}", credits=40, secs=60) for i in range(25)] + [gen("L1", credits=100, secs=None, action="long_dub_dub")]
        def meta(job_id):
            raise ValueError("bad json")
        out = cpm.compute("https://db", "k", meta, opener=lambda r, timeout=0: Response(json.dumps(rows).encode()))
        self.assertEqual(out["projects"], 25)


class CacheTests(unittest.TestCase):
    def setUp(self):
        cpm.reset_for_tests()

    def test_first_call_returns_nothing_then_the_refreshed_value_and_does_not_refresh_again(self):
        calls = []
        def refresh():
            calls.append(1)
            return {"median": 42.0}
        self.assertIsNone(cpm.cached(refresh, start_thread=False))      # refreshes now, returns what was cached before
        self.assertEqual(cpm.cached(refresh, start_thread=False), {"median": 42.0})
        self.assertEqual(len(calls), 1)

    def test_stale_value_is_refreshed_and_a_failure_keeps_the_old_value_and_waits_before_retrying(self):
        state = {"n": 0}
        def refresh():
            state["n"] += 1
            if state["n"] == 2:
                raise RuntimeError("down")
            return {"median": state["n"]}
        now = time.time()
        cpm.cached(refresh, now=now, start_thread=False)
        later = now + cpm.CACHE_SECONDS + 5
        self.assertEqual(cpm.cached(refresh, now=later, start_thread=False), {"median": 1})     # stale: refresh fails, old value kept
        self.assertEqual(state["n"], 2)
        cpm.cached(refresh, now=later + 60, start_thread=False)         # inside the retry pause: no new try
        self.assertEqual(state["n"], 2)


class JobUsageTests(unittest.TestCase):
    def opener(self, rows, seen=None):
        def op(request, timeout=0):
            if seen is not None:
                seen.append(request.full_url)
            return Response(json.dumps(rows).encode())
        return op

    def test_per_minute_uses_the_longest_generate_row_and_all_the_credits(self):
        rows = [{"credits": 30, "action": "generate", "generated_seconds": 120}, {"credits": 30, "action": "generate", "generated_seconds": 118},
                {"credits": 20, "action": "transcribe", "generated_seconds": None}]
        got = job_usage.for_job("u", "j", "https://db", "k", self.opener(rows))
        self.assertEqual(got, {"credits_charged": 80, "credits_refunded": 0, "finished_seconds": 120.0, "credits_per_minute": 40.0})

    def test_no_length_in_the_history_keeps_the_old_answer(self):
        got = job_usage.for_job("u", "j", "https://db", "k", self.opener([{"credits": 8, "action": "merge", "generated_seconds": None}]))
        self.assertEqual(got, {"credits_charged": 8, "credits_refunded": 0})

    def test_a_clip_under_twenty_seconds_gets_no_per_minute_price(self):
        got = job_usage.for_job("u", "j", "https://db", "k", self.opener([{"credits": 8, "action": "generate", "generated_seconds": 9}]))
        self.assertNotIn("credits_per_minute", got)

    def test_when_the_length_column_is_missing_the_credits_are_still_read(self):
        calls = []
        def op(request, timeout=0):
            calls.append(request.full_url)
            if "generated_seconds" in request.full_url:
                raise OSError("column does not exist")
            return Response(json.dumps([{"credits": 8}]).encode())
        got = job_usage.for_job("u", "j", "https://db", "k", op)
        self.assertEqual(got, {"credits_charged": 8, "credits_refunded": 0})
        self.assertEqual(len(calls), 2)


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.main = (BACKEND / "main.py").read_text(encoding="utf-8")
        self.pricing = (BACKEND / "pricing.html").read_text(encoding="utf-8")
        self.js = (BACKEND / "app.js").read_text(encoding="utf-8")

    def test_route_is_public_and_can_be_switched_off(self):
        self.assertIn('"/api/cost_per_minute"', self.main[self.main.index("PUBLIC_PATHS"):self.main.index("PUBLIC_PATHS") + 900])
        self.assertIn('@app.get("/api/cost_per_minute")', self.main)
        self.assertIn("SHOW_COST_PER_MINUTE", self.main)

    def test_route_returns_no_customer_data(self):
        route = self.main[self.main.index('@app.get("/api/cost_per_minute")'):]
        route = route[:route.index("def _cpm_warm_up")]
        for word in ("uid", "job_id", "email", "request"):
            self.assertNotIn(word, route.replace("cost_per_minute_public()", ""))

    def test_pricing_page_block_starts_hidden_and_needs_a_good_answer(self):
        self.assertIn('id="realCost" style="display:none"', self.pricing)
        self.assertIn('fetch("/api/cost_per_minute")', self.pricing)
        self.assertIn("!d.ok", self.pricing)
        self.assertIn("What a finished minute really costs", self.pricing)
        self.assertIn("كم تكلّف الدقيقة المنتهية فعلًا", self.pricing)

    def test_dubbing_page_shows_the_per_minute_line_only_when_the_server_sends_it(self):
        self.assertIn("u.credits_per_minute", self.js)
        self.assertIn("per finished minute", self.js)


if __name__ == "__main__":
    unittest.main()
