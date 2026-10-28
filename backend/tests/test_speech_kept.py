"""Sentences the speech recognition heard stay in the transcript: a step that loses one is named in the log and the sentence
comes back as its own line, marked 'check speaker' (the receptionist's 'They're coming this afternoon...' went missing)."""
import inspect
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speech_kept as K


def seg(words):
    return NS(words=[NS(word=w, start=s, end=e) for s, e, w in words])


HEARD = [seg([(274.2, 274.5, "you"), (274.5, 274.8, "got"), (274.8, 275.1, "it")]),
         seg([(299.9, 300.2, "they're"), (300.2, 300.5, "coming"), (300.5, 300.9, "this"), (300.9, 301.4, "afternoon"),
              (301.5, 302.4, "elevator"), (302.6, 303.3, "congratulations"), (303.5, 303.8, "you"), (303.8, 304.1, "made"),
              (304.1, 304.5, "the"), (304.5, 304.9, "noon"), (304.9, 305.4, "news")])]


def rows_full():
    return [{"segment_id": "seg_0", "start": 274.2, "end": 275.1, "text": "you got it", "speaker": "Speaker 9"},
            {"segment_id": "seg_1", "start": 299.9, "end": 303.3, "text": "they're coming this afternoon elevator congratulations", "speaker": "Speaker 9"},
            {"segment_id": "seg_2", "start": 303.5, "end": 305.4, "text": "you made the noon news", "speaker": "Speaker 11"}]


class Kept(unittest.TestCase):
    def test_a_line_a_step_drops_is_named_and_put_back(self):
        k = K.Tracker(HEARD)
        k.check("lines built", rows_full())
        lost = [r for r in rows_full() if r["segment_id"] != "seg_1"]
        k.check("speaker check", rows_full())
        k.check("line clean-up", lost)
        self.assertEqual([step for step, _ in k.lost_at], ["line clean-up"])
        out, back = k.restore(lost)
        self.assertEqual(len(back), 1)
        new = [r for r in out if r.get("restored")]
        self.assertEqual(len(new), 1)
        self.assertTrue(new[0]["text"].startswith("they're coming"))
        self.assertEqual(new[0]["speaker"], "Speaker 11")              # the line right after it: the same voice goes on
        self.assertTrue(new[0]["speaker_vote"]["badge"])               # shown as 'check speaker'
        self.assertEqual([r["segment_id"] for r in out], ["seg_0", "seg_3", "seg_2"])      # in time order, a new id
        self.assertLessEqual(new[0]["end"], 303.5)
        self.assertIn("line clean-up 4:59.9", K.summary(k, back))

    def test_words_moved_to_another_line_are_not_lost(self):
        k = K.Tracker(HEARD)
        k.check("lines built", rows_full())
        moved = rows_full()
        moved[2]["text"] = moved[1]["text"] + " " + moved[2]["text"]; moved[2]["start"] = 302.0      # joined, start moved later
        moved.pop(1)
        k.check("line starts", moved)
        self.assertEqual(k.lost_at, [])
        self.assertEqual(k.restore(moved)[1], [])

    def test_words_never_in_a_line_are_only_logged(self):
        k = K.Tracker(HEARD)
        start = [r for r in rows_full() if r["segment_id"] != "seg_1"]
        k.check("lines built", start)
        out, back = k.restore(start)
        self.assertEqual(back, [])
        self.assertEqual(len(out), 2)
        self.assertIn("heard but never in a line", K.summary(k, back))

    def test_a_single_short_word_is_not_a_sentence(self):
        k = K.Tracker([seg([(10.0, 10.2, "Mm")])])
        k.check("lines built", [{"start": 10.0, "end": 10.2, "text": "Mm"}])
        k.check("x", [])
        self.assertEqual(k.lost_at, [])

    def test_nothing_lost_nothing_logged(self):
        k = K.Tracker(HEARD)
        k.check("lines built", rows_full())
        k.check("line starts", rows_full())
        self.assertEqual(K.summary(k, k.restore(rows_full())[1]), "")


class Wiring(unittest.TestCase):
    def test_every_step_is_checked_and_the_lines_are_restored_before_the_review(self):
        import longdub_service as ld
        src = inspect.getsource(ld._run_analysis)
        for step in ("speaker check", "face check", "pitch check", "line clean-up", "line planner", "line starts"):
            self.assertIn(f'_kept_check(_kept, "{step}", rows)', src)
        self.assertLess(src.index("rows = _kept_restore(job, _kept, rows)"), src.index("_review_speakers(job, rows, turns"))
        self.assertLess(src.index("_kept = _kept_start(raw_all, rows)"), src.index("rows = _apply_subtitle(job, rows)"))


if __name__ == "__main__":
    unittest.main()
