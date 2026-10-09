import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import unittest

import api_errors as ae


class ApiErrorsTests(unittest.TestCase):
    def test_all_codes_have_status_retry_and_fixed_public_body(self):
        for code, (status, retry, message) in ae.ERRORS.items():
            with self.subTest(code=code):
                self.assertIn(status, (400, 401, 402, 403, 404, 409, 410, 413, 415, 429, 500, 503))
                self.assertIsInstance(retry, bool)
                self.assertEqual(ae.error_body(code, "request_1"), {"error": {
                    "code": code, "message": message, "request_id": "request_1"}})

    def test_untrusted_errors_and_request_ids_are_never_echoed(self):
        secret = "C:/private/secret.txt\naccess_token=do-not-return"
        text = json.dumps(ae.error_body(secret, secret))
        self.assertNotIn("secret", text)
        self.assertIn('"code": "internal_error"', text)
        self.assertIn('"request_id": "unavailable"', text)
        self.assertEqual(ae.error_body({}, None)["error"]["code"], "internal_error")

    def test_exact_app_errors_are_mapped_without_substring_guessing(self):
        self.assertEqual(ae.from_app_error("This job is not waiting for approval.", 409), "job_not_ready")
        self.assertEqual(ae.from_app_error("The upload didn't finish. Please try again.", 409), "upload_incomplete")
        self.assertEqual(ae.from_app_error("This file has no audio track to dub.", 400), "unsupported_media")
        self.assertEqual(ae.from_app_error("Please check and confirm the current price before starting. The price may have changed.", 409), "quote_changed")
        self.assertEqual(ae.from_app_error("RuntimeError: insufficient credits from upstream", 500), "internal_error")
        self.assertEqual(ae.from_app_error(None, 402), "insufficient_credits")

    def test_every_http_fallback_is_closed(self):
        for status in (400, 401, 402, 403, 404, 409, 410, 413, 415, 429, 500, 503, 999):
            self.assertIn(ae.from_app_error("private failure", status), ae.ERRORS)
