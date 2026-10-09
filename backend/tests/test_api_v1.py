"""Public API phase 1: key check, limits, scopes, errors, key management. Fake database, no network."""
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api_keys
import api_settings
import api_v1

from api_testkit import PEPPER, OWNER, OTHER, FakeDB, Harness


class KeyCheckTests(unittest.TestCase):
    def test_valid_key_reads_balance(self):
        h = Harness()
        r = h.c.get("/v1/account/balance", headers=h.bearer(h.make_key()))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"credits": 1234, "subscription_credits": 234, "permanent_credits": 1000})
        self.assertTrue(r.headers["X-Request-Id"].startswith("req_"))

    def test_missing_wrong_malformed_all_invalid_key(self):
        h = Harness()
        h.make_key()
        other = api_keys.generate_key()
        cases = [{}, h.bearer(other), {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"},
                 {"Authorization": "bearer"}, {"Authorization": "Bearer " + "x" * 500}]
        bodies = set()
        for hdr in cases:
            r = h.c.get("/v1/account/balance", headers=hdr)
            self.assertEqual(r.status_code, 401, hdr)
            self.assertEqual(r.json()["error"]["code"], "invalid_key")
            bodies.add(r.json()["error"]["message"])
        self.assertEqual(len(bodies), 1)                 # nothing says which part was wrong

    def test_key_in_url_or_cookie_is_ignored(self):
        h = Harness()
        k = h.make_key()
        self.assertEqual(h.c.get("/v1/account/balance?api_key=" + k).status_code, 401)
        h.c.cookies.set("session", k)
        self.assertEqual(h.c.get("/v1/account/balance").status_code, 401)

    def test_revoked_and_expired_keys_rejected_same_way(self):
        h = Harness()
        a = h.make_key(state="revoked")
        b = h.make_key(expires="2020-01-01T00:00:00+00:00")
        c = h.make_key(expires="garbage")
        for k in (a, b, c):
            r = h.c.get("/v1/account/balance", headers=h.bearer(k))
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "invalid_key"))

    def test_future_expiry_ok(self):
        h = Harness()
        k = h.make_key(expires="2999-01-01T00:00:00+00:00")
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(k)).status_code, 200)

    def test_scope_required(self):
        h = Harness()
        k = h.make_key(scopes=("read",))
        r = h.c.get("/v1/account/balance", headers=h.bearer(k))
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "scope_required"))

    def test_corrupt_scopes_fail_closed(self):
        h = Harness()
        k = h.make_key(scopes=("read", "root"))
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(k)).status_code, 403)

    def test_api_off_means_unavailable(self):
        h = Harness(enabled=False)
        r = h.c.get("/v1/account/balance", headers=h.bearer(h.make_key()))
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (503, "service_unavailable"))

    def test_missing_server_secret_is_unavailable_not_open(self):
        h = Harness()
        k = h.make_key()
        h.pepper = None
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(k)).status_code, 503)

    def test_database_down_is_unavailable(self):
        h = Harness()
        k = h.make_key()
        h.db.fail = True
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(k)).status_code, 503)

    def test_plan_gate(self):
        h = Harness()
        k = h.make_key()
        h.eligible = False
        r = h.c.get("/v1/account/balance", headers=h.bearer(k))
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "plan_required"))
        h.eligible = True
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(k)).status_code, 200)

    def test_unreadable_balance_is_503_never_zero(self):
        h = Harness(burst=50)
        k = h.make_key()
        good = {"credits": 5, "subscription_credits": 2, "permanent_credits": 3}
        for bad in (None, 12, "12", {}, {"credits": 5}, dict(good, credits=6), dict(good, credits="5"), dict(good, permanent_credits=-3, credits=-1),
                    dict(good, credits=True, subscription_credits=0, permanent_credits=1), dict(good, credits=1.5)):
            h.balance = bad
            self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(k)).status_code, 503, bad)
        h.balance = {"credits": 0, "subscription_credits": 0, "permanent_credits": 0}
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(k)).json()["credits"], 0)

    def test_balance_matches_the_published_contract(self):
        import json
        spec = json.loads((Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.yaml").read_text(encoding="utf-8"))
        schema = spec["components"]["schemas"]["Balance"]
        h = Harness()
        body = h.c.get("/v1/account/balance", headers=h.bearer(h.make_key())).json()
        self.assertEqual(set(body), set(schema["properties"]))
        self.assertEqual(set(body), set(schema["required"]))

    def test_responses_are_not_cacheable_and_leak_no_secret(self):
        h = Harness()
        k = h.make_key()
        r = h.c.get("/v1/account/balance", headers=h.bearer(k))
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertNotIn(k, r.text)
        self.assertNotIn("key_hash", r.text)


class RateLimitTests(unittest.TestCase):
    def test_burst_then_429_with_retry_after_then_refill(self):
        h = Harness(burst=3, requests_per_minute=60)
        k = h.make_key()
        codes = [h.c.get("/v1/account/balance", headers=h.bearer(k)).status_code for _ in range(5)]
        self.assertEqual(codes, [200, 200, 200, 429, 429])
        r = h.c.get("/v1/account/balance", headers=h.bearer(k))
        self.assertEqual(r.json()["error"]["code"], "rate_limited")
        self.assertGreaterEqual(int(r.headers["Retry-After"]), 1)
        h.clock[0] += 2
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(k)).status_code, 200)

    def test_each_key_has_its_own_bucket(self):
        h = Harness(burst=1, requests_per_minute=1)
        a, b = h.make_key(), h.make_key(uid=OTHER)
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(a)).status_code, 200)
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(a)).status_code, 429)
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(b)).status_code, 200)

    def test_admin_change_applies_to_new_limit(self):
        h = Harness(burst=1, requests_per_minute=60)
        k = h.make_key()
        h.c.get("/v1/account/balance", headers=h.bearer(k))
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(k)).status_code, 429)
        h.settings.update(burst=10)
        h.clock[0] += 5
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(k)).status_code, 200)

    def test_bad_key_does_not_consume_anyones_bucket(self):
        h = Harness(burst=1, requests_per_minute=1)
        k = h.make_key()
        for _ in range(5):
            h.c.get("/v1/account/balance", headers=h.bearer(api_keys.generate_key()))
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(k)).status_code, 200)


