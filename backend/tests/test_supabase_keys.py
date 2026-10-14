"""Supabase project keys: old keys keep working exactly as before, new sb_ keys go in `apikey` only.

Nothing here talks to Supabase: requests are caught before they leave."""
import io
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import supabase_keys as sk
import job_usage

OLD_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c2lnbmF0dXJl"
SECRET = "sb_secret_abcDEF123"
PUBLISHABLE = "sb_publishable_abcDEF123"


class HelperTests(unittest.TestCase):
    def test_an_old_key_is_sent_as_bearer_exactly_as_before(self):
        self.assertEqual(sk.sb_auth(OLD_KEY), {"Authorization": f"Bearer {OLD_KEY}"})

    def test_new_keys_send_no_authorization_header(self):
        self.assertEqual(sk.sb_auth(SECRET), {})
        self.assertEqual(sk.sb_auth(PUBLISHABLE), {})
        self.assertEqual(sk.sb_auth("  " + SECRET), {})

    def test_the_two_kinds_are_told_apart(self):
        self.assertTrue(sk.is_new_style_key(SECRET))
        self.assertTrue(sk.is_new_style_key(PUBLISHABLE))
        self.assertFalse(sk.is_new_style_key(OLD_KEY))
        self.assertFalse(sk.is_new_style_key(""))
        self.assertFalse(sk.is_new_style_key(None))

    def test_a_key_that_is_not_recognised_behaves_as_it_always_did(self):
        self.assertEqual(sk.sb_auth("something-else"), {"Authorization": "Bearer something-else"})

    def test_it_can_be_merged_into_a_header_dict(self):
        old = {"apikey": OLD_KEY, **sk.sb_auth(OLD_KEY), "Prefer": "return=minimal"}
        new = {"apikey": SECRET, **sk.sb_auth(SECRET), "Prefer": "return=minimal"}
        self.assertEqual(set(old), {"apikey", "Authorization", "Prefer"})
        self.assertEqual(new, {"apikey": SECRET, "Prefer": "return=minimal"})


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class RequestTests(unittest.TestCase):
    def headers_for(self, key):
        seen = []

        def opener(req, timeout=0):
            seen.append({k.lower(): v for k, v in req.header_items()})
            return Response(json.dumps([{"credits": 5}]).encode())
        out = job_usage.for_job("u1", "j1", "https://x.supabase.co", key, opener=opener)
        self.assertEqual(out, {"credits_charged": 5, "credits_refunded": 0})
        return seen[0]

    def test_an_old_key_request_is_unchanged(self):
        h = self.headers_for(OLD_KEY)
        self.assertEqual(h["apikey"], OLD_KEY)
        self.assertEqual(h["authorization"], f"Bearer {OLD_KEY}")

    def test_a_new_key_request_has_only_the_apikey_header(self):
        h = self.headers_for(SECRET)
        self.assertEqual(h["apikey"], SECRET)
        self.assertNotIn("authorization", h)

    def test_the_database_backup_reader_does_the_same(self):
        try:
            import r2_backup
        except Exception as e:                     # botocore not installed here
            self.skipTest(f"r2_backup cannot be imported: {e}")
        import urllib.request
        seen = []
        real = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=0: (seen.append({k.lower(): v for k, v in req.header_items()}), Response(b"[]"))[1]
        try:
            r2_backup._fetch_table("https://x.supabase.co", SECRET, "profiles")
            r2_backup._fetch_table("https://x.supabase.co", OLD_KEY, "profiles")
        finally:
            urllib.request.urlopen = real
        self.assertNotIn("authorization", seen[0])
        self.assertEqual(seen[1]["authorization"], f"Bearer {OLD_KEY}")


def _modules():
    skip = ("test_", "patch_", "fix_", "add_", "apply_", "check_")
    for f in sorted(ROOT.glob("*.py")):
        if f.name.startswith(skip) or f.name in ("pricing_sync.py",):          # pricing_sync.py is an old one-off patch script
            continue
        if re.search(r"-[0-9]{1,6}\.", f.name.lower()):                          # backup copies like longdub_service-1.py
            continue
        yield f


class NoModuleSendsAProjectKeyAsBearerTests(unittest.TestCase):
    BAD = re.compile(r"""Bearer\s*(\{\s*(SUPABASE_SERVICE_KEY|SUPABASE_ANON_KEY|service_key|self\.key|self\.anon)\b|["']\s*\+\s*\(?\s*(token\s+or\s+)?(self\.)?(anon|service_key|key)\b)""")

    def test_every_request_uses_the_helper(self):
        bad = []
        for f in _modules():
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if self.BAD.search(line):
                    bad.append(f"{f.name}:{n}: {line.strip()[:100]}")
        self.assertEqual(bad, [])

    def test_the_scan_would_catch_the_old_way(self):
        for line in ('h = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}',
                     'headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"}',
                     'headers = {"apikey": self.anon, "Authorization": "Bearer " + (token or self.anon)}',
                     '"Authorization": f"Bearer {self.key}"'):
            self.assertTrue(self.BAD.search(line), line)
        for line in ('"Authorization": f"Bearer {sb_token}"', '"Authorization": f"Bearer {access_token}"',
                     '"Authorization": f"Bearer {RESEND_API_KEY}"'):
            self.assertFalse(self.BAD.search(line), line)

    def test_every_module_that_calls_the_helper_imports_it(self):
        missing = []
        for f in _modules():
            text = f.read_text(encoding="utf-8", errors="ignore")
            if "sb_auth(" in text and f.name != "supabase_keys.py" and not re.search(r"^\s*from supabase_keys import .*\bsb_auth\b", text, re.M):
                missing.append(f.name)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
