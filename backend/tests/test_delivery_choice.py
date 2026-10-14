"""The customer can choose how the voice speaks (Balanced / Stable / Creative) at the price step of a long dub.

Nothing is sent anywhere: the provider is replaced by a recorder, and the page and server are read as text where a browser or a
payment would be needed."""
import base64
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import inworld_service as iw
import longdub_service as ld

AUDIO = base64.b64encode(b"mp3").decode()
PAGE = (ROOT / "dub_long.html").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
LD = (ROOT / "longdub_service.py").read_text(encoding="utf-8")
EDITS = (ROOT / "longdub_edits.py").read_text(encoding="utf-8")


class Recorder(unittest.TestCase):
    def setUp(self):
        self.bodies = []

        def fake(method, path, key, body=None, timeout=30):
            self.bodies.append(body)
            return {"audioContent": AUDIO}
        for p in (mock.patch.object(iw, "_request", fake), mock.patch.object(iw, "_delivery_notes", set())):
            p.start()
            self.addCleanup(p.stop)

    def speak(self, env_mode=None, **kw):
        env = dict(os.environ)
        env.pop("INWORLD_DELIVERY_MODE", None)
        if env_mode is not None:
            env["INWORLD_DELIVERY_MODE"] = env_mode
        with mock.patch.dict(os.environ, env, clear=True):
            return iw.synthesize("v1", "مرحبا", "key", language="ar", **kw)


