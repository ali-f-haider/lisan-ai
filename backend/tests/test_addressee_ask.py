"""Who each "you" is said to, read from the conversation before the translation (Air Force One clip, October 2026: the
hijacker's "Don't move" / "Give me the gun" and the President's "Listen to me" were dubbed in the feminine because the old hint
was "said to the speaker of the line before", a woman on the phone line). Fake network only; nothing calls a service."""
import json
import unittest
from unittest import mock

import addressee_ask as aa
import gemini_service
import gender_context
import language_prompts
import longdub_service as ld
from models import Segment

# a short version of the scene: the switchboard operator (a woman) on the phone, the hijacker finds the President
ROWS = [dict(segment_id=f"seg_{i}", start=float(t), end=float(t) + 1.5, speaker=s, speaker_id=sid, text=x)
        for i, (t, s, sid, x) in enumerate([
            (86, "Speaker 4", "sp4", "Okay, sir. You want to make a federal case out of this, fine by me!"),
            (88, "Speaker 1", "sp1", "Don't move."),
            (89, "Speaker 4", "sp4", "I traced the call, sir."),
            (92, "Speaker 1", "sp1", "Give me the gun."),
            (93, "Speaker 4", "sp4", "Are you there?"),
            (114, "Speaker 2", "sp2", "Listen to me."),
            (193, "Speaker 7", "sp7", "Boys, give me room."),
            (301, "Speaker 1", "sp1", "Be brave."),
            (302, "Speaker 2", "sp2", "This is the president."),
            (303, "Speaker 2", "sp2", "Nobody can tell you what to do in life."),
        ])]
GENDERS = {"Speaker 1": {"gender": "male"}, "Speaker 2": {"gender": "male"}, "Speaker 4": {"gender": "female"},
           "Speaker 7": {"gender": "male"}}
ANSWERS = [{"id": "seg_1", "said_to": "Speaker 2", "reason": "the hijacker finds the President"},
           {"id": "seg_3", "said_to": "Speaker 2", "reason": "at gunpoint"},
           {"id": "seg_4", "said_to": "Speaker 2", "reason": "on the phone"},
           {"id": "seg_5", "said_to": "Speaker 1", "reason": "to the man holding him"},
           {"id": "seg_6", "said_to": "group", "group_gender": "male", "reason": "Boys"},
           {"id": "seg_7", "said_to": "unsure", "reason": "the hostage never speaks"},
           {"id": "seg_9", "said_to": "nobody", "reason": "a saying"}]


def reply(lines):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps({"lines": lines})}]}}], "usageMetadata": {"promptTokenCount": 10}}


class WhichLines(unittest.TestCase):
    def test_lines_with_you_or_a_command_are_asked_about(self):
        for t in ("Don't move.", "Give me the gun.", "Shut up!", "Boys, give me room.", "Okay. Listen.", "Do it!",
                  "Are you there?", "Take me off the speakerphone.", "Please wait here."):
            self.assertTrue(aa.needs_question(t), t)

    def test_other_lines_are_not(self):
        for t in ("This is the president.", "Fox Three.", "Let's pray it works.", "It won't end there.", "I traced the call.", ""):
            self.assertFalse(aa.needs_question(t), t)


