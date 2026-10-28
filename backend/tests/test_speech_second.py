"""The second listen: a sentence the speech recognition missed becomes a line, a line it clearly misheard gets the second
listener's words, small differences stay. Measured cases from the test clip (Oct 2026)."""
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speech_second as S


def row(i, start, words, speaker="Speaker 1", step=0.4):
    ws = [{"word": w, "start": round(start + k * step, 2), "end": round(start + (k + 1) * step - 0.05, 2)} for k, w in enumerate(words.split())]
    return {"segment_id": f"seg_{i}", "start": ws[0]["start"], "end": ws[-1]["end"], "text": words, "speaker": speaker, "words": ws}


ROWS = [row(0, 274.2, "you got it mister you try the rest"),
        row(1, 303.5, "you made the new news", "Speaker 9"),
        row(2, 330.0, "forty thousand dollars to have him killed"),
        row(3, 425.6, "When you're hurt, I'd like to shoot you."),
        row(4, 428.0, "That chief's name is Lemke.")]

HEARD = [{"start": 274.0, "end": 277.0, "text": "You got it, mister, you try the rest."},
         {"start": 300.4, "end": 303.0, "text": "They're coming this afternoon to fix the elevator."},
         {"start": 303.4, "end": 305.4, "text": "And congratulations, you made the noon news."},
         {"start": 330.0, "end": 333.0, "text": "$40,000 to have him killed."},
         {"start": 425.0, "end": 427.6, "text": "You hear, judge? No problem. Look into what Ancaster's doing here."},
         {"start": 428.0, "end": 430.0, "text": "Their chief, name's Lempke."}]


class Compare(unittest.TestCase):
    def test_a_missed_sentence_becomes_its_own_line_in_the_pause(self):
        out, added, changed = S.apply(ROWS, S.compare(ROWS, HEARD, clip_end=489))
        self.assertEqual(len(added), 1)
        a = added[0]
        self.assertTrue(a["text"].startswith("They're coming this afternoon to fix the elevator"))
        self.assertGreater(a["start"], ROWS[0]["end"]); self.assertLess(a["end"], ROWS[1]["start"])     # inside the pause
        self.assertEqual(a["text_check"], "added")
        self.assertEqual([r["segment_id"] for r in out], [f"seg_{i}" for i in range(len(out))])
        self.assertEqual(out.index(a), 1)                    # in time order

    def test_a_clearly_misheard_line_gets_the_new_words_small_differences_stay(self):
        out, added, changed = S.apply(ROWS, S.compare(ROWS, HEARD, clip_end=489))
        texts = {r["start"]: r["text"] for r in out}
        self.assertIn("You hear, judge?", texts[ROWS[3]["start"]])
        hit = [r for r in changed if r["start"] == ROWS[3]["start"]][0]
        self.assertEqual(hit["text_first"], "When you're hurt, I'd like to shoot you.")      # kept for the person to compare
        self.assertEqual(hit["text_check"], "changed")
        self.assertEqual(texts[ROWS[4]["start"]], "That chief's name is Lemke.")              # spelling only: stays
        self.assertEqual(texts[ROWS[2]["start"]], "forty thousand dollars to have him killed")  # numbers written differently: stays
        self.assertEqual(texts[ROWS[1]["start"]], "you made the new news")                     # one word: stays
        self.assertEqual(len(changed), 1)

    def test_the_new_line_takes_the_speaker_heard_there(self):
        out, added, _ = S.apply(ROWS, S.compare(ROWS, HEARD, clip_end=489), speaker_at=lambda t: "Speaker 10")
        self.assertEqual(added[0]["speaker"], "Speaker 10")
        out, added, _ = S.apply(ROWS, S.compare(ROWS, HEARD, clip_end=489))
        self.assertEqual(added[0]["speaker"], "Speaker 1")          # no detector: the line before

    def test_nothing_new_nothing_changed(self):
        same = [{"start": r["start"], "end": r["end"], "text": r["text"]} for r in ROWS]
        plan = S.compare(ROWS, same, clip_end=489)
        self.assertEqual(plan, {"add": [], "change": {}})

    def test_answer_forms(self):
        self.assertEqual(S.parse('[{"start": 1, "end": 2, "text": "Hi"}]'), [{"start": 1.0, "end": 2.0, "text": "Hi"}])
        self.assertEqual(S.parse('{"lines": [{"start": 1, "end": 2, "text": "Hi"}]}')[0]["text"], "Hi")
        self.assertIsNone(S.parse(""))
        self.assertIsNone(S.parse("not json"))


class RealRun28Oct(unittest.TestCase):
    """What went wrong in the first real run of the second listen (job b9a2face, 28 Oct)."""
    def test_a_different_end_of_a_short_line_does_not_split_a_sentence_over_two_lines(self):
        rows = [row(0, 49.4, "You would not Promise", "Speaker 12"), row(1, 50.9, "we don't", "Speaker 1"),
                row(2, 333.1, "They're gonna miss her.", "Speaker 1"), row(3, 334.2, "All right.", "Speaker 10")]
        heard = [{"start": 49.4, "end": 50.5, "text": "You would not."}, {"start": 50.6, "end": 51.6, "text": "Try me, sweetheart."},
                 {"start": 333.1, "end": 334.6, "text": "How can I miss her?"}]
        plan = S.compare(rows, heard, clip_end=489)
        self.assertEqual(plan["change"], {})

    def test_two_sentences_in_one_pause_become_two_lines(self):
        rows = [row(0, 272.2, "you got it mister you try the rest", "Speaker 3"), row(1, 303.2, "congratulations you made the new news", "Speaker 3")]
        heard = [{"start": 272.2, "end": 275.0, "text": "You got it, mister, you try the rest."},
                 {"start": 278.0, "end": 286.0, "text": "Huh? What do you think, huh? Come on, everybody!"},
                 {"start": 299.9, "end": 302.9, "text": "They're coming this afternoon to fix the elevator."},
                 {"start": 303.2, "end": 305.4, "text": "Congratulations, you made the new news."}]
        plan = S.compare(rows, heard, clip_end=489)
        self.assertEqual(len(plan["add"]), 2)
        a, b = plan["add"]
        self.assertIn("What do you think", a["text"]); self.assertNotIn("elevator", a["text"])
        self.assertIn("elevator", b["text"])
        self.assertLessEqual(a["end"], b["start"])
        self.assertLess(b["end"], 303.2)                     # before the line that follows
        self.assertGreaterEqual(b["start"], 299.0)            # near where it was heard, not right after the line before


