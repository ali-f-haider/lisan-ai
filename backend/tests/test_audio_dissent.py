"""v1.82.70: the audio check outranks the dialogue check on a tie, and its disagreement is a reason to look.
Offline; invented lines (no film dialogue)."""
import unittest

import speaker_vote as sv
import speaker_vote_live as live


def lines(n=6):
    return [{"segment_id": f"seg_{i}", "speaker": "Speaker 1" if i % 2 == 0 else "Speaker 2"} for i in range(n)]


def labels(rows, flips=(), a="A", b="B"):
    out = {}
    for i, r in enumerate(rows):
        mine = a if i % 2 == 0 else b
        if i in flips:
            mine = b if mine == a else a
        out[r["segment_id"]] = mine
    return out


class AudioDissent(unittest.TestCase):
    def setUp(self):
        self.rows = lines()

    def run_vote(self, dialogue, audio, app=None):
        voters = {"dialogue": dialogue, "audio": audio, "voice": None}
        if app is not None:
            voters["app"] = app
        return sv.decide(self.rows, voters)

    def test_audio_alone_against_two_others_marks_the_line_without_moving_it(self):
        # dialogue and the detector both say line 3 is Speaker 1's; audio says Speaker 2 (the truth in the Dutch clip)
        rows = self.rows
        app = {r["segment_id"]: r["speaker"] for r in rows}
        app["seg_3"] = "Speaker 1"
        for r in rows:
            r["speaker"] = app[r["segment_id"]]
        res = self.run_vote(labels(rows, flips=(3,)), labels(rows))
        d = {x["segment_id"]: x for x in res["lines"]}
        self.assertEqual(d["seg_3"]["speaker"], "Speaker 1")
        self.assertTrue(d["seg_3"]["badge"])
        self.assertIn("audio_disagrees", d["seg_3"]["reasons"])
        self.assertIn("voters_split", d["seg_3"]["reasons"])
        self.assertEqual(res["summary"]["badged_lines"], 1)

    def test_lines_the_audio_check_agrees_on_are_not_marked(self):
        rows = self.rows
        res = self.run_vote(labels(rows), labels(rows))
        self.assertFalse(any(x["badge"] for x in res["lines"]))
        self.assertFalse(any("audio_disagrees" in x["reasons"] for x in res["lines"]))

    def test_dialogue_and_audio_both_wrong_together_is_not_marked_by_this_rule(self):
        rows = self.rows
        res = self.run_vote(labels(rows, flips=(3,)), labels(rows, flips=(3,)))
        d = {x["segment_id"]: x for x in res["lines"]}
        self.assertNotIn("audio_disagrees", d["seg_3"]["reasons"])

    def test_three_way_split_goes_to_the_audio_answer_not_the_dialogue_answer(self):
        rows = self.rows
        # line 2: app says Speaker 1; dialogue says B-line... make all three differ on 3 speakers
        for r in rows:
            r["speaker"] = "Speaker 1" if int(r["segment_id"][4:]) % 3 == 0 else "Speaker 2" if int(r["segment_id"][4:]) % 3 == 1 else "Speaker 3"
        base = {r["segment_id"]: {"Speaker 1": "A", "Speaker 2": "B", "Speaker 3": "C"}[r["speaker"]] for r in rows}
        dialogue, audio = dict(base), dict(base)
        dialogue["seg_1"] = "C"     # app: B (Speaker 2), dialogue: C, audio: A -> three different answers
        audio["seg_1"] = "A"
        res = self.run_vote(dialogue, audio)
        d = {x["segment_id"]: x for x in res["lines"]}
        self.assertEqual(d["seg_1"]["speaker"], "Speaker 1")
        self.assertIn("tie_broken_by_audio", d["seg_1"]["reasons"])
        self.assertTrue(d["seg_1"]["badge"])

    def test_dialogue_and_audio_agreeing_win_normally(self):
        rows = self.rows
        for r in rows:
            r["speaker"] = "Speaker 1" if int(r["segment_id"][4:]) % 3 == 0 else "Speaker 2" if int(r["segment_id"][4:]) % 3 == 1 else "Speaker 3"
        base = {r["segment_id"]: {"Speaker 1": "A", "Speaker 2": "B", "Speaker 3": "C"}[r["speaker"]] for r in rows}
        dialogue, audio = dict(base), dict(base)
        # only 3 voters, so a tie always includes every answer; here two voters tie at 1 with audio = dialogue's neighbour
        dialogue["seg_1"] = "C"
        audio["seg_1"] = "C"
        res = self.run_vote(dialogue, audio)
        d = {x["segment_id"]: x for x in res["lines"]}
        self.assertEqual(d["seg_1"]["speaker"], "Speaker 3")
        self.assertNotIn("audio_disagrees", d["seg_1"]["reasons"])

    def test_a_hand_set_speaker_is_never_marked(self):
        rows = self.rows
        rows[3]["manual_speaker"] = True
        app = {r["segment_id"]: r["speaker"] for r in rows}
        res = self.run_vote(labels(rows, flips=(3,)), labels(rows))
        d = {x["segment_id"]: x for x in res["lines"]}
        self.assertFalse(d["seg_3"]["badge"])
        self.assertNotIn("audio_disagrees", d["seg_3"]["reasons"])

    def test_the_editor_shows_the_existing_not_sure_text_for_it(self):
        row = {"speaker_vote": {"badge": True, "reasons": ["voters_split", "audio_disagrees"]}}
        ev = live.review_from_vote(row)
        self.assertEqual(ev["speaker_confidence"], "low")
        self.assertEqual(ev["speaker_reasons"], ["voters_split"])

    def test_no_audio_voter_means_nothing_new(self):
        rows = self.rows
        res = sv.decide(rows, {"dialogue": labels(rows), "audio": None, "voice": labels(rows)})
        self.assertFalse(any("audio_disagrees" in x["reasons"] for x in res["lines"]))


if __name__ == "__main__":
    unittest.main()
