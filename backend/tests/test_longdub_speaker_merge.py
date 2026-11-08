"""Long dub speakers (8 Nov 2026): two speakers can be put together as one, and the page never throws away the names typed on it
when a save is refused (two speakers with the same name, an empty name)."""
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import longdub_service as ld

ROOT = Path(__file__).resolve().parents[1]


class Merge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(ld, "LONG_DIR", Path(self.tmp.name))
        self.p.start()
        self.job = {"id": "job-merge", "uid": "u", "status": "editing",
                    "speaker_list": [{"id": "sp1", "name": "Soldier", "gender": "male", "gender_source": "chosen"},
                                     {"id": "sp2", "name": "Guard"}, {"id": "sp3", "name": "Old man"}]}
        ld._save(self.job)
        ld._write_segments(self.job, [{"segment_id": "a", "speaker_id": "sp1", "speaker": "Soldier", "text": "Go."},
                                      {"segment_id": "b", "speaker_id": "sp2", "speaker": "Guard", "text": "Stop."},
                                      {"segment_id": "c", "speaker_id": "sp2", "speaker": "Guard", "text": "Who are you?"},
                                      {"segment_id": "d", "speaker_id": "sp3", "speaker": "Old man", "text": "Me."}])

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_the_lines_move_and_the_speaker_goes(self):
        ok, res = ld.merge_speakers(self.job, "sp2", "sp1")
        self.assertEqual((ok, res), (True, {"moved": 2}))
        rows = ld.read_segments(self.job)
        self.assertEqual([(r["segment_id"], r["speaker_id"], r["speaker"]) for r in rows],
                         [("a", "sp1", "Soldier"), ("b", "sp1", "Soldier"), ("c", "sp1", "Soldier"), ("d", "sp3", "Old man")])
        self.assertEqual([sp["id"] for sp in self.job["speaker_list"]], ["sp1", "sp3"])
        self.assertEqual(self.job["speaker_list"][0]["gender_source"], "chosen")      # the speaker kept keeps its gender
        saved = ld.load_job(self.job["id"]) if hasattr(ld, "load_job") else None
        if saved:
            self.assertEqual([sp["id"] for sp in saved["speaker_list"]], ["sp1", "sp3"])

    def test_what_is_refused(self):
        self.assertEqual(ld.merge_speakers(self.job, "sp2", "sp2")[1][1], 400)
        self.assertEqual(ld.merge_speakers(self.job, "sp9", "sp1")[1][1], 404)
        self.job["status"] = "dubbing"
        self.assertEqual(ld.merge_speakers(self.job, "sp2", "sp1")[1][1], 409)


class ThePage(unittest.TestCase):
    def test_wiring(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/longdub/{job_id}/speakers/merge")', main)
        page = (ROOT / "dub_long.html").read_text(encoding="utf-8")
        for needle in ("/speakers/merge", "function speakerProblem()", "spkUnsaved", 'id="spkMsg"',
                       "saveSpeakers(false).then(function (saved) {", "if (!saved)"):
            self.assertIn(needle, page)
        for key in ("spkDup", "spkJoinBtn", "spkJoinAsk", "spkEmpty", "spkNotSavedRedo"):
            self.assertEqual(len(re.findall(r"\b" + key + r":", page)), 2, key)
        ask = page.split("function onGenderChanged(")[1].split("\nfunction ")[0]
        self.assertIn("if (before && now && before === now) return;", ask)      # the same gender as before: no question, no re-translation
        redo = page.split("function redoForGender(")[1].split("\nfunction ")[0]
        self.assertIn('$("spkMsg")', redo)                     # the re-translation never hides an error about the names
        self.assertNotIn('$("spkErr")', redo)


if __name__ == "__main__":
    unittest.main()
