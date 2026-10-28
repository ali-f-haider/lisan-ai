"""The answer-sheet scorer: who speaks is compared by time, app names are matched one-to-one to the real people."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import answer_score as A

REF = {"turns": [{"start": 0, "end": 2, "speaker": "Billy", "text": "a"}, {"start": 2, "end": 4, "speaker": "Mother", "text": "b"},
                 {"start": 4, "end": 6, "speaker": "Billy", "text": "c"}, {"start": 6, "end": 8, "speaker": "Daughter", "text": "d"}]}


class Score(unittest.TestCase):
    def test_names_do_not_matter_only_who(self):
        rows = [{"start": 0.1, "end": 1.9, "speaker": "Speaker 3"}, {"start": 2.1, "end": 3.9, "speaker": "Speaker 1"},
                {"start": 4.0, "end": 6.0, "speaker": "Speaker 3"}, {"start": 6.2, "end": 7.5, "speaker": "Speaker 2"}]
        s = A.score(REF, rows)
        self.assertEqual((s["lines_right"], s["lines_scored"]), (4, 4))

    def test_a_merged_pair_costs_lines_and_shows_who_was_never_found(self):
        rows = [{"start": 0, "end": 2, "speaker": "S1"}, {"start": 2, "end": 4, "speaker": "S2"}, {"start": 4, "end": 6, "speaker": "S1"},
                {"start": 6, "end": 8, "speaker": "S2"}]
        s = A.score(REF, rows)
        self.assertEqual(s["lines_right"], 3)
        self.assertEqual(len(s["people_never_matched"]), 1)
        self.assertEqual(len(s["mistakes"]), 1)

    def test_the_c4k_answer_sheet_is_readable(self):
        ref = json.loads((Path(__file__).resolve().parents[1] / "eval" / "c4k_reference.json").read_text(encoding="utf-8"))
        self.assertEqual(len(ref["people"]), 15)
        self.assertGreater(len(ref["turns"]), 100)

    def test_every_answer_sheet_is_usable(self):
        files = sorted((Path(__file__).resolve().parents[1] / "eval").glob("*_reference.json"))
        self.assertGreaterEqual(len(files), 2)
        for f in files:
            ref = json.loads(f.read_text(encoding="utf-8"))
            self.assertTrue(ref["clip"] and ref["duration_sec"] > 0, f.name)
            self.assertEqual(sorted({t["speaker"] for t in ref["turns"]}), sorted(ref["people"]), f.name)
            for a, b in zip(ref["turns"], ref["turns"][1:]):
                self.assertLess(a["start"], a["end"], f.name)
                self.assertLessEqual(a["end"], b["start"] + 0.05, f.name)

    def test_the_air_force_one_answer_sheet_has_nine_people(self):
        ref = json.loads((Path(__file__).resolve().parents[1] / "eval" / "af1_reference.json").read_text(encoding="utf-8"))
        self.assertEqual(ref["clip"], "Fighter-Jets-Shoot-SnapYT.App.mp4")
        self.assertEqual(len(ref["people"]), 9)


class InTheLog(unittest.TestCase):
    def test_a_test_clip_gets_its_score_in_the_log_and_others_do_not(self):
        import longdub_service as ld
        from unittest import mock
        ref = json.loads((Path(__file__).resolve().parents[1] / "eval" / "c4k_reference.json").read_text(encoding="utf-8"))
        rows = [{"segment_id": f"seg_{i}", "start": t["start"], "end": t["end"], "speaker": "S" + t["speaker"]} for i, t in enumerate(ref["turns"])]
        seen = []
        with mock.patch.object(ld, "_ev", lambda job, step, status, detail, *a: seen.append((step, detail))):
            sc = ld._answer_sheet_score({"filename": "C4K Clip.mp4", "analysis": {"audio_duration": 488.8}}, rows)
            self.assertEqual(sc["line_accuracy"], 1.0)
            self.assertEqual(seen[0][0], "answer_sheet_score")
            self.assertIn("100.0%", seen[0][1])
            self.assertIsNone(ld._answer_sheet_score({"filename": "other.mp4", "analysis": {"audio_duration": 488.8}}, rows))
            self.assertIsNone(ld._answer_sheet_score({"filename": "C4K Clip.mp4", "analysis": {"audio_duration": 300}}, rows))
        self.assertEqual(len(seen), 1)

    def test_the_air_force_one_clip_is_scored_too(self):
        import longdub_service as ld
        from unittest import mock
        ref = json.loads((Path(__file__).resolve().parents[1] / "eval" / "af1_reference.json").read_text(encoding="utf-8"))
        rows = [{"segment_id": f"seg_{i}", "start": t["start"], "end": t["end"], "speaker": "S" + t["speaker"]} for i, t in enumerate(ref["turns"])]
        with mock.patch.object(ld, "_ev", lambda *a: None):
            sc = ld._answer_sheet_score({"filename": "Fighter-Jets-Shoot-SnapYT.App.mp4", "analysis": {"audio_duration": 441.2}}, rows)
        self.assertEqual(sc["line_accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
