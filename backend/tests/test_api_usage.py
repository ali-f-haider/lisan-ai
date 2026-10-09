import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import unittest

from api_usage import usage_report


def event(op, amount, at="2026-01-01T12:00:00Z", kind="debit", key="key-a"):
    return dict(api_key_id=key, operation_id=op, kind=kind, amount=amount,
                created_at=at, status="done", filename="PRIVATE-FILE", text="PRIVATE-TEXT")


class ApiUsageTests(unittest.TestCase):
    def test_totals_dedupe_refunds_and_privacy(self):
        debit = event("paid", 30)
        report = usage_report([debit, debit, event("refund", 12, kind="refund")], "key-a", "2026-01-01", "2026-01-02")
        self.assertEqual((report["calls"], report["credits_spent"], report["credits_refunded"], report["net_credits"]), (1, 30, 12, 18))
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertEqual(report["calls_meaning"], "paid_operations")

    def test_filter_keys_incomplete_events_and_inclusive_days(self):
        rows = [event("a", 10), event("b", 20, key="other"), dict(event("c", 10), status="pending"),
                {"credits": 100, "job_id": "unattributed"}, None, event("d", 99, at="2026-01-03T00:00:00Z")]
        report = usage_report(rows, "key-a", "2026-01-01", "2026-01-02")
        self.assertEqual(report["credits_spent"], 10)
        self.assertEqual(report["calls"], 1)

    def test_utc_conversion_and_refund_on_its_own_day(self):
        rows = [event("a", 10, at="2026-01-02T01:00:00+02:00"),
                event("b", 5, at="2026-01-02T03:00:00Z", kind="refund")]
        report = usage_report(rows, "key-a", "2026-01-01", "2026-01-02")
        self.assertEqual([r["day"] for r in report["by_day"]], ["2026-01-01", "2026-01-02"])
        self.assertEqual(report["by_day"][1]["net_credits"], -5)

    def test_bad_matching_money_or_date_is_not_silently_discarded(self):
        for row in (dict(event("a", 1), amount=True), event("a", -1), event("a", 1.5),
                    event("a", 1, at="2026-01-01T12:00:00"), event("", 1), event("a", 1, kind="unknown")):
            with self.subTest(row=row), self.assertRaises(ValueError):
                usage_report([row], "key-a", "2026-01-01", "2026-01-02")
        with self.assertRaises(ValueError):
            usage_report([event("a", 10), event("a", 11)], "key-a", "2026-01-01", "2026-01-02")

    def test_dates_are_bounded_and_empty_reports_are_explicit(self):
        self.assertEqual(usage_report([], "a", "2026-01-01", "2026-01-01")["by_day"], [])
        for first, last in (("2026-1-1", "2026-01-01"), ("bad", "bad"), ("2026-01-02", "2026-01-01"),
                            ("2024-01-01", "2026-01-01")):
            with self.assertRaises(ValueError):
                usage_report([], "a", first, last)
        with self.assertRaises(ValueError):
            usage_report([], "", "2026-01-01", "2026-01-01")
