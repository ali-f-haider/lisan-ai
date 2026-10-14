"""Inworld's deliveryMode can be switched on with the INWORLD_DELIVERY_MODE variable; left empty, nothing changes.

Nothing is sent anywhere: the provider is replaced by a recorder."""
import base64
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import inworld_service as iw

AUDIO = base64.b64encode(b"mp3").decode()


class Base(unittest.TestCase):
    def setUp(self):
        self.bodies = []

        def fake(method, path, key, body=None, timeout=30):
            self.bodies.append((path, body))
            return {"audioContent": AUDIO}
        for p in (mock.patch.object(iw, "_request", fake), mock.patch.object(iw, "_delivery_notes", set(), create=True)):
            p.start()
            self.addCleanup(p.stop)

    def speak(self, env, **kw):
        with mock.patch.dict(os.environ, env, clear=False):
            if "INWORLD_DELIVERY_MODE" not in env:
                os.environ.pop("INWORLD_DELIVERY_MODE", None)
            return iw.synthesize("voice1", "مرحبا", "key", language="ar", **kw)


class UnsetTests(Base):
    def test_nothing_is_added_when_the_variable_is_not_set(self):
        self.assertEqual(self.speak({}), b"mp3")
        path, body = self.bodies[0]
        self.assertEqual(path, "/tts/v1/voice")
        self.assertEqual(body, {"text": "مرحبا", "voiceId": "voice1", "modelId": "inworld-tts-2", "language": "ar",
                                "audioConfig": {"audioEncoding": "MP3"}})        # exactly the request as it was before

    def test_an_empty_or_blank_value_sends_nothing(self):
        for v in ("", "   "):
            self.speak({"INWORLD_DELIVERY_MODE": v})
            self.assertNotIn("deliveryMode", self.bodies[-1][1])

    def test_a_value_that_is_not_a_mode_is_ignored_and_said_once(self):
        with mock.patch("builtins.print") as said:
            self.speak({"INWORLD_DELIVERY_MODE": "wild"})
            self.speak({"INWORLD_DELIVERY_MODE": "wild"})
        self.assertTrue(all("deliveryMode" not in b for _, b in self.bodies))
        warnings = [c.args[0] for c in said.call_args_list if "ignored" in c.args[0]]
        self.assertEqual(len(warnings), 1)
        self.assertIn("STABLE, BALANCED, CREATIVE", warnings[0])


class SetTests(Base):
    def test_each_mode_is_sent_and_nothing_else_changes(self):
        for mode in ("STABLE", "BALANCED", "CREATIVE"):
            self.speak({"INWORLD_DELIVERY_MODE": mode})
            body = self.bodies[-1][1]
            self.assertEqual(body.pop("deliveryMode"), mode)
            self.assertEqual(body, {"text": "مرحبا", "voiceId": "voice1", "modelId": "inworld-tts-2", "language": "ar",
                                    "audioConfig": {"audioEncoding": "MP3"}})

    def test_capitals_and_spaces_do_not_matter(self):
        for raw, mode in (("stable", "STABLE"), ("  Creative ", "CREATIVE"), ("balanced", "BALANCED")):
            self.speak({"INWORLD_DELIVERY_MODE": raw})
            self.assertEqual(self.bodies[-1][1]["deliveryMode"], mode)

    def test_it_is_not_sent_to_a_model_that_does_not_take_it(self):
        self.speak({"INWORLD_DELIVERY_MODE": "STABLE"}, model_id="inworld-tts-2-flash")
        self.assertNotIn("deliveryMode", self.bodies[-1][1])
        self.assertEqual(self.bodies[-1][1]["modelId"], "inworld-tts-2-flash")

    def test_the_value_is_read_for_each_request(self):
        self.speak({"INWORLD_DELIVERY_MODE": "STABLE"})
        self.speak({})
        self.speak({"INWORLD_DELIVERY_MODE": "CREATIVE"})
        self.assertEqual([b.get("deliveryMode") for _, b in self.bodies], ["STABLE", None, "CREATIVE"])

    def test_it_is_announced_once_not_for_every_line(self):
        with mock.patch("builtins.print") as said:
            for _ in range(5):
                self.speak({"INWORLD_DELIVERY_MODE": "STABLE"})
        self.assertEqual(len([c for c in said.call_args_list if "deliveryMode STABLE is on" in c.args[0]]), 1)


class HelperTests(unittest.TestCase):
    def test_the_three_modes_are_the_ones_inworld_documents(self):
        self.assertEqual(iw.DELIVERY_MODES, ("STABLE", "BALANCED", "CREATIVE"))

    def test_only_one_place_builds_the_speech_request(self):
        text = (ROOT / "inworld_service.py").read_text(encoding="utf-8")
        self.assertEqual(text.count('"/tts/v1/voice"'), 1)


if __name__ == "__main__":
    unittest.main()
