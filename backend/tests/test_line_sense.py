"""Does each line make sense for its speaker (line_sense.py, 8 Nov 2026), and the speaker check that no longer gives up."""
import json
import unittest
from pathlib import Path
from unittest import mock

import line_sense as ls
import longdub_service as ld
import speaker_vote_live as svl

ROOT = Path(__file__).resolve().parents[1]


def rows():
    return [
        {"segment_id": "seg_0", "start": 124.78, "end": 128.06, "speaker": "Speaker 1", "speaker_id": "sp1", "text": "Messenger of God, let me stand with you."},
        {"segment_id": "seg_1", "start": 130.49, "end": 132.67, "speaker": "Speaker 1", "speaker_id": "sp1", "text": "Let me stand with you, Ali."},
        {"segment_id": "seg_2", "start": 151.27, "end": 151.79, "speaker": "Speaker 2", "speaker_id": "sp2", "text": "Yes."},
        {"segment_id": "seg_3", "start": 154.80, "end": 155.60, "speaker": "Speaker 2", "speaker_id": "sp2", "text": "Thanks, God."},
        {"segment_id": "seg_4", "start": 230.71, "end": 234.89, "speaker": "Speaker 1", "speaker_id": "sp1",
         "text": "and will you survive o prophet of god if i spend the night there yes Thanks for watching."},
    ]


ANSWER = {"problems": [
    {"id": "seg_1", "kind": "split", "words": "Ali.", "speaker": "Speaker 2", "reason": "the other person says the name"},
    {"id": "seg_3", "kind": "wrong_speaker", "words": "", "speaker": "Speaker 1", "reason": "Ali thanks God"},
    {"id": "seg_4", "kind": "not_spoken", "words": "Thanks for watching.", "speaker": "", "reason": "speech recognition"},
    {"id": "seg_4", "kind": "split", "words": "yes", "speaker": "Speaker 2", "reason": "the answer"},
    {"id": "seg_9", "kind": "split", "words": "x", "speaker": "Speaker 2"},                 # no such line
    {"id": "seg_0", "kind": "split", "words": "not in the line", "speaker": "Speaker 2"},   # words not in the line
    {"id": "seg_0", "kind": "wrong_speaker", "speaker": "Somebody"},                        # not a speaker of the scene
    {"id": "seg_2", "kind": "rewrite", "words": "Yes."},                                    # not a kind we take
]}


def fake_call(answer):
    def call(key, payload, timeout):
        call.payload = payload
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]}, None
    return call


class Ask(unittest.TestCase):
    def test_only_answers_that_fit_the_lines_are_kept(self):
        call = fake_call(ANSWER)
        got = ls.ask(rows(), "QUJD", "key", call)
        self.assertEqual([(p["id"], p["kind"]) for p in got],
                         [("seg_1", "split"), ("seg_3", "wrong_speaker"), ("seg_4", "not_spoken"), ("seg_4", "split")])
        cfg = call.payload["generationConfig"]
        self.assertEqual(cfg["thinkingConfig"]["thinkingLevel"], "low")
        self.assertEqual(call.payload["contents"][0]["parts"][0]["inline_data"]["data"], "QUJD")
        self.assertIn('"speaker": "Speaker 1"', call.payload["contents"][0]["parts"][1]["text"])

    def test_nothing_without_a_key_or_an_answer(self):
        self.assertEqual(ls.ask(rows(), None, "", fake_call(ANSWER)), [])
        self.assertEqual(ls.ask(rows(), None, "key", lambda k, p, t: (None, "down")), [])
        self.assertEqual(ls.ask(rows(), None, "key", lambda k, p, t: ({"candidates": []}, None)), [])


