"""Admin-controlled API settings: defaults, validation, price rule, limiter config. Pure, offline."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api_settings as S


def good(**kw):
    body = dict(S.DEFAULTS)
    body.update(kw)
    return body


class NormalizeTests(unittest.TestCase):
    def test_garbage_gives_safe_defaults_and_api_off(self):
        for raw in (None, "x", 5, [], {}, {"enabled": "maybe"}, {"enabled": 1}, {"enabled": None}):
            out = S.normalize(raw)
            self.assertFalse(out["enabled"], raw)
            self.assertEqual(out["requests_per_minute"], 60)
            self.assertFalse(out["webhooks"])

    def test_values_out_of_range_fall_back(self):
        out = S.normalize({"price_percent": 5, "requests_per_minute": 10 ** 9, "burst": -1, "concurrency_per_key": 0,
                           "keys_per_account": "abc", "default_daily_credit_cap": True})
        self.assertEqual(out["price_percent"], 100)
        self.assertEqual(out["requests_per_minute"], 60)
        self.assertEqual(out["burst"], 10)
        self.assertEqual(out["concurrency_per_key"], 1)
        self.assertEqual(out["keys_per_account"], 5)
        self.assertEqual(out["default_daily_credit_cap"], 1000)

    def test_strings_and_whole_floats_accepted(self):
        out = S.normalize({"enabled": "true", "requests_per_minute": "120", "burst": 20.0})
        self.assertTrue(out["enabled"])
        self.assertEqual(out["requests_per_minute"], 120)
        self.assertEqual(out["burst"], 20)

    def test_fractional_and_nan_rejected(self):
        self.assertEqual(S.normalize({"burst": 2.5})["burst"], 10)
        self.assertEqual(S.normalize({"burst": float("nan")})["burst"], 10)
        self.assertEqual(S.normalize({"burst": float("inf")})["burst"], 10)

    def test_default_cap_never_above_max(self):
        out = S.normalize({"default_daily_credit_cap": 5000, "max_daily_credit_cap": 100})
        self.assertLessEqual(out["default_daily_credit_cap"], out["max_daily_credit_cap"])

    def test_webhooks_always_off(self):
        self.assertFalse(S.normalize({"webhooks": True})["webhooks"])

    def test_input_not_mutated(self):
        raw = {"burst": 3}
        S.normalize(raw)
        self.assertEqual(raw, {"burst": 3})


class ValidateTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        clean, err = S.validate(good())
        self.assertIsNone(err)
        self.assertEqual(clean["price_percent"], 100)

    def test_each_number_names_its_field_and_range(self):
        for name, (_d, lo, hi, label) in S.NUMBERS.items():
            for bad in (lo - 1, hi + 1, "x", None, 1.5, True):
                clean, err = S.validate(good(**{name: bad}))
                self.assertIsNone(clean, (name, bad))
                self.assertIn(label, err)
                self.assertIn(str(lo), err)

    def test_switches_must_be_booleans(self):
        for name in ("enabled", "jobs_visible"):
            clean, err = S.validate(good(**{name: "yes"}))
            self.assertIsNone(clean)
            self.assertIn(name.replace("_", " "), err)

    def test_eligibility_must_be_known(self):
        self.assertIsNotNone(S.validate(good(eligibility="free"))[1])
        self.assertIsNone(S.validate(good(eligibility="everyone"))[1])

    def test_default_cap_cannot_exceed_max(self):
        self.assertIsNotNone(S.validate(good(default_daily_credit_cap=500, max_daily_credit_cap=100))[1])

    def test_burst_far_above_rate_refused(self):
        self.assertIsNotNone(S.validate(good(requests_per_minute=1, burst=11))[1])
        self.assertIsNone(S.validate(good(requests_per_minute=1, burst=10))[1])

    def test_not_a_dict(self):
        for body in (None, [], "x", 3):
            self.assertIsNone(S.validate(body)[0])

    def test_webhooks_cannot_be_turned_on_through_save(self):
        clean, _ = S.validate(good(webhooks=True))
        self.assertFalse(clean["webhooks"])


class PriceTests(unittest.TestCase):
    def test_100_is_the_website_price(self):
        for c in (0, 1, 7, 1234):
            self.assertEqual(S.apply_price(c, {"price_percent": 100}), c)

    def test_rounds_up_never_below_one(self):
        self.assertEqual(S.apply_price(7, {"price_percent": 150}), 11)      # 10.5 -> 11
        self.assertEqual(S.apply_price(1, {"price_percent": 50}), 1)
        self.assertEqual(S.apply_price(3, {"price_percent": 50}), 2)
        self.assertEqual(S.apply_price(0, {"price_percent": 300}), 0)

    def test_bad_amounts_refused(self):
        for bad in (-1, "x", None):
            with self.assertRaises(ValueError):
                S.apply_price(bad, {})

    def test_exact_integer_arithmetic(self):
        self.assertEqual(S.apply_price(10 ** 9, {"price_percent": 300}), 3 * 10 ** 9)


class LimiterAndCapTests(unittest.TestCase):
    def test_limiter_config(self):
        cfg = S.limiter_config({"requests_per_minute": 120, "burst": 20})
        self.assertEqual(cfg, {"capacity": 20, "refill_per_second": 2.0})

    def test_daily_cap_required_and_bounded(self):
        s = {"max_daily_credit_cap": 5000}
        self.assertEqual(S.daily_cap_for(300, s), (300, None))
        for bad in (None, "", 0, -5, 5001, "abc", True, 1.5):
            cap, err = S.daily_cap_for(bad, s)
            self.assertIsNone(cap, bad)
            self.assertTrue(err)

    def test_public_view_has_no_internal_fields(self):
        v = S.public_view({"enabled": True})
        self.assertNotIn("eligibility", v)
        self.assertNotIn("jobs_visible", v)
        self.assertEqual(v["requests_per_minute"], 60)


if __name__ == "__main__":
    unittest.main()
