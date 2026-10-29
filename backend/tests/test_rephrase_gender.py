"""Offline regressions: gender context must survive automatic timing rewrites."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arabic_rewrite_guard as guard
import gemini_service as gs
import longdub_service as ld
from arabic_waqf import pausal


class RewriteGuardTests(unittest.TestCase):
    def test_female_address_cannot_become_male_title(self):
        self.assertEqual(guard.conflict("شُكْرًا لَكِ، سِيَادَةَ الْقَاضِيَةِ.", "شُكْرًا سَيِّدِي."), "address gender changed")

    def test_female_second_person_cannot_become_male(self):
        self.assertTrue(guard.conflict("سِيَادَتُكِ نَظَرَتْ فِي الْأَمْرِ.", "أَنْتَ نَظَرْتَ فِيهِ."))

    def test_known_addressee_protects_new_address_in_neutral_original(self):
        self.assertTrue(guard.conflict("نُوقِشَ الْأَمْرُ.", "أَنَّكَ نَظَرْتَ فِيهِ.", "male", "female"))

    def test_male_address_cannot_become_female(self):
        self.assertTrue(guard.conflict("شُكْرًا لَكَ.", "شُكْرًا لَكِ."))

    def test_female_speaker_cannot_get_masculine_self_description(self):
        self.assertEqual(guard.conflict("دَعْنِي أُوَضِّحْ.", "لِأَكُونَ وَاضِحًا.", "female"), "speaker gender changed")

    def test_male_speaker_cannot_get_feminine_self_description(self):
        self.assertTrue(guard.conflict("سَأَحْضُرُ.", "أَنَا مُسْتَعِدَّةٌ.", "male"))

    def test_speaker_gender_is_not_addressee_gender(self):
        self.assertEqual(guard.conflict("شُكْرًا لَكِ.", "لَكِ شُكْرِي.", "male", "female"), "")

    def test_female_addressee_does_not_feminize_other_male_people(self):
        self.assertEqual(guard.conflict("شُكْرًا لَكِ، سَيَحْضُرُ الطَّبِيبُ.", "لَكِ شُكْرِي، الطَّبِيبُ قَادِمٌ.", "male", "female"), "")

    def test_unknown_and_mixed_addressees_are_not_guessed(self):
        self.assertEqual(guard.conflict("سَأَحْضُرُ.", "شُكْرًا لَكَ."), "")
        self.assertEqual(guard.conflict("لَكِ وَلَكَ شُكْرِي.", "شُكْرًا لَكَ.", None, "female"), "")

    def test_arabic_text_has_priority_over_guessed_addressee(self):
        self.assertEqual(guard.conflict("شُكْرًا لَكِ.", "لَكِ شُكْرِي.", "male", "male"), "")

    def test_root_kaf_is_not_a_pronoun_and_nouns_are_not_self_adjectives(self):
        self.assertEqual(guard.conflict("لَكِ شُكْرِي.", "أُرِيدُ شُبَّاكَ الْغُرْفَةِ."), "")
        self.assertEqual(guard.conflict("سَأُوَضِّحُ.", "أَنَا شَخْصٌ وَاضِحٌ.", "female"), "")


class ShorteningPromptTests(unittest.TestCase):
    def answer(self, text):
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({"arabic_text": text})}]}}]}

    def test_gender_and_reviewed_reference_reach_existing_shortening_call(self):
        context = {"reviewed_arabic_text": "شُكْرًا لَكِ.", "lines": {"s1": {"speaker_gender": "male", "addressee_is": "female"}}}
        with mock.patch.object(gs, "call_gemini", return_value=(self.answer("لَكِ شُكْرِي."), "")) as call, mock.patch.object(gs, "record_gemini"):
            self.assertEqual(gs.shorten_arabic_line("job", "Thank you, madam.", "شُكْرًا لَكِ.", 12, "test-key", gender=context), "لَكِ شُكْرِي.")
        prompt = call.call_args.args[1]["contents"][0]["parts"][0]["text"]
        self.assertIn(json.dumps(context, ensure_ascii=False), prompt)
        self.assertIn("Speaker gender and addressee gender are DIFFERENT", prompt)
        self.assertIn("Neighbou", prompt)
        self.assertEqual(call.call_count, 1)

    def test_no_context_keeps_the_existing_english_prompt(self):
        with mock.patch.object(gs, "call_gemini", return_value=(self.answer("نَصٌّ."), "")) as call, mock.patch.object(gs, "record_gemini"):
            gs.shorten_arabic_line("job", "Text.", "نَصٌّ طَوِيلٌ.", 8, "test-key")
        prompt = call.call_args.args[1]["contents"][0]["parts"][0]["text"]
        self.assertNotIn("GENDER AND REFERENCE", prompt)
        self.assertTrue(prompt.endswith('Arabic line to shorten: "نَصٌّ طَوِيلٌ."\n'))


class TimingRewriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "lines").mkdir()
        (self.d / "fit").mkdir()
        self.row = {"segment_id": "s1", "speaker": "Presenter", "speaker_id": "p1", "start": 10,
                    "end": 13, "gender": "male", "waqf": "stop", "text": "Thank you for your detailed explanation, Your Honor.",
                    "arabic_text": "شُكْرًا لَكِ عَلَى هَذَا التَّوْضِيحِ الْمُفَصَّلِ، سِيَادَةَ الْقَاضِيَةِ."}
        self.next_row = {"segment_id": "s2", "speaker": "Judge", "start": 14, "end": 16, "text": "You are welcome.", "arabic_text": "عَفْوًا."}
        self.rows = [self.row, self.next_row]
        self.job = {"id": "test", "speaker_list": [
            {"name": "Presenter", "gender": "male", "gender_source": "chosen"},
            {"name": "Judge", "gender": "female", "gender_source": "chosen", "role": "judge"}]}
        self.meta = {"dur": 6, "raw": 6, "tempo": 1, "start": 10}

    def run_rewrite(self, answer, row=None, after_tashkeel=None):
        def fit(raw, out, *args):
            out.write_bytes(b"fitted")
            return {"dur": 2, "raw": 2, "tempo": 1}
        with mock.patch.object(gs, "shorten_arabic_line", return_value=answer) as shorten, \
             mock.patch.object(gs, "check_shortened_arabic", return_value={"same": True, "lost": ""}), \
             mock.patch.object(ld, "_tts_with_retry", return_value=(b"audio", "")) as speak, \
             mock.patch.object(ld, "_fit_line", side_effect=fit), \
             mock.patch.object(ld, "_ev") as event, \
             mock.patch.object(ld, "needs_tashkeel", return_value=after_tashkeel is not None), \
             mock.patch.object(gs, "add_tashkeel_lines", return_value={"s1": after_tashkeel}), \
             mock.patch.object(ld, "merge_tashkeel", return_value=after_tashkeel):
            result = ld._rephrase_to_fit(self.job, row or self.row, "[calm] ", self.meta, 3, 3, "voice", self.d / "ref.wav", self.d,
                                        gap=1, rows_all=self.rows)
        return result, shorten, speak, event

    def test_shortening_receives_authoritative_speaker_and_addressee_context(self):
        result, shorten, speak, event = self.run_rewrite("شُكْرًا لَكِ.")
        context = shorten.call_args.kwargs["gender"]
        self.assertEqual(context["lines"]["s1"]["speaker_gender"], "male")
        self.assertEqual(context["lines"]["s1"]["addressee_is"], "female")
        self.assertEqual(context["reviewed_arabic_text"], self.row["arabic_text"])
        self.assertEqual(result[1], "شُكْرًا لَكِ.")

    def test_wrong_gender_is_not_sent_to_speech_or_accepted(self):
        before = dict(self.row)
        result, shorten, speak, event = self.run_rewrite("شُكْرًا سَيِّدِي.")
        self.assertIsNone(result[1])
        self.assertIs(result[0], self.meta)
        speak.assert_not_called()
        self.assertEqual(self.row, before)
        self.assertIn("address gender changed", event.call_args.args[3])

    def test_tashkeel_cannot_reverse_gender_after_shortening_check(self):
        result, shorten, speak, event = self.run_rewrite("شكرا لك.", after_tashkeel="شُكْرًا لَكَ.")
        self.assertIsNone(result[1])
        speak.assert_not_called()

    def test_second_rewrite_uses_original_reviewed_wording(self):
        row = dict(self.row, arabic_text="شُكْرًا لَكِ عَلَى الْبَيَانِ الْمُفَصَّلِ الْجَيِّدِ.")
        result, shorten, speak, event = self.run_rewrite("شُكْرًا لَكِ.", row=row)
        self.assertEqual(shorten.call_args.kwargs["gender"]["reviewed_arabic_text"], self.row["arabic_text"])
        self.assertEqual(result[0]["voice_text"], pausal("[calm] شُكْرًا لَكِ.", "stop", 1))

    def test_selected_female_speaker_overrides_legacy_row_default(self):
        self.job["speaker_list"][0]["gender"] = "female"
        self.row.update(text="Let me explain this clearly in more detail.", arabic_text="دَعْنِي أُوَضِّحْ هَذِهِ النُّقْطَةَ بِمَزِيدٍ مِنَ التَّفْصِيلِ.")
        result, shorten, speak, event = self.run_rewrite("لِأَكُونَ وَاضِحًا.")
        self.assertEqual(shorten.call_args.kwargs["gender"]["lines"]["s1"]["speaker_gender"], "female")
        self.assertIsNone(result[1])
        speak.assert_not_called()

    def test_saved_addressee_is_passed_without_promoting_neighbour_guess(self):
        self.row["text"] = "This was considered carefully and fairly."
        self.row["arabic_text"] = "نُوقِشَ هَذَا الْأَمْرُ بِعِنَايَةٍ شَدِيدَةٍ وَبِإِنْصَافٍ تَامٍّ."
        self.row["addressee"] = "female"
        result, shorten, speak, event = self.run_rewrite("أَنَّكَ نَظَرْتَ فِيهِ.")
        line = shorten.call_args.kwargs["gender"]["lines"]["s1"]
        self.assertEqual(line["reviewed_addressee"], "female")
        self.assertNotIn("addressee_is", line)
        self.assertIsNone(result[1])
        speak.assert_not_called()

    def test_non_english_source_retains_source_language_and_gender(self):
        self.job["source_language"] = "fr"
        result, shorten, speak, event = self.run_rewrite("شُكْرًا لَكِ.")
        self.assertEqual(shorten.call_args.kwargs["source_language"], "fr")
        self.assertIn("gender", shorten.call_args.kwargs)

    def test_voice_text_matches_exact_provider_request(self):
        import inworld_service
        with mock.patch.object(inworld_service, "synthesize", return_value=b"audio") as send:
            ld._tts_with_retry("voice", "[calm] شُكْرًا لَكِ.", "stop", 1)
        self.assertEqual(send.call_args.args[1], ld._voice_text("[calm] شُكْرًا لَكِ.", "stop", 1))


if __name__ == "__main__":
    unittest.main()