class TheQuestion(unittest.TestCase):
    def ask(self, lines, targets=("seg_1", "seg_3", "seg_4", "seg_5", "seg_6", "seg_7", "seg_9")):
        calls, recorded = [], []
        def call(key, payload, timeout):
            calls.append(payload["contents"][0]["parts"][0]["text"])
            return reply(lines), None
        got = aa.ask(ROWS, list(targets), "k", call, record=recorded.append)
        return got, calls, recorded

    def test_each_answer_is_kept_with_the_english_it_was_about(self):
        got, calls, recorded = self.ask(ANSWERS)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(recorded), 1)                       # its price goes onto the job
        self.assertEqual((got["seg_1"]["kind"], got["seg_1"]["said_to"], got["seg_1"]["said_to_id"]), ("speaker", "Speaker 2", "sp2"))
        self.assertEqual(got["seg_1"]["text"], "Don't move.")
        self.assertEqual((got["seg_6"]["kind"], got["seg_6"]["group_of"]), ("group", "men"))
        self.assertEqual(got["seg_7"]["kind"], "unsure")
        self.assertEqual(got["seg_9"]["kind"], "nobody")

    def test_the_whole_conversation_and_only_the_asked_ids_are_sent(self):
        _, calls, _ = self.ask(ANSWERS)
        p = calls[0]
        self.assertIn("seg_1 [88 s] Speaker 1: Don't move.", p)
        self.assertIn("seg_8 [302 s] Speaker 2: This is the president.", p)        # not asked, but part of the conversation
        self.assertTrue(p.rstrip().endswith("QUESTION (line ids): seg_1, seg_3, seg_4, seg_5, seg_6, seg_7, seg_9"))
        self.assertIn("Never guess a gender", p)

    def test_said_to_himself_an_unknown_name_or_a_line_not_asked_is_not_taken(self):
        got, _, _ = self.ask([{"id": "seg_1", "said_to": "Speaker 1"}, {"id": "seg_3", "said_to": "the pilot"},
                              {"id": "seg_8", "said_to": "Speaker 1"}, "junk"])
        self.assertEqual((got["seg_1"]["kind"], got["seg_3"]["kind"]), ("unsure", "unsure"))
        self.assertNotIn("seg_8", got)

    def test_a_failed_or_broken_answer_gives_nothing_and_never_raises(self):
        self.assertEqual(aa.ask(ROWS, ["seg_1"], "k", lambda *a: (None, "down")), {})
        self.assertEqual(aa.ask(ROWS, ["seg_1"], "k", lambda *a: ({"candidates": [{"content": {"parts": [{"text": "no json"}]}}]}, None)), {})
        self.assertEqual(aa.ask(ROWS, ["seg_1"], "k", lambda *a: 1 / 0), {})

    def test_a_long_video_is_asked_in_parts_each_with_the_lines_around_it(self):
        rows = [dict(segment_id=f"s{i}", start=float(i), speaker="A" if i % 2 else "B", speaker_id=None, text=f"Listen {i}.") for i in range(30)]
        sent = []
        def call(key, payload, timeout):
            sent.append(payload["contents"][0]["parts"][0]["text"])
            return reply([]), None
        with mock.patch.object(aa, "ASK_TARGETS", 10), mock.patch.object(aa, "ASK_AROUND", 2):
            aa.ask(rows, [r["segment_id"] for r in rows], "k", call)
        self.assertEqual(len(sent), 3)
        self.assertIn("s12 [12 s]", sent[1])                   # two lines after the last of the first ten
        self.assertNotIn("s13 [13 s]", sent[0])
        self.assertIn("s8 [8 s]", sent[1])                     # two lines before the first of the second ten
        self.assertNotIn("s7 [7 s]", sent[1])


class WhatTheTranslatorIsTold(unittest.TestCase):
    def answers(self):
        return aa.ask(ROWS, ["seg_1", "seg_5", "seg_6", "seg_7", "seg_9"], "k", lambda *a: (reply(ANSWERS), None))

    def test_a_person_is_found_by_id_with_todays_gender(self):
        got = self.answers()
        self.assertEqual(aa.resolve(got["seg_1"], "Don't move.", {"sp2": "Marshall"}, {"Marshall": {"gender": "male"}}),
                         {"addressee_is": "male", "said_to": "Marshall"})
        self.assertIsNone(aa.resolve(got["seg_1"], "Don't move.", {"sp2": "Marshall"}, {"Marshall": {"gender": ""}}))
        self.assertEqual(aa.resolve(got["seg_6"], "Boys, give me room.", {}, {}), {"addressee_is": "group", "group_of": "men"})
        self.assertEqual(aa.resolve(got["seg_9"], "Nobody can tell you what to do in life.", {}, {}), {"said_to": "nobody (a general statement)"})
        self.assertIsNone(aa.resolve(got["seg_7"], "Be brave.", {}, GENDERS))

    def test_an_answer_counts_only_while_the_english_is_the_same(self):
        got = self.answers()
        self.assertIsNone(aa.resolve(got["seg_1"], "Don't you move.", {"sp2": "Speaker 2"}, GENDERS))

    def test_the_answer_replaces_the_neighbour_hint_but_never_a_sir_or_maam(self):
        asked = {"seg_1": {"addressee_is": "male", "said_to": "Speaker 2"}, "seg_0": {"addressee_is": "female", "said_to": "x"}}
        ctx = gender_context.build_context(ROWS, ["seg_0", "seg_1", "seg_3"], GENDERS, asked=asked)
        self.assertEqual((ctx["seg_1"]["addressee_is"], ctx["seg_1"]["said_to"]), ("male", "Speaker 2"))
        self.assertNotIn("probably_said_to", ctx["seg_1"])
        self.assertEqual(ctx["seg_0"]["addressee_is"], "male")             # "sir" in the line stays the certain answer
        self.assertEqual(ctx["seg_3"]["probably_said_to"]["speaker"], "Speaker 4")     # not asked: the old hint
        old = gender_context.build_context(ROWS, ["seg_1"], GENDERS)
        self.assertEqual(old["seg_1"]["probably_said_to"]["gender"], "female")         # what went wrong before

    def test_the_rules_and_the_fields_reach_the_translator(self):
        self.assertEqual(gemini_service.GENDER_RULES, language_prompts.GENDER_RULES_EN)
        self.assertIn('"group_of": "men", "women" or "mixed"', gemini_service.GENDER_RULES)
        seen = {}
        def fake(key, payload, timeout=0):
            seen["prompt"] = payload["contents"][0]["parts"][0]["text"]
            return None, "stop"
        gender = {"speakers": {}, "lines": {"s1": {"speaker_gender": "male", "addressee_is": "group", "group_of": "men",
                                                   "said_to": "x", "before": [], "after": []}}}
        with mock.patch("gemini_service.call_gemini", fake), mock.patch("gemini_service.record_gemini"):
            gemini_service.translate_segments("j", [Segment(segment_id="s1", start=5, end=7, speaker="Red", text="Boys, give me room.")], "k", gender=gender)
        for needle in ('"addressee_is": "group"', '"group_of": "men"', '"said_to": "x"'):
            self.assertIn(needle, seen["prompt"])
        p = language_prompts.translation_prompt([{"segment_id": "s1", "start": 5, "end": 7, "speaker": "Red", "text": "Vai!"}], "it", gender=gender)
        self.assertIn('"group_of": "men"', p)


