import unittest
from unittest.mock import patch

import api_keys as ak


class ApiKeysTests(unittest.TestCase):
    def setUp(self):
        self.key = "lsn_live_" + "A" * 32
        self.secret = b"server-side-pepper-for-testing-32bytes"

    def test_generated_keys_have_exact_format_and_use_secrets(self):
        with patch.object(ak.secrets, "token_urlsafe", return_value="B" * 32) as random:
            self.assertEqual(ak.generate_key(), "lsn_live_" + "B" * 32)
        random.assert_called_once_with(24)

    def test_dashboard_prefix_is_only_eight_suffix_characters(self):
        self.assertEqual(ak.dashboard_prefix(self.key), "A" * 8)
        with self.assertRaises(ValueError):
            ak.dashboard_prefix("lsn_live_invalid")

    def test_storage_hash_changes_with_pepper_and_is_not_plain_key(self):
        digest = ak.storage_hash(self.key, self.secret)
        self.assertEqual(len(digest), 64)
        self.assertNotIn(self.key, digest)
        self.assertNotEqual(digest, ak.storage_hash(self.key, b"x" * 32))
        for key, secret in ((self.key, b"short"), (None, self.secret), (self.key, "secret")):
            with self.subTest(key=type(key), secret=type(secret)), self.assertRaises(ValueError):
                ak.storage_hash(key, secret)

    def test_good_verification_uses_constant_time_compare(self):
        digest = ak.storage_hash(self.key, self.secret)
        with patch.object(ak.hmac, "compare_digest", wraps=ak.hmac.compare_digest) as compare:
            self.assertTrue(ak.verify_key(self.key, digest, self.secret))
        compare.assert_called_once_with(digest, digest)
        self.assertFalse(ak.verify_key(self.key, digest, b"x" * 32))
        for bad in (None, {}, "a" * 63, "g" * 64):
            self.assertFalse(ak.verify_key(self.key, bad, self.secret))
        self.assertFalse(ak.verify_key("bad", digest, self.secret))

    def test_authorization_accepts_only_one_bearer_header(self):
        self.assertEqual(ak.parse_authorization("bEaReR " + self.key), self.key)
        for bad in (None, {}, self.key, "Basic " + self.key, "Bearer  " + self.key,
                    "Bearer " + self.key + "\n", "Bearer " + self.key + ",other",
                    "?api_key=" + self.key, "Bearer\t" + self.key):
            with self.subTest(bad=type(bad)):
                self.assertIsNone(ak.parse_authorization(bad))

    def test_expiry_boundary_revocation_and_corrupt_states_fail_closed(self):
        self.assertEqual(ak.key_state({"state": "active", "expires_at": 101}, 100), "active")
        self.assertEqual(ak.key_state({"state": "active", "expires_at": 100}, 100), "expired")
        self.assertEqual(ak.key_state({"state": "revoked", "expires_at": 999}, 100), "revoked")
        for expiry in ("tomorrow", float("nan"), float("inf"), True, -1):
            self.assertEqual(ak.key_state({"state": "active", "expires_at": expiry}, 100), "expired")
        self.assertEqual(ak.key_state(None, 100), "revoked")
        self.assertEqual(ak.key_state({"state": "active"}, float("nan")), "expired")

    def test_scopes_are_explicit_without_implicit_privilege(self):
        record = {"state": "active", "scopes": ["dub"]}
        self.assertTrue(ak.has_scope(record, "dub", 100))
        self.assertFalse(ak.has_scope(record, "account", 100))
        self.assertFalse(ak.has_scope(record, [], 100))
        self.assertFalse(ak.has_scope(dict(record, state="revoked"), "dub", 100))
        for scopes in ("dub", ["dub", "admin"], [None], None):
            self.assertFalse(ak.has_scope(dict(record, scopes=scopes), "dub", 100))
