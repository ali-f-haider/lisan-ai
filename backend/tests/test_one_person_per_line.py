"""Air Force One run, 28 Oct 2026 (104 of 115 lines right): lines holding two people, short sentences left out, a quiet first
sentence cut off, a first word placed seconds early, made-up words in jet noise, and the pitch check moving a line all three
voters agreed on. Each is fixed at its cause; these tests pin the fixes."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speech_second as S
import pitch_check as pc
import line_tidy as lt


def W(word, a, b):
    return {"word": word, "start": a, "end": b}


def row(i, words, spk="Speaker 1", **kw):
    r = {"segment_id": f"seg_{i}", "start": words[0]["start"], "end": words[-1]["end"], "speaker": spk,
         "text": " ".join(w["word"] for w in words), "words": words}
    r.update(kw)
    return r


GUARD_OPERATOR = [      # how the app cut 1:28-1:35 (the guard and the operator in every line)
    row(0, [W("don't", 88.8, 89.0), W("move", 89.0, 89.4), W("i", 89.43, 89.6)], "Speaker 1"),
    row(1, [W("traced", 89.7, 90.1), W("the", 90.1, 90.2), W("call", 90.2, 90.6), W("sir", 90.6, 91.2),
            W("Give", 91.4, 91.6), W("me", 91.6, 91.7), W("the", 91.7, 91.8), W("gun.", 91.8, 92.2), W("are", 92.5, 92.7)], "Speaker 5"),
    row(2, [W("you", 93.13, 94.07), W("there", 94.07, 94.31), W("very", 94.31, 94.79), W("slowly", 94.79, 95.11)], "Speaker 5"),
]
HEARD = [{"start": 89.3, "end": 90.0, "text": "Don't move."}, {"start": 91.5, "end": 93.3, "text": "I traced the call, sir."},
         {"start": 93.3, "end": 94.1, "text": "Give me the gun."}, {"start": 94.3, "end": 95.1, "text": "Are you there?"},
         {"start": 95.3, "end": 96.2, "text": "Very slowly."}]


class OnePersonPerLine(unittest.TestCase):
    def test_lines_are_cut_where_the_second_listener_heard_a_new_sentence(self):
        rows, cuts = S.cut_at_sentences([dict(r) for r in GUARD_OPERATOR], HEARD)
        self.assertEqual([r["text"] for r in rows],
                         ["don't move", "i traced the call sir", "Give me the gun.", "are you there", "very slowly"])
        self.assertGreater(cuts, 0)
        self.assertEqual([r["speaker"] for r in rows], ["Speaker 1", "Speaker 5", "Speaker 5", "Speaker 5", "Speaker 5"])
        words = [w["word"] for r in rows for w in r["words"]]
        self.assertEqual(words, [w["word"] for r in GUARD_OPERATOR for w in r["words"]])        # nothing lost, nothing added
        self.assertEqual([r["segment_id"] for r in rows], [f"seg_{i}" for i in range(5)])

    def test_a_line_with_one_sentence_is_left_as_it_was(self):
        rows = [row(0, [W("Fine", 1.0, 1.3), W("by", 1.3, 1.5), W("me.", 1.5, 1.8)], text="Fine by me.")]
        out, cuts = S.cut_at_sentences([dict(r) for r in rows], [{"start": 1.0, "end": 1.8, "text": "Fine by me."}])
        self.assertEqual((out[0]["text"], cuts), ("Fine by me.", 0))

    def test_a_line_set_by_hand_is_never_cut(self):
        rows = [dict(r) for r in GUARD_OPERATOR]
        rows[1]["manual_speaker"] = True
        out, _ = S.cut_at_sentences(rows, HEARD)
        self.assertIn("traced the call sir Give me the gun. are", [r["text"] for r in out])


class ShortSentencesOnTheVoice(unittest.TestCase):
    def rows(self):
        return [row(0, [W("I'm", 121.8, 122.3), W("the", 122.3, 122.5), W("president", 122.5, 123.4), W("of", 123.4, 123.6),
                        W("the", 123.6, 123.8), W("United", 123.8, 124.3), W("States.", 124.3, 125.0)]),
                row(1, [W("Don't", 127.7, 127.9), W("think", 127.9, 128.1), W("that", 128.1, 128.3), W("means", 128.3, 128.6),
                        W("I", 128.6, 128.7), W("don't", 128.7, 128.9), W("shoot", 128.9, 129.1), W("you.", 129.1, 129.4)], "Speaker 2")]

    HEARD = [{"start": 121.0, "end": 125.0, "text": "I'm the president of the United States."},
             {"start": 125.7, "end": 126.2, "text": "He's alive!"},
             {"start": 127.7, "end": 129.4, "text": "Don't think that means I don't shoot you."}]

    def test_a_two_word_sentence_is_added_when_a_voice_sounds_there(self):
        plan = S.compare(self.rows(), self.HEARD, voiced=lambda lo, hi: [(125.4, 126.0)])
        self.assertEqual([(a["text"], a["start"]) for a in plan["add"]], [("He's alive!", 125.35)])

    def test_nothing_is_added_where_no_voice_sounds(self):
        plan = S.compare(self.rows(), self.HEARD, voiced=lambda lo, hi: [])
        self.assertEqual(plan["add"], [])

    def test_without_the_voices_short_sentences_stay_out_as_before(self):
        self.assertEqual(S.compare(self.rows(), self.HEARD)["add"], [])

    def test_a_sentence_goes_on_the_voice_not_on_the_second_listeners_clock(self):
        rows = [row(0, [W("very", 94.31, 94.79), W("slowly", 94.79, 95.11)]),
                row(1, [W("I'll", 98.5, 98.7), W("put", 98.7, 98.9), W("you", 98.9, 99.0), W("through.", 99.0, 99.6)], "Speaker 2")]
        heard = [{"start": 94.3, "end": 95.1, "text": "very slowly"}, {"start": 95.6, "end": 96.6, "text": "Oh, my God."},
                 {"start": 98.5, "end": 99.6, "text": "I'll put you through."}]
        plan = S.compare(rows, heard, voiced=lambda lo, hi: [(96.7, 97.9)])
        self.assertEqual([(a["text"], a["start"], a["end"]) for a in plan["add"]], [("Oh, my God.", 96.65, 97.95)])


class Unheard(unittest.TestCase):
    def test_words_where_the_second_listener_heard_nothing_are_marked(self):
        rows = [row(0, [W("Fox", 225.7, 226.0), W("three.", 226.0, 226.2)]),
                row(1, [W("I", 262.0, 262.2), W("was", 262.2, 262.4), W("the", 262.4, 262.5), W("team.", 262.5, 262.9)])]
        marked = S.mark_unheard(rows, [{"start": 225.6, "end": 226.6, "text": "Fox three."}])
        self.assertEqual([r["segment_id"] for r in marked], ["seg_1"])
        self.assertEqual(rows[1]["text_check"], "unheard")
        self.assertNotIn("text_check", rows[0])


class EarlyWord(unittest.TestCase):
    def test_a_first_word_placed_seconds_early_moves_next_to_its_sentence(self):
        import longdub_service as ld
        segs = [NS(start=388.8, end=390.97, text=" Jim, they shot Jack. We", words=[
                    NS(word=" Jim,", start=388.8, end=389.1), NS(word=" Doherty.", start=389.1, end=389.9), NS(word=" We", start=390.49, end=390.97)]),
                NS(start=395.21, end=396.4, text=" can't give in", words=[
                    NS(word=" can't", start=395.21, end=395.5), NS(word=" give", start=395.5, end=395.8), NS(word=" in", start=395.8, end=396.4)])]
        self.assertEqual(ld.fix_early_words(segs), 1)
        self.assertEqual([w.word for w in segs[1].words], [" We", " can't", " give", " in"])
        self.assertAlmostEqual(segs[1].words[0].end, 395.16)
        self.assertEqual(segs[0].end, 389.9)

    def test_a_word_that_ends_a_sentence_or_is_followed_soon_stays(self):
        import longdub_service as ld
        segs = [NS(start=0, end=1, text=" Fine.", words=[NS(word=" Fine.", start=0.5, end=1.0)]),
                NS(start=9, end=10, text=" what now", words=[NS(word=" what", start=9.0, end=9.3), NS(word=" now", start=9.3, end=10.0)])]
        self.assertEqual(ld.fix_early_words(segs), 0)


class QuietStart(unittest.TestCase):
    def test_a_line_never_starts_after_words_heard_together(self):
        words = [W("Believe", 160.5, 160.8), W("me.", 160.8, 161.1), W("All", 161.1, 161.3), W("that'd", 161.3, 161.6),
                 W("happen", 161.6, 162.0)]
        self.assertEqual(lt._trusted_start(words), 160.5)

    def test_a_short_run_seconds_before_the_rest_may_be_skipped(self):
        words = [W("You", 86.3, 86.5), W("know,", 86.5, 86.8), W("it", 91.1, 91.2), W("was", 91.2, 91.4), W("late", 91.4, 91.7),
                 W("again", 91.7, 92.0)]
        self.assertAlmostEqual(lt._trusted_start(words), 91.1 - lt.LEAD_MARGIN)


class PitchAndVoters(unittest.TestCase):
    def rows_and_meas(self, voted=True):
        rows, meas = [], {}
        for i in range(12):
            spk = "Speaker 1" if i % 2 == 0 else "Speaker 3"
            hz = 162.0 if spk == "Speaker 1" else 97.0
            rows.append({"segment_id": f"seg_{i}", "start": i * 3.0, "end": i * 3.0 + 2.0, "speaker": spk, "text": "x"})
            meas[f"seg_{i}"] = (40, hz + (i % 3), 1.0)
        rows[4]["speaker_vote"] = {"support": 3, "present": 3} if voted else {"support": 2, "present": 3}
        meas["seg_4"] = (40, 89.0, 1.0)          # "I have a good lock." -- the pilot, measured low
        return rows, meas

    def test_two_men_a_line_all_voters_agree_on_is_not_moved(self):
        rows, meas = self.rows_and_meas()
        held = []
        moves, _ = pc.decide(rows, meas, held)
        self.assertEqual(moves, [])
        self.assertEqual([h["sid"] for h in held], ["seg_4"])

    def test_a_low_line_an_octave_below_a_middle_voice_is_not_moved_either(self):
        rows, meas = self.rows_and_meas(voted=False)
        held = []
        self.assertEqual(pc.decide(rows, meas, held)[0], [])
        self.assertEqual(held[0]["why"], "octave")

    def test_a_man_and_a_boy_are_still_told_apart_even_against_the_voters(self):
        rows, meas = [], {}
        for i in range(12):
            spk = "Speaker 1" if i % 2 == 0 else "Speaker 2"
            rows.append({"segment_id": f"seg_{i}", "start": i * 3.0, "end": i * 3.0 + 2.0, "speaker": spk, "text": "x"})
            meas[f"seg_{i}"] = (40, (110.0 if spk == "Speaker 1" else 260.0) + (i % 3), 1.0)
        rows[4]["speaker_vote"] = {"support": 3, "present": 3}
        meas["seg_4"] = (40, 255.0, 1.0)          # the boy's line, all three voters said the man
        moves, _ = pc.decide(rows, meas, [])
        self.assertEqual([(m["sid"], m["to"]) for m in moves], [("seg_4", "Speaker 2")])


if __name__ == "__main__":
    unittest.main()
