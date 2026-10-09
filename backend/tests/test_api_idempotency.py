import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest

import api_idempotency as ai


class ApiIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.key = "logical_request_123456"
        self.fp = ai.fingerprint("post", "/v1/jobs", {"a": 1, "b": "مرحبا"})
        self.record = {"key": self.key, "fingerprint": self.fp, "created_at": 100, "state": "complete"}

    def test_valid_keys_have_bounded_url_safe_ascii(self):
        self.assertTrue(ai.valid_key(self.key))
        for value in ("short", "a" * 129, "x" * 16 + "\n", None, {}, "ع" * 16):
            self.assertFalse(ai.valid_key(value))

    def test_canonical_hash_ignores_dictionary_order_but_preserves_semantics(self):
        self.assertEqual(self.fp, ai.fingerprint("POST", "/v1/jobs", {"b": "مرحبا", "a": 1}))
        self.assertNotEqual(self.fp, ai.fingerprint("POST", "/v1/jobs", {"a": 2, "b": "مرحبا"}))
        self.assertNotEqual(self.fp, ai.fingerprint("PUT", "/v1/jobs", {"a": 1, "b": "مرحبا"}))
        self.assertNotEqual(self.fp, ai.fingerprint("POST", "/v1/jobs/other", {"a": 1, "b": "مرحبا"}))
        self.assertNotEqual(ai.fingerprint("POST", "/v1/jobs", [1, 2]), ai.fingerprint("POST", "/v1/jobs", [2, 1]))

    def test_fingerprint_rejects_non_json_and_ambiguous_targets(self):
        for body in ({"x": float("nan")}, {1: "a"}, {"x": b"bytes"}, {"x": "\ud800"}):
            with self.subTest(body=type(body)), self.assertRaises(ValueError):
                ai.fingerprint("POST", "/v1/jobs", body)
        for path in ("/jobs", "/v1/jobs?x=1", "/v1/jobs#x", "/v1/jobs\n"):
            with self.assertRaises(ValueError):
                ai.fingerprint("POST", path, {})
        nested = None
        for _ in range(22):
            nested = [nested]
        with self.assertRaises(ValueError):
            ai.fingerprint("POST", "/v1/jobs", nested)

    def test_new_replay_conflict_and_exact_ttl_boundary(self):
        self.assertEqual(ai.decide(self.key, self.fp, None, 100), "new")
        self.assertEqual(ai.decide(self.key, self.fp, self.record, 101), "replay")
        self.assertEqual(ai.decide(self.key, "0" * 64, self.record, 101), "conflict")
        self.assertEqual(ai.decide(self.key, self.fp, self.record, 100 + ai.TTL_SECONDS - 1), "replay")
        self.assertEqual(ai.decide(self.key, self.fp, self.record, 100 + ai.TTL_SECONDS), "new")

    def test_uncertain_in_flight_operation_does_not_expire_into_new_work(self):
        self.record["state"] = "in_progress"
        self.assertEqual(ai.decide(self.key, self.fp, self.record, 100 + 3 * ai.TTL_SECONDS), "replay")
        self.assertEqual(ai.decide(self.key, "f" * 64, self.record, 100 + 3 * ai.TTL_SECONDS), "conflict")

    def test_corrupt_record_or_clock_regression_never_starts_work(self):
        self.assertEqual(ai.decide(self.key, self.fp, self.record, 99), "conflict")
        for record in ({}, "bad", dict(self.record, created_at=float("nan")), dict(self.record, state="unknown")):
            self.assertEqual(ai.decide(self.key, self.fp, record, 101), "conflict")
        for key, fp, now in (("bad", self.fp, 100), (self.key, "bad", 100), (self.key, self.fp, float("inf"))):
            with self.assertRaises(ValueError):
                ai.decide(key, fp, None, now)
