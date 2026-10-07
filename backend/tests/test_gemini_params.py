"""Gemini requests carry no sampling parameters (temperature, topP, topK) and never a thinking budget: the thinking is set by level."""
import io
import json
import re
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gemini_service as gs

BACKEND = Path(__file__).resolve().parents[1]


class PayloadTests(unittest.TestCase):
    def test_sampling_parameters_are_removed_and_the_original_is_not_changed(self):
        p = {"contents": [], "generationConfig": {"temperature": 0.3, "topP": 0.9, "topK": 40, "top_p": 1, "maxOutputTokens": 100}}
        out = gs._payload_for(p, "gemini-2.5-flash")
        self.assertEqual(out["generationConfig"], {"maxOutputTokens": 100})
        self.assertIn("temperature", p["generationConfig"])

    def test_a_thinking_budget_becomes_a_level(self):
        mk = lambda b: {"generationConfig": {"thinkingConfig": {"thinkingBudget": b}}}
        self.assertEqual(gs._payload_for(mk(0), "gemini-2.5-flash")["generationConfig"]["thinkingConfig"], {"thinkingLevel": "low"})
        self.assertEqual(gs._payload_for(mk(4096), "gemini-flash-latest")["generationConfig"]["thinkingConfig"], {"thinkingLevel": "medium"})
        self.assertEqual(gs._payload_for(mk(20000), "gemini-flash-latest")["generationConfig"]["thinkingConfig"], {"thinkingLevel": "high"})
        self.assertNotIn("thinkingConfig", gs._payload_for(mk(-1), "gemini-2.5-flash")["generationConfig"])      # dynamic = the model's default

    def test_a_level_is_kept_and_a_model_that_does_not_think_gets_none(self):
        p = {"generationConfig": {"thinkingConfig": {"thinkingLevel": "high"}}}
        self.assertEqual(gs._payload_for(p, "gemini-3.8-flash")["generationConfig"]["thinkingConfig"], {"thinkingLevel": "high"})
        self.assertNotIn("thinkingConfig", gs._payload_for(p, "gemini-2.0-flash")["generationConfig"])
        bad = {"generationConfig": {"thinkingConfig": {"thinkingLevel": "extreme"}}}
        self.assertNotIn("thinkingConfig", gs._payload_for(bad, "gemini-2.5-flash")["generationConfig"])

    def test_a_payload_without_generation_config_is_returned_as_it_is(self):
        p = {"contents": [1]}
        self.assertEqual(gs._payload_for(p, "gemini-2.5-flash"), p)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CallTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.real = (gs.urllib.request.urlopen, gs.time.sleep, gs.GEMINI_MODELS)
        gs.time.sleep = lambda s: None
        gs.GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash"]

    def tearDown(self):
        gs.urllib.request.urlopen, gs.time.sleep, gs.GEMINI_MODELS = self.real

    def test_what_is_sent_has_no_sampling_and_no_budget(self):
        def fake(req, timeout=0):
            self.sent.append((req.full_url, json.loads(req.data)))
            return FakeResponse(b'{"ok": 1}')
        gs.urllib.request.urlopen = fake
        data, err = gs.call_gemini("k", {"generationConfig": {"temperature": 0.2, "topK": 3, "thinkingConfig": {"thinkingBudget": 0}}})
        self.assertEqual((data, err), ({"ok": 1}, None))
        cfg = self.sent[0][1]["generationConfig"]
        self.assertEqual(cfg, {"thinkingConfig": {"thinkingLevel": "low"}})

    def test_a_refused_thinking_level_is_retried_without_it(self):
        def fake(req, timeout=0):
            body = json.loads(req.data)
            self.sent.append(body)
            if "thinkingConfig" in body["generationConfig"]:
                raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b'{"error":{"message":"thinking level is not supported"}}'))
            return FakeResponse(b'{"ok": 2}')
        gs.urllib.request.urlopen = fake
        data, err = gs.call_gemini("k", {"generationConfig": {"maxOutputTokens": 5, "thinkingConfig": {"thinkingLevel": "low"}}})
        self.assertEqual(data, {"ok": 2})
        self.assertEqual(len(self.sent), 2)
        self.assertNotIn("thinkingConfig", self.sent[1]["generationConfig"])


class NoDeprecatedParameterIsLeftInTheCode(unittest.TestCase):
    def test_no_module_and_no_page_sets_them(self):
        pat = re.compile(r"""["']?(temperature|thinkingBudget|thinking_budget|topP|topK|top_p|top_k)["']?\s*[:=]""")
        skip = ("test_", "patch_", "fix_", "add_", "apply_", "check_")
        files = [f for f in BACKEND.glob("*.py") if not f.name.startswith(skip) and f.name != "gemini_service.py"] + list(BACKEND.glob("*.js"))
        bad = []
        for f in files:
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if pat.search(line) and "ffmpeg" not in line.lower():
                    bad.append(f"{f.name}:{n}: {line.strip()[:100]}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
