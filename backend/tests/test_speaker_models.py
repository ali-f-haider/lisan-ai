"""The speaker checks (and only they) ask Google's strongest model first, fall back to the usual models, and are priced at its rate."""
import datetime
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importlib.util
_spec = importlib.util.spec_from_file_location("config", Path(__file__).resolve().parents[1] / "config.py")
config = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(config)      # the real settings (another test swaps in a stand-in)
import gemini_service
import resource_meter
import speaker_vote_live as svl


class Models(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(sys.modules, {"config": config})
        p.start(); self.addCleanup(p.stop)

    def test_the_strong_model_is_tried_first_then_the_usual_ones(self):
        m = svl.speaker_models()
        self.assertEqual(m[0], "gemini-4-argon")
        self.assertEqual(m[1:], [x for x in config.GEMINI_MODELS if x != "gemini-4-argon"])

    def test_the_speaker_requests_pass_that_list(self):
        seen = {}

        def fake(api_key, payload, timeout=120, models=None):
            seen["models"] = models
            return {"candidates": []}, None
        with mock.patch.object(gemini_service, "call_gemini", fake):
            svl._default_ask("k", {"contents": []}, 10)
        self.assertEqual(seen["models"][0], "gemini-4-argon")

    def test_other_requests_keep_the_usual_models(self):
        tried = []

        def fake_open(req, timeout=0):
            tried.append(req.full_url.split("/models/")[1].split(":")[0])
            raise OSError("offline")
        with mock.patch.object(gemini_service.urllib.request, "urlopen", fake_open), mock.patch.object(gemini_service.time, "sleep", lambda s: None):
            gemini_service.call_gemini("k", {"contents": []}, timeout=1, models=None)
        self.assertNotIn("gemini-4-argon", tried)
        self.assertEqual(sorted(set(tried)), sorted(set(config.GEMINI_MODELS)))

    def test_switch_off(self):
        with mock.patch.object(config, "SPEAKER_MODELS", []):
            self.assertEqual(svl.speaker_models(), list(dict.fromkeys(config.GEMINI_MODELS)))

    def test_the_model_that_answered_is_recorded(self):
        u = {"usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0}
        rows = [{"segment_id": f"seg_{i}", "start": i, "end": i + 1, "text": "x"} for i in range(3)]
        answer = '{"people": 1, "lines": [{"id": "seg_0", "speaker": "A"}, {"id": "seg_1", "speaker": "A"}, {"id": "seg_2", "speaker": "A"}]}'
        data = {"modelVersion": "gemini-4-argon", "candidates": [{"content": {"parts": [{"text": answer}]}}], "usageMetadata": {}}
        got = svl._one_voter("dialogue", "j", rows, None, "k", lambda k, p, t: (data, None), u)
        self.assertEqual(got, {"seg_0": "A", "seg_1": "A", "seg_2": "A"})
        self.assertEqual(u["model"], "gemini-4-argon")


class Price(unittest.TestCase):
    def test_argon_price_and_the_december_rise(self):
        self.assertEqual(resource_meter._argon_rates(datetime.date(2026, 11, 30)), (2.0, 10.0))
        self.assertEqual(resource_meter._argon_rates(datetime.date(2026, 12, 1)), (4.0, 20.0))
        d = {"modelVersion": "gemini-4-argon", "usageMetadata": {"promptTokenCount": 1_000_000, "candidatesTokenCount": 0, "thoughtsTokenCount": 0}}
        with mock.patch.object(resource_meter, "_argon_rates", lambda today=None: (2.0, 10.0)):
            self.assertAlmostEqual(resource_meter.gemini_usd(d), 2.0)

    def test_other_models_unchanged(self):
        d = {"modelVersion": "gemini-2.5-flash", "usageMetadata": {"promptTokenCount": 1_000_000}}
        self.assertAlmostEqual(resource_meter.gemini_usd(d), resource_meter.GEMINI_IN_USD_PER_M)


if __name__ == "__main__":
    unittest.main()