class RealRun28OctSecond(unittest.TestCase):
    """Job 09f3e558 (28 Oct): the closing lines were 'rewritten' with words heard 40 s earlier, and a line was cut to a stub."""
    def test_words_heard_at_another_time_never_rewrite_a_line(self):
        rows = [row(0, 435.0, "Thank you.", "Speaker 9"),
                row(1, 476.8, "Thank you for clicking on our video everybody if you like what you saw", "Speaker 13"),
                row(2, 485.0, "We're an independent company and we appreciate all of your support", "Speaker 13")]
        heard = [{"start": 435.0, "end": 436.0, "text": "Thank you."},
                 {"start": 437.5, "end": 441.0, "text": "One of Ancaster's neighbors is bound to have a half-decent meal."}]
        plan = S.compare(rows, heard, clip_end=489)
        self.assertEqual(plan["change"], {})
        self.assertEqual(len(plan["add"]), 1)                      # the missed sentence goes in its own pause instead
        self.assertIn("Ancaster", plan["add"][0]["text"])
        self.assertLess(plan["add"][0]["end"], 476.8)

    def test_a_rewrite_that_leaves_a_stub_is_not_made(self):
        rows = [row(0, 333.2, "I'm gonna miss her today", "Speaker 1"), row(1, 336.0, "All right.", "Speaker 10")]
        heard = [{"start": 333.2, "end": 334.4, "text": "How can I"}, {"start": 336.0, "end": 336.5, "text": "All right."}]
        self.assertEqual(S.compare(rows, heard, clip_end=489)["change"], {})

    def test_a_trailing_and_is_left_for_the_next_line(self):
        self.assertEqual(S._trim("They're coming this afternoon to fix the elevator. And"), "They're coming this afternoon to fix the elevator.")
        self.assertEqual(S._trim("Rock and"), "Rock and")


class Listen(unittest.TestCase):
    def test_one_retry_on_an_empty_answer_and_the_answer_is_saved(self):
        calls = []
        good = {"modelVersion": "gemini-4-argon", "candidates": [{"content": {"parts": [{"text": json.dumps({"lines": HEARD})}]}}], "usageMetadata": {}}
        empty = {"modelVersion": "gemini-4-argon", "candidates": [], "usageMetadata": {}}

        def ask(p):
            calls.append(1)
            return (empty if len(calls) == 1 else good), None
        with tempfile.TemporaryDirectory() as d:
            voc = Path(d) / "v.wav"; voc.write_bytes(b"RIFF0000")
            import speaker_vote_live
            orig = speaker_vote_live._audio_b64
            speaker_vote_live._audio_b64 = lambda *a, **k: "QUFB"
            try:
                got = S.listen("j", voc, d, "key", 489, ask=ask)
                self.assertEqual(len(calls), 2)
                self.assertEqual(len(got["lines"]), len(HEARD))
                again = S.listen("j", voc, d, "key", 489, ask=lambda p: self.fail("the saved answer is reused"))
                self.assertTrue(again["reused"])
            finally:
                speaker_vote_live._audio_b64 = orig

    def test_no_key_no_request(self):
        with tempfile.TemporaryDirectory() as d:
            voc = Path(d) / "v.wav"; voc.write_bytes(b"x")
            self.assertIsNone(S.listen("j", voc, d, "", 10, ask=lambda p: self.fail("no request"))["lines"])


class Wiring(unittest.TestCase):
    def test_runs_after_the_lines_are_built_and_before_anything_else(self):
        import longdub_service as ld
        src = inspect.getsource(ld._run_analysis)
        a = src.index("rows = rows_from_raw(raw_all")
        b = src.index("rows = _second_listener(job, rows, wd, vocals_all, turns, label_map, source_language)")
        c = src.index("_kept = _kept_start(raw_all, rows)")
        self.assertLess(a, b); self.assertLess(b, c)
        self.assertLess(b, src.index("rows = _apply_subtitle(job, rows)"))

    def test_skipped_with_a_subtitle_or_switched_off(self):
        import longdub_service as ld
        src = inspect.getsource(ld._second_listener)
        self.assertIn('job.get("subtitle")', src)
        self.assertIn("speech_second.ENABLED", src)

    def test_editor_mark_and_its_confirm(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "dub_long.html").read_text(encoding="utf-8")
        self.assertIn("function textBadge(s)", html)
        self.assertIn("d.text_ok = true", html)
        self.assertIn("!!s.text_check", html)
        self.assertNotIn("Gemini", html[html.index("function textBadge"):html.index("function textBadge") + 2000])
        import longdub_service as ld
        self.assertIn('e.get("text_ok") is True and r.get("text_check")', (root / "longdub_service.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