class Apply(unittest.TestCase):
    def run_apply(self):
        job = {"id": "t", "seg_counter": 50}
        probs = ls.check(rows(), ANSWER["problems"])
        return ls.apply(rows(), probs, ld._split_plan, lambda rs: ld._new_segment_id(job, rs), {"Speaker 1": "sp1", "Speaker 2": "sp2"},
                        ld._clear_speaker_review)

    def test_the_chapter_one_problems(self):
        new, summary = self.run_apply()
        by = [(r["segment_id"], r["speaker"], r["text"]) for r in new]
        self.assertIn(("seg_1", "Speaker 1", "Let me stand with you,"), by)
        self.assertIn(("seg_50", "Speaker 2", "Ali."), by)                    # cut, and the name goes to the other person
        four = [r for r in new if r["segment_id"] in ("seg_4", "seg_51")]
        self.assertEqual([(r["speaker"], r["text"]) for r in four],
                         [("Speaker 1", "and will you survive o prophet of god if i spend the night there"), ("Speaker 2", "yes")])
        self.assertGreaterEqual(four[1]["end"] - four[1]["start"], 0.59)       # without word times the short part still gets time
        three = next(r for r in new if r["segment_id"] == "seg_3")
        self.assertEqual((three["speaker"], three["speaker_confidence"], three["speaker_reasons"], three["sense_speaker"]),
                         ("Speaker 2", "low", ["conversation_suggests"], "Speaker 1"))     # only marked, never changed by itself
        self.assertEqual(len(summary["split"]), 2)
        self.assertEqual(summary["not_spoken"], ['seg_4 "Thanks for watching."'])
        self.assertEqual([r["segment_id"] for r in new], ["seg_0", "seg_1", "seg_50", "seg_2", "seg_3", "seg_4", "seg_51"])

    def test_words_and_word_times_go_together(self):
        row = {"segment_id": "s", "start": 1.0, "end": 5.0, "text": "I am Ali. Thanks for watching.",
               "words": [{"start": 1.0, "end": 1.2}, {"start": 1.2, "end": 1.4}, {"start": 1.4, "end": 1.9},
                         {"start": 3.0, "end": 3.4}, {"start": 3.4, "end": 3.6}, {"start": 3.6, "end": 4.9}]}
        out = ls._remove_words(row, "Thanks for watching.")
        self.assertEqual((out["text"], len(out["words"]), out["end"]), ("I am Ali.", 3, 1.9))
        self.assertIsNone(ls._remove_words(dict(row, text="Thanks for watching.", words=[]), "Thanks for watching."))

    def test_a_failure_leaves_the_lines_alone(self):
        new, _ = ls.apply(rows(), [{"id": "seg_1", "kind": "split", "words": "Ali.", "speaker": "Speaker 2"}],
                          lambda r, p: 1 / 0, lambda rs: "x", {}, lambda r: None)
        self.assertIsNone(new)


class SpeakerCheckNeverGivesUp(unittest.TestCase):
    def test_the_second_try_thinks_briefly_and_the_ending_is_logged(self):
        rs = [{"segment_id": f"seg_{i}", "start": i, "end": i + 1, "text": "Hello."} for i in range(4)]
        sent = []

        def ask(key, payload, timeout):
            sent.append(payload["generationConfig"])
            if len(sent) == 1:
                return {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}],
                        "usageMetadata": {"thoughtsTokenCount": 28973, "candidatesTokenCount": 3}}, None
            return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(
                {"people": 2, "lines": [{"id": f"seg_{i}", "speaker": "AB"[i % 2]} for i in range(4)]})}]}}]}, None
        usage = {"usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0}
        got = svl._one_voter("dialogue", "job", rs, None, "key", ask, usage)
        self.assertEqual(len(got), 4)
        self.assertEqual([c["thinkingConfig"]["thinkingLevel"] for c in sent], [svl.THINKING, "low"])
        self.assertGreater(sent[1]["maxOutputTokens"], sent[0]["maxOutputTokens"])      # and more room the second time
        self.assertEqual(usage["finish"], ["dialogue:MAX_TOKENS", "dialogue:STOP"])


class Wiring(unittest.TestCase):
    def test_the_analysis_runs_it_and_the_editor_shows_it(self):
        src = (ROOT / "longdub_service.py").read_text(encoding="utf-8")
        flow = src.split("_review_speakers(job, rows, turns, label_map, vote=vote)")[1][:200]
        self.assertIn("rows = _line_sense(job, rows, wd, vocals_all)", flow)
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"conversation_suggests")', main)
        self.assertIn('out["sense_speaker_id"]', main)
        page = (ROOT / "dub_long.html").read_text(encoding="utf-8")
        self.assertIn('conversation_suggests: ["The conversation suggests another speaker", "يشير سياق الحوار إلى متحدث آخر"]', page)


if __name__ == "__main__":
    unittest.main()