class ProviderRequestTests(Recorder):
    def test_without_a_choice_and_without_the_variable_the_request_is_exactly_as_before(self):
        for kw in ({}, {"delivery": None}, {"delivery": ""}, {"delivery": "   "}, {"delivery": "wild"}):
            self.bodies.clear()
            self.speak(**kw)
            self.assertEqual(self.bodies[0], {"text": "مرحبا", "voiceId": "v1", "modelId": "inworld-tts-2", "language": "ar",
                                              "audioConfig": {"audioEncoding": "MP3"}}, kw)

    def test_a_choice_is_sent_in_capitals(self):
        for given, sent in (("stable", "STABLE"), ("Creative", "CREATIVE"), (" BALANCED ", "BALANCED")):
            self.bodies.clear()
            self.speak(delivery=given)
            self.assertEqual(self.bodies[0]["deliveryMode"], sent)

    def test_the_customers_choice_wins_over_the_variable(self):
        self.speak(env_mode="STABLE", delivery="creative")
        self.assertEqual(self.bodies[-1]["deliveryMode"], "CREATIVE")

    def test_no_choice_leaves_the_variable_in_charge(self):
        self.speak(env_mode="STABLE", delivery="")
        self.assertEqual(self.bodies[-1]["deliveryMode"], "STABLE")
        self.speak(env_mode="STABLE")
        self.assertEqual(self.bodies[-1]["deliveryMode"], "STABLE")

    def test_another_voice_model_never_gets_the_field(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            iw.synthesize("v1", "x", "key", language="ar", model_id="inworld-tts-2-flash", delivery="stable")
        self.assertNotIn("deliveryMode", self.bodies[-1])


class LongDubHelperTests(unittest.TestCase):
    def test_clean_delivery_mode_keeps_only_the_three_words(self):
        self.assertEqual(ld.clean_delivery_mode("Stable "), "stable")
        self.assertEqual(ld.clean_delivery_mode("CREATIVE"), "creative")
        self.assertEqual(ld.clean_delivery_mode("balanced"), "balanced")
        for bad in ("", None, "wild", 7, "stable;drop", ["stable"]):
            self.assertEqual(ld.clean_delivery_mode(bad), "", bad)

    def test_the_retry_helper_passes_the_choice_only_when_there_is_one(self):
        seen = []

        def fake(voice, text, key, **kw):
            seen.append(kw)
            return b"audio"
        with mock.patch.object(iw, "synthesize", fake):
            self.assertEqual(ld._tts_with_retry("v", "نص", "auto", 99.0), (b"audio", ""))
            self.assertEqual(ld._tts_with_retry("v", "نص", "auto", 99.0, ""), (b"audio", ""))
            self.assertEqual(ld._tts_with_retry("v", "نص", "auto", 99.0, "stable"), (b"audio", ""))
        self.assertEqual(seen, [{"language": "ar"}, {"language": "ar"}, {"language": "ar", "delivery": "stable"}])


class WiringTests(unittest.TestCase):
    def test_the_server_takes_the_choice_and_defaults_to_none(self):
        self.assertRegex(MAIN, r'class LongDubConfirm\(BaseModel\):[^@]*?delivery_mode: str = ""')
        self.assertIn("body.music_budget, body.delivery_mode)", MAIN)

    def test_confirm_stores_a_cleaned_choice_and_logs_it_only_when_set(self):
        self.assertIn('music_budget=0, delivery_mode=""):', LD)
        self.assertIn('job["delivery_mode"] = clean_delivery_mode(delivery_mode)', LD)
        self.assertIn('f" delivery={job[\'delivery_mode\']}" if job.get("delivery_mode") else ""', LD)

    def test_every_line_the_voice_speaks_uses_the_jobs_choice(self):
        self.assertEqual(LD.count('job.get("delivery_mode"))'), 2)       # the dub itself and the shorter-line retry
        self.assertIn("gap, parent.get('delivery_mode'))", EDITS)             # later corrections follow the original dub
        calls = re.findall(r"_tts_with_retry\(([^\n]*)\)", LD + EDITS)
        self.assertEqual(len([c for c in calls if "delivery_mode" in c]), 3, calls)

    def test_the_api_confirm_is_unchanged(self):
        self.assertIn("LongDubConfirm(expected_due=int(expected_due), room=\"\", tracks=bool(tracks), keep_music=bool(keep_music),", MAIN)
        self.assertNotIn("delivery_mode", re.search(r"async def confirm\(self, request.*?\n\n", MAIN, flags=re.S).group(0))


class PageTests(unittest.TestCase):
    def test_the_choice_is_a_select_next_to_the_room_sound_and_balanced_comes_first_and_means_nothing_chosen(self):
        self.assertIn('<select id="deliveryPref">', PAGE)
        self.assertLess(PAGE.index('id="roomPref"'), PAGE.index('id="deliveryPref"'))
        self.assertIn('[["", T.deliveryBalanced], ["stable", T.deliveryStable], ["creative", T.deliveryCreative]]', PAGE)
        self.assertIn('var deliveryPref = "";', PAGE)

    def test_the_choice_is_sent_with_the_confirmation(self):
        self.assertIn("room: roomPref, delivery_mode: deliveryPref,", PAGE)

    def test_every_value_the_page_can_send_is_one_the_server_keeps(self):
        sent = {v for v in re.findall(r'\["(\w*)", T\.delivery(?:Balanced|Stable|Creative)\]', PAGE)}
        self.assertEqual(sent, {"", "stable", "creative"})
        for v in sent - {""}:
            self.assertEqual(ld.clean_delivery_mode(v), v)

    def test_both_languages_explain_every_option(self):
        keys = ("deliveryLabel", "deliveryBalanced", "deliveryStable", "deliveryCreative",
                "deliveryBalancedNote", "deliveryStableNote", "deliveryCreativeNote", "deliveryNote")
        for k in keys:
            self.assertEqual(len(re.findall(r"^\s+%s: \"" % k, PAGE, flags=re.M)), 2, k)       # one English, one Arabic
        at = PAGE.index('deliveryLabel: "أسلوب')
        ar = PAGE[PAGE.rindex("\n", 0, at):]
        for k in keys:
            line = re.search(r"^\s+%s: \"([^\"]*)\"" % k, ar, flags=re.M).group(1)
            self.assertTrue(re.search("[؀-ۿ]", line), k)

    def test_the_explanation_changes_with_the_choice_and_names_no_company(self):
        self.assertIn('var dd = $("deliveryDesc"); if (dd) dd.textContent = deliveryDescFor(deliveryPref);', PAGE)
        block = "".join(re.findall(r"^\s+delivery\w+: \"[^\"]*\"", PAGE, flags=re.M))
        for word in ("Inworld", "Gemini", "Google", "ElevenLabs", "deliveryMode"):
            self.assertNotIn(word, block)


if __name__ == "__main__":
    unittest.main()
