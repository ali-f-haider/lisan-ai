"""Empty audio from the voice (9 Nov 2026: 17 of 61 lines of one text, the same lines on every try). Cause found on 10 Nov 2026: the
voice reads square brackets as an instruction, so a line written entirely in brackets says nothing. The person's brackets are now
sent as round ones, two empty answers stop the waiting, cleaned copies are tried once each, and the admin sees what failed without
the words of the line."""
import json
import time
import unittest
from unittest import mock

import inworld_service as iw
import longdub_service as ld
from test_text_voice import Base, UID, mp3
import text_voice as tv


class Brackets(unittest.TestCase):
    def test_the_persons_brackets_become_round_and_the_instruction_stays(self):
        self.assertEqual(ld._protect_brackets("[sound serious] [قل هو الله أحد]"), "[sound serious] (قل هو الله أحد)")
        self.assertEqual(ld._protect_brackets("[قل هو الله أحد]"), "(قل هو الله أحد)")
        self.assertEqual(ld._protect_brackets("مرحبا"), "مرحبا")

    def test_the_shape_has_no_words(self):
        shape = ld.text_shape("[sound sad] قُلْ هُوَ ۚ 12")
        self.assertEqual((shape["tag"], shape["quran_marks"], shape["digits"]), (True, 1, 2))
        self.assertNotIn("قل", json.dumps(shape, ensure_ascii=False))


class Retry(unittest.TestCase):
    def run_it(self, answer):
        sent, waits = [], []

        def synth(voice, text, key, language="ar", **kw):
            sent.append(text)
            return answer(text)
        with mock.patch.object(iw, "synthesize", synth), mock.patch.object(ld.time, "sleep", lambda s: waits.append(s)):
            audio, err = ld._tts_with_retry("v", "[sound sad] قُلْ ۚ هُوَ")
        return audio, err, sent, waits, ld.TTS_NOTE.info

    def test_two_empty_answers_stop_the_waiting_and_a_cleaned_copy_is_spoken(self):
        def answer(text):
            if "ۚ" in text:
                raise iw.EmptyAudio("We couldn't generate this line. Please try again.")
            return b"mp3"
        audio, err, sent, waits, info = self.run_it(answer)
        self.assertEqual(audio, b"mp3")
        self.assertEqual(waits, [4])                       # not 4 + 12 + 30 seconds
        self.assertEqual(info["fallback"], "cleaned")
        self.assertNotIn("ۚ", sent[-1])

    def test_nothing_helps_then_the_line_fails_with_the_attempts_noted(self):
        def answer(text):
            raise iw.EmptyAudio("We couldn't generate this line. Please try again.")
        audio, err, sent, waits, info = self.run_it(answer)
        self.assertIsNone(audio)
        self.assertEqual(info["empty"], 2)
        self.assertEqual(info["tried"], ["without the instruction", "cleaned", "without vowel marks"])
        self.assertEqual(len(sent), 5)

    def test_other_errors_keep_the_old_waits(self):
        def answer(text):
            raise RuntimeError("timeout")
        audio, err, sent, waits, info = self.run_it(answer)
        self.assertEqual((audio, waits, info["tried"]), (None, [4, 12, 30], []))


class AdminRecord(Base):
    def ready(self):
        job, _ = tv.new_job(UID, "One.\nTwo.\nThree.", "en")
        job["prepared"] = True
        for ln in job["lines"]:
            ln["arabic"], ln["emotion"] = "مَرْحَبًا " + ln["id"], "neutral"
        tv._save(job)
        tv.set_speakers(job, [{"id": "s1", "voice_id": "v1"}])
        return tv.load(job["id"])

    def speak(self, job, tts):
        with mock.patch("longdub_service._tts_with_retry", tts):
            ok, err = tv.start_speak(tv.load(job["id"]))
            self.assertTrue(ok, err)
            return self.wait(job["id"])

    def test_a_failure_is_documented_without_the_words_and_solved_by_a_later_run(self):
        job = self.ready()
        tone = mp3()

        def bad(voice, text, waqf="auto", gap=None, delivery=None):
            if "l2" in text:
                ld.TTS_NOTE.info = {"tries": 5, "empty": 2, "fallback": "", "tried": ["cleaned"]}
                return None, "We couldn't generate this line. Please try again."
            return tone, ""
        job = self.speak(job, bad)
        lf = job["last_failure"]
        self.assertEqual((lf["lines"], lf["of"]), (1, 3))
        self.assertGreaterEqual(lf["refund"], 1)
        self.assertEqual(lf["lines_info"][0]["id"], "l2")
        self.assertEqual(lf["lines_info"][0]["tries"], 5)
        self.assertNotIn("مَرْحَبًا", json.dumps(lf, ensure_ascii=False))
        self.assertEqual(tv.admin_summary()[UID]["failures"], 1)
        job = self.speak(job, lambda voice, text, waqf="auto", gap=None, delivery=None: (tone, ""))
        self.assertTrue(job["last_failure"].get("fixed_at"))
        self.assertEqual(tv.admin_summary()[UID]["failures"], 0)


if __name__ == "__main__":
    unittest.main()