class InTheEngine(unittest.TestCase):
    def job(self):
        return {"id": "j", "uid": "u", "source_language": "en", "analysis": {},
                "speaker_list": [{"id": f"sp{n}", "name": f"Speaker {n}", "gender": GENDERS.get(f"Speaker {n}", {}).get("gender", ""),
                                  "gender_source": "chosen"} for n in (1, 2, 4, 7)]}

    def run_ask(self, job, rows, answers):
        calls = []
        def call(key, payload, timeout=120, models=None):
            calls.append(payload["contents"][0]["parts"][0]["text"])
            return reply(answers), None
        with mock.patch.object(ld, "GEMINI_API_KEY", "k"), mock.patch("gemini_service.call_gemini", call), \
             mock.patch("app_state.record_gemini"), mock.patch.object(ld, "_save"), mock.patch.object(ld, "_ev"), mock.patch.object(ld, "_mark"):
            n = ld._ask_addressees(job, rows)
        return n, calls

    def test_asked_once_and_the_translation_uses_the_answers(self):
        job, rows = self.job(), [dict(r) for r in ROWS]
        n, calls = self.run_ask(job, rows, ANSWERS)
        self.assertEqual(len(calls), 1)
        asked = calls[0].rstrip().rsplit("QUESTION (line ids): ", 1)[1].split(", ")
        self.assertEqual(asked, ["seg_1", "seg_3", "seg_4", "seg_5", "seg_6", "seg_7", "seg_9"])     # not the "sir" line, not the plain ones
        self.assertEqual(job["analysis"]["addressees"]["seg_1"]["said_to_id"], "sp2")
        g = ld._gender_args(job, rows, rows[:6])
        self.assertEqual(g["lines"]["seg_1"]["addressee_is"], "male")
        self.assertEqual(g["lines"]["seg_5"]["addressee_is"], "male")
        self.assertNotIn("probably_said_to", g["lines"]["seg_1"])
        n2, calls2 = self.run_ask(job, rows, ANSWERS)                 # a resumed run: nothing asked or charged again
        self.assertEqual((n2, calls2), (0, []))
        rows[1]["text"] = "Don't you move."                          # the English changed: that line alone is asked again
        _, calls3 = self.run_ask(job, rows, [])
        self.assertTrue(calls3[0].rstrip().endswith("QUESTION (line ids): seg_1"))
        self.assertIn("probably_said_to", ld._gender_args(job, rows, rows[:2])["lines"]["seg_1"])

    def test_not_for_other_languages_or_when_switched_off(self):
        job = self.job()
        job["source_language"] = "it"
        self.assertEqual(self.run_ask(job, [dict(r) for r in ROWS], ANSWERS), (0, []))
        with mock.patch.object(aa, "ENABLED", False):
            self.assertEqual(self.run_ask(self.job(), [dict(r) for r in ROWS], ANSWERS), (0, []))

    def test_the_translation_asks_first(self):
        import inspect
        src = inspect.getsource(ld._translate_all)
        self.assertLess(src.index("_ask_addressees(job, rows)"), src.index("_translate_batch("))


if __name__ == "__main__":
    unittest.main()
