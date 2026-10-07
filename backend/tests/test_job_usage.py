"""'Credits used for this dubbing' is the real total of the project's credit history."""
import io, json, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import job_usage


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass


def opener_for(rows, seen=None):
    def opener(request, timeout=0):
        if seen is not None: seen.append(request.full_url)
        return Response(json.dumps(rows).encode())
    return opener


class TotalTests(unittest.TestCase):
    def test_every_charge_counts_not_just_the_last_one(self):
        rows = [{"credits": 5}, {"credits": 3}, {"credits": 40}, {"credits": 2}]
        self.assertEqual(job_usage.totals(rows), (50, 0))

    def test_refunds_are_taken_off_and_reported(self):
        self.assertEqual(job_usage.totals([{"credits": 40}, {"credits": -5}, {"credits": 2}]), (37, 5))

    def test_never_negative_and_bad_rows_are_ignored(self):
        self.assertEqual(job_usage.totals([{"credits": -9}]), (0, 9))
        self.assertEqual(job_usage.totals([{"credits": "7"}, {"credits": None}, {"credits": True}, {"credits": 1.5}, {}, "x", {"credits": 4.0}]), (4, 0))
        self.assertEqual(job_usage.totals(None), (0, 0))

    def test_the_request_is_for_this_account_and_this_project_only(self):
        seen = []
        got = job_usage.for_job("user-1", "job&x=1", "https://db", "key", opener_for([{"credits": 8}], seen))
        self.assertEqual(got, {"credits_charged": 8, "credits_refunded": 0})
        self.assertIn("uid=eq.user-1", seen[0]); self.assertIn("job_id=eq.job%26x%3D1", seen[0])

    def test_a_failed_or_odd_lookup_gives_none_so_the_page_keeps_what_it_had(self):
        def boom(request, timeout=0): raise OSError("down")
        self.assertIsNone(job_usage.for_job("u", "j", "https://db", "key", boom))
        self.assertIsNone(job_usage.for_job("u", "j", "https://db", "key", opener_for({"message": "error"})))
        self.assertIsNone(job_usage.for_job("", "j", "https://db", "key", opener_for([])))
        self.assertIsNone(job_usage.for_job("u", "j", "", "key", opener_for([])))


class WiringTests(unittest.TestCase):
    def test_the_usage_route_uses_the_history_total(self):
        src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        route = src[src.index('@app.get("/api/usage/{job_id}")'):]
        route = route[:route.index("@app.get(\"/api/download")]
        self.assertIn("job_usage.for_job(", route)
        self.assertIn("_current_uid(request)", route)
        self.assertNotIn("cost_usd", route)      # customers see credits only, never dollar costs
        self.assertIn("SUPABASE_URL, SUPABASE_SERVICE_KEY", route)


if __name__ == "__main__":
    unittest.main()
