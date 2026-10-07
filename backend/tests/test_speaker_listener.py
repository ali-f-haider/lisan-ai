"""describe_speaker_voice: one listening answer is validated and never trusted blindly."""
import json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gemini_service as gs


def reply(obj, raw=None):
    text = raw if raw is not None else json.dumps(obj)
    return {"candidates": [{"content": {"parts": [{"text": text}]}}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}


class ListenerTests(unittest.TestCase):
    def setUp(self):
        self.mp3 = Path(tempfile.mkdtemp()) / "a.mp3"; self.mp3.write_bytes(b"x")

    def run_with(self, data):
        sent = {}
        def fake(key, payload, timeout=0):
            sent["payload"] = payload; return data, None
        with patch.object(gs, "call_gemini", fake), patch.object(gs, "record_gemini") as rec:
            try:
                out = gs.describe_speaker_voice("job", self.mp3, "key")
            finally:
                self.recorded = rec.call_count
        return out, sent

    def test_a_clear_answer_is_returned_and_its_cost_is_recorded(self):
        out, sent = self.run_with(reply({"gender": "Male", "age": "senior", "tone": ["Deep", "calm", "made-up"], "uncertain": False}))
        self.assertEqual(out, {"gender": "male", "age": "senior", "tone": ["deep", "calm"], "uncertain": False})
        self.assertEqual(self.recorded, 1)
        self.assertEqual(sent["payload"]["contents"][0]["parts"][0]["inline_data"]["mime_type"], "audio/mpeg")

    def test_fenced_json_is_accepted(self):
        out, _ = self.run_with(reply(None, raw="```json\n" + json.dumps({"gender": "female", "age": "young", "tone": [], "uncertain": False}) + "\n```"))
        self.assertEqual((out["gender"], out["age"], out["uncertain"]), ("female", "young", False))

    def test_an_unknown_word_makes_the_answer_uncertain_instead_of_guessing(self):
        out, _ = self.run_with(reply({"gender": "male", "age": "ancient", "tone": [], "uncertain": False}))
        self.assertTrue(out["uncertain"]); self.assertEqual(out["age"], "")
        out, _ = self.run_with(reply({"gender": "", "age": "adult", "tone": [], "uncertain": False}))
        self.assertTrue(out["uncertain"])

    def test_no_answer_or_no_key_raises(self):
        with patch.object(gs, "call_gemini", lambda *a, **k: (None, "down")), patch.object(gs, "record_gemini"):
            with self.assertRaises(ValueError):
                gs.describe_speaker_voice("job", self.mp3, "key")
        with self.assertRaises(ValueError):
            gs.describe_speaker_voice("job", self.mp3, "")

    def test_the_prompt_never_names_a_vendor(self):
        _, sent = self.run_with(reply({"gender": "male", "age": "adult", "tone": [], "uncertain": False}))
        text = sent["payload"]["contents"][0]["parts"][1]["text"].lower()
        self.assertNotIn("gemini", text)


class ListeningIsChargedTests(unittest.TestCase):
    """Everything the listener costs lands in the usage bucket that the Generate price settles."""
    def test_the_listening_cost_is_added_to_the_generate_price_once(self):
        import app_state, shortdub_billing
        job = "job-" + __import__("uuid").uuid4().hex
        data = reply({"gender": "male", "age": "senior", "tone": [], "uncertain": False})
        data["usageMetadata"] = {"promptTokenCount": 40000, "candidatesTokenCount": 100000, "thoughtsTokenCount": 20000}
        mp3 = Path(tempfile.mkdtemp()) / "a.mp3"; mp3.write_bytes(b"x")
        before = shortdub_billing.studio_quote({}, app_state.usage_bucket(job), {})
        self.assertEqual(before["analysis_credits"], 0)
        with patch.object(gs, "call_gemini", lambda *a, **k: (data, None)):
            gs.describe_speaker_voice(job, mp3, "key")
        bucket = app_state.usage_bucket(job)
        self.assertEqual((bucket["gemini_in"], bucket["gemini_out"], bucket["gemini_thoughts"]), (40000, 100000, 20000))
        quote = shortdub_billing.studio_quote({}, bucket, {})
        self.assertGreater(quote["analysis_credits"], 0)                       # charged ...
        self.assertEqual(quote["credits"], quote["analysis_credits"])
        bucket["shortdub_ai_settled"] = quote["ai_snapshot"]                   # ... and once Generate has settled it ...
        self.assertEqual(shortdub_billing.studio_quote({}, bucket, {})["analysis_credits"], 0)    # ... never charged again
        with patch.object(gs, "call_gemini", lambda *a, **k: (data, None)):    # a second Auto-Assign is charged again
            gs.describe_speaker_voice(job, mp3, "key")
        self.assertGreater(shortdub_billing.studio_quote({}, bucket, {})["analysis_credits"], 0)


if __name__ == "__main__":
    unittest.main()