class OwnerKeyTests(unittest.TestCase):
    def create(self, h, **body):
        payload = {"name": "my app", "daily_credit_cap": 300, "accept_terms": True, "confirm_rights": True}
        payload.update(body)
        return h.c.post("/api/api-keys", json=payload)

    def test_create_shows_secret_once_and_stores_only_hash(self):
        h = Harness()
        r = self.create(h)
        self.assertEqual(r.status_code, 201)
        key = r.json()["key"]
        self.assertRegex(key, r"^lsn_live_[A-Za-z0-9_-]{32}$")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        row = h.db.t["api_keys"][0]
        self.assertNotIn(key, str(row))
        self.assertEqual(row["key_hash"], api_keys.storage_hash(key, PEPPER))
        self.assertEqual(row["daily_credit_cap"], 300)
        listing = h.c.get("/api/api-keys")
        self.assertNotIn(key, listing.text)
        self.assertNotIn("key_hash", listing.text)
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(key)).status_code, 200)

    def test_key_scopes_follow_the_owner_choice(self):
        h = Harness()
        self.assertEqual(sorted(self.create(h).json()["scopes"]), ["account", "dub", "read"])       # default: may start paid dubbing
        self.assertEqual(sorted(self.create(h, can_dub=False).json()["scopes"]), ["account", "read"])
        self.assertEqual(self.create(h, can_dub="yes").status_code, 400)
        self.assertEqual(self.create(h, can_dub=None).status_code, 400)
        self.assertEqual(len(h.db.t["api_keys"]), 2)

    def test_daily_cap_is_required_and_bounded(self):
        h = Harness(max_daily_credit_cap=1000)
        for bad in (None, 0, -1, 1001, "x"):
            r = self.create(h, daily_credit_cap=bad)
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(len(h.db.t["api_keys"]), 0)

    def test_terms_required_first_time_only(self):
        h = Harness()
        self.assertEqual(self.create(h, accept_terms=False).status_code, 403)
        self.assertEqual(self.create(h, confirm_rights=False).status_code, 403)
        self.assertEqual(h.db.t["api_owner_acceptances"], [])
        self.assertEqual(self.create(h).status_code, 201)
        self.assertEqual(len(h.db.t["api_owner_acceptances"]), 1)
        r = h.c.post("/api/api-keys", json={"name": "second", "daily_credit_cap": 10})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(len(h.db.t["api_owner_acceptances"]), 1)

    def test_key_limit_per_account(self):
        h = Harness(keys_per_account=2)
        self.assertEqual(self.create(h).status_code, 201)
        self.assertEqual(self.create(h).status_code, 201)
        self.assertEqual(self.create(h).status_code, 409)

    def test_not_eligible_or_off_or_logged_out(self):
        h = Harness()
        h.eligible = False
        self.assertEqual(self.create(h).status_code, 403)
        h.eligible = True
        h.settings["enabled"] = False
        self.assertEqual(self.create(h).status_code, 403)
        h.settings["enabled"] = True
        h.owner = None
        self.assertEqual(self.create(h).status_code, 401)
        self.assertEqual(h.c.get("/api/api-keys").status_code, 401)

    def test_no_secret_means_no_key(self):
        h = Harness()
        h.pepper = None
        self.assertEqual(self.create(h).status_code, 503)

    def test_name_checked(self):
        h = Harness()
        for bad in ("", "  ", "x" * 61, None, 5):
            self.assertEqual(self.create(h, name=bad).status_code, 400, bad)

    def test_garbage_body(self):
        h = Harness()
        self.assertEqual(h.c.post("/api/api-keys", content=b"not json").status_code, 400)
        self.assertEqual(h.c.post("/api/api-keys", json=[1]).status_code, 400)

    def test_revoke_stops_the_key_and_is_owner_only(self):
        h = Harness()
        key = self.create(h).json()["key"]
        kid = h.db.t["api_keys"][0]["id"]
        h.owner = OTHER
        self.assertEqual(h.c.post("/api/api-keys/%s/revoke" % kid).status_code, 404)
        self.assertEqual(h.c.get("/v1/account/balance", headers=h.bearer(key)).status_code, 200)
        h.owner = OWNER
        self.assertEqual(h.c.post("/api/api-keys/%s/revoke" % kid).status_code, 200)
        self.assertEqual(h.c.post("/api/api-keys/%s/revoke" % kid).status_code, 200)       # again: harmless
        r = h.c.get("/v1/account/balance", headers=h.bearer(key))
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "invalid_key"))

    def test_revoke_bad_ids(self):
        h = Harness()
        for bad in ("zzz", "../x", "1;drop"):
            self.assertEqual(h.c.post("/api/api-keys/%s/revoke" % bad).status_code, 404)

    def test_list_only_own_keys(self):
        h = Harness()
        self.create(h)
        h.make_key(uid=OTHER)
        rows = h.c.get("/api/api-keys").json()["keys"]
        self.assertEqual(len(rows), 1)

    def test_an_api_key_cannot_manage_keys(self):
        h = Harness()
        k = h.make_key()
        h.owner = None
        r = h.c.post("/api/api-keys", json={"name": "x", "daily_credit_cap": 5, "accept_terms": True, "confirm_rights": True},
                     headers=h.bearer(k))
        self.assertEqual(r.status_code, 401)


class PepperTests(unittest.TestCase):
    def test_pepper_from_env(self):
        self.assertIsNone(api_v1.pepper_from_env({}))
        self.assertIsNone(api_v1.pepper_from_env({"API_KEY_PEPPER": "short"}))
        self.assertIsNone(api_v1.pepper_from_env({"API_KEY_PEPPER": "x" * 40, "API_KEY_PEPPER_VERSION": "zero"}))
        self.assertEqual(api_v1.pepper_from_env({"API_KEY_PEPPER": "x" * 40}), (1, b"x" * 40))
        self.assertEqual(api_v1.pepper_from_env({"API_KEY_PEPPER": "x" * 40, "API_KEY_PEPPER_VERSION": "3"})[0], 3)


if __name__ == "__main__":
    unittest.main()
