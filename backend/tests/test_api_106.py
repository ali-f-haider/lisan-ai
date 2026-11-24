"""Public API catch-up (1.106.0): voice style on the dub quote, a job from a link, "As written" line ending."""
import unittest
from pathlib import Path
from unittest.mock import patch

import api_errors
import api_jobs
import api_money
import api_schema

ROOT = Path(__file__).resolve().parent.parent
TERMS = "2026-10"


class SchemaTests(unittest.TestCase):
    def test_voice_style_only_on_the_dub_quote(self):
        self.assertIsNone(api_schema.validate_body("QuoteRequest", {"step": "dub", "voice_style": "stable"}))
        self.assertIsNone(api_schema.validate_body("QuoteRequest", {"step": "dub"}))
        self.assertIsNotNone(api_schema.validate_body("QuoteRequest", {"step": "dub", "voice_style": "loud"}))
        self.assertIsNotNone(api_schema.validate_body("QuoteRequest", {"step": "analysis", "voice_style": "stable"}))

    def test_job_from_a_link(self):
        ok = {"kind": "long", "url": "https://drive.google.com/file/d/abc/view", "rights_confirmed": True, "terms_version": TERMS}
        self.assertIsNone(api_schema.validate_body("CreateJobFromLink", ok))
        self.assertIsNone(api_schema.validate_body("CreateJobFromLink", dict(ok, source_language="de", name="Film", stated_speakers=3)))
        for bad in (dict(ok, url="ftp://x.com/a.mp4"), dict(ok, url="https://x.com/a b.mp4"), dict(ok, kind="short"),
                    dict(ok, rights_confirmed=False), dict(ok, filename="a.mp4"), {k: v for k, v in ok.items() if k != "url"}):
            self.assertIsNotNone(api_schema.validate_body("CreateJobFromLink", bad), bad)

    def test_as_written_line_ending_is_known(self):
        self.assertIn("exact", api_schema.SEGMENT["properties"]["waqf"]["enum"])


class QuoteTests(unittest.TestCase):
    def _q(self, style):
        n = {"fixed": 10, "additional_max": 0, "required_balance": 10, "already_paid": 0, "breakdown": [("voices", 10)]}
        return api_money.build_quote("q", "j", "dub", n, 0, 1000.0, "pv", TERMS, True, True, [], style)

    def test_the_style_travels_in_the_quote(self):
        self.assertEqual(self._q("creative")["voice_style"], "creative")
        self.assertIsNone(self._q(None)["voice_style"])
        self.assertIsNone(self._q("odd")["voice_style"])


class LinkViewTests(unittest.TestCase):
    def test_link_state_in_the_upload_view(self):
        job = {"id": "j1", "size": 10, "chunk_bytes": 4, "total_chunks": 3, "received": [0, 1, 2],
               "link": {"url": "https://x", "kind": "direct", "state": "done", "error": ""}}
        with patch("link_fetch.active", return_value=False):
            self.assertEqual(api_jobs.upload_view(job)["link"], {"state": "done", "error": None})
            job["link"].update(state="fetching")
            self.assertEqual(api_jobs.upload_view(job)["link"], {"state": "failed", "error": "stopped"})
        self.assertNotIn("link", api_jobs.upload_view({"id": "j2", "received": []}))

    def test_link_error_code_exists(self):
        self.assertEqual(api_errors.ERRORS["link_failed"][0], 400)


class WiringTests(unittest.TestCase):
    def test_routes_adapter_and_guide(self):
        dub = (ROOT / "api_dub.py").read_text(encoding="utf-8")
        for key in ('@api.post("/jobs/from-link")', '@api.post("/jobs/{job_id}/upload/link-retry")',
                    'quote.get("voice_style") or ""', 'body.get("voice_style"))', 'return "link_failed"'):
            self.assertIn(key, dub)
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        for key in ("async def create_link(self, request, params):", "async def link_retry(self, request, job_id):",
                    'delivery_mode=str(delivery_mode or "")'):
            self.assertIn(key, main)
        docs = (ROOT / "api_docs.html").read_text(encoding="utf-8")
        for key in ("POST /v1/jobs/from-link", "voice_style", "<code>link_failed</code>"):
            self.assertIn(key, docs)
        self.assertIn("sentry-sdk>=2.0.0,<3", (ROOT / "requirements.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
