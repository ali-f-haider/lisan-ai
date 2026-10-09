from copy import deepcopy
import unittest

import api_limits as al


class ApiLimitsTests(unittest.TestCase):
    def test_token_bucket_burst_and_fractional_refill(self):
        cfg, state = {"capacity": 2, "refill_per_second": .5}, {}
        for _ in range(2):
            allowed, retry, state = al.rate_limit("a", 100, cfg, state)
            self.assertTrue(allowed)
            self.assertEqual(retry, 0)
        allowed, retry, state = al.rate_limit("a", 101, cfg, state)
        self.assertFalse(allowed)
        self.assertEqual(retry, 1)
        self.assertTrue(al.rate_limit("a", 102, cfg, state)[0])

    def test_rate_state_is_independent_and_input_is_not_mutated(self):
        cfg, original = {"capacity": 1, "refill_per_second": 1}, {}
        _, _, state = al.rate_limit("a", 100, cfg, original)
        before = deepcopy(state)
        self.assertTrue(al.rate_limit("b", 100, cfg, state)[0])
        self.assertEqual(original, {})
        self.assertEqual(state, before)

    def test_clock_regression_does_not_create_tokens(self):
        cfg = {"capacity": 1, "refill_per_second": 1}
        _, _, state = al.rate_limit("a", 100, cfg, {})
        allowed, retry, after = al.rate_limit("a", 99, cfg, state)
        self.assertFalse(allowed)
        self.assertEqual(retry, 2)
        self.assertFalse(al.rate_limit("a", 100, cfg, after)[0])

    def test_invalid_limit_or_persisted_state_is_not_reset(self):
        for cfg, state in (({"capacity": 0, "refill_per_second": 1}, {}),
                           ({"capacity": 1, "refill_per_second": 0}, {}),
                           ({"capacity": 1, "refill_per_second": 1}, {"a": {"tokens": -1, "at": 100}})):
            with self.assertRaises(ValueError):
                al.rate_limit("a", 100, cfg, state)

    def test_concurrency_replay_capacity_and_explicit_release(self):
        yes, _, state = al.acquire("a", "op1", 1, {})
        self.assertTrue(yes)
        self.assertTrue(al.acquire("a", "op1", 1, state)[0])
        self.assertFalse(al.acquire("a", "op2", 1, state)[0])
        self.assertTrue(al.acquire("b", "op2", 1, state)[0])
        released = al.release("a", "op1", state)
        self.assertTrue(al.acquire("a", "op2", 1, released)[0])
        self.assertEqual(state, {"a": ["op1"]})
        self.assertEqual(al.release("a", "op1", released), released)

    def test_reservations_prevent_simultaneous_budget_overspend(self):
        yes, _, state = al.reserve_credits("a", "op1", 70, 100, 100, {})
        self.assertTrue(yes)
        yes, retry, after = al.reserve_credits("a", "op2", 31, 100, 100, state)
        self.assertFalse(yes)
        self.assertGreater(retry, 0)
        self.assertEqual(after, state)
        self.assertTrue(al.reserve_credits("a", "op2", 30, 100, 100, state)[0])
        self.assertTrue(al.reserve_credits("b", "op2", 100, 100, 100, state)[0])

    def test_pending_reservations_survive_midnight(self):
        _, _, state = al.reserve_credits("a", "op1", 70, 86399, 100, {})
        self.assertFalse(al.reserve_credits("a", "op2", 31, 86401, 100, state)[0])
        settled = al.settle_credits("a", "op1", 70, 86399, state)
        self.assertTrue(al.reserve_credits("a", "op2", 100, 86401, 100, settled)[0])

    def test_repeat_reservation_cannot_increase_approved_spending(self):
        _, _, state = al.reserve_credits("a", "op1", 30, 100, 100, {})
        self.assertTrue(al.reserve_credits("a", "op1", 30, 101, 100, state)[0])
        self.assertFalse(al.reserve_credits("a", "op1", 31, 101, 100, state)[0])

    def test_settlement_frees_unused_maximum_but_never_refunds_gross_cap(self):
        _, _, state = al.reserve_credits("a", "op1", 90, 100, 100, {})
        settled = al.settle_credits("a", "op1", 40, 101, state)
        self.assertTrue(al.reserve_credits("a", "op2", 60, 102, 100, settled)[0])
        self.assertFalse(al.reserve_credits("a", "op2", 61, 102, 100, settled)[0])
        self.assertEqual(al.settle_credits("a", "op1", 40, 999, settled), settled)
        with self.assertRaises(ValueError):
            al.settle_credits("a", "op1", 0, 102, settled)

    def test_proved_no_debit_releases_and_unknown_debit_stays_reserved(self):
        _, _, state = al.reserve_credits("a", "op1", 100, 100, 100, {})
        with self.assertRaises(ValueError):
            al.settle_credits("a", "op1", 101, 101, state)
        released = al.settle_credits("a", "op1", 0, 101, state)
        self.assertTrue(al.reserve_credits("a", "op2", 100, 102, 100, released)[0])
        self.assertFalse(al.reserve_credits("a", "op1", 100, 102, 100, released)[0])
        with self.assertRaises(ValueError):
            al.settle_credits("a", "missing", 0, 100, {})

    def test_zero_cost_does_not_need_a_daily_allowance(self):
        self.assertTrue(al.reserve_credits("a", "free", 0, 100, 0, {})[0])
        _, _, state = al.reserve_credits("a", "paid", 10, 100, 10, {})
        self.assertTrue(al.reserve_credits("a", "free", 0, 100, 0, state)[0])

    def test_budget_and_concurrency_reject_invalid_values(self):
        for value in (-1, True, 1.5, float("nan")):
            with self.assertRaises(ValueError):
                al.reserve_credits("a", "op", value, 100, 100, {})
        with self.assertRaises(ValueError):
            al.acquire("a", "op", 0, {})
