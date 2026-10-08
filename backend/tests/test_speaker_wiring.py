"""The long dub's "check speaker" evidence: attached once after the lines are built, never changes a speaker, is dropped when the
user changes the line, and reaches the editor only as a known confidence + a fixed list of reasons. Offline, invented data."""
import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import longdub_service as ld

ROOT = Path(__file__).resolve().parents[1]


def row(i, a, b, speaker="Speaker 1", text="we can start the next part now", **kw):
    r = {"segment_id": f"seg_{i}", "start": a, "end": b, "speaker": speaker, "text": text,
         "words": [{"word": "we", "start": a, "end": b}]}
    r.update(kw)
    return r


TURNS = [{"start": 0, "end": 3, "speaker": "raw_a"}, {"start": 3, "end": 6, "speaker": "raw_b"}]
MAP = {"raw_a": "Speaker 1", "raw_b": "Speaker 2"}


class ReviewTests(unittest.TestCase):
    def run_review(self, rows, turns=TURNS, stated=2):
        job = {"id": "j", "stated_speakers": stated}
        events = []
        with mock.patch.object(ld, "_ev", lambda j, step, status="ok", detail="", **k: events.append((step, status, detail))):
            n = ld._review_speakers(job, rows, turns, MAP)
        return job, events, n

    def test_fields_are_added_and_speakers_never_change(self):
        rows = [row(0, 0, 3, "Speaker 1"), row(1, 3, 3.5, "Speaker 2", text="Okay.")]
        before = [r["speaker"] for r in rows]
        job, events, n = self.run_review(rows)
        self.assertEqual([r["speaker"] for r in rows], before)
        self.assertEqual(rows[0]["speaker_confidence"], "high")
        self.assertEqual(rows[1]["speaker_confidence"], "low")
        self.assertIn("short_reply", rows[1]["speaker_reasons"])
        self.assertEqual(n, 1)
        self.assertEqual(events[0][0], "speaker_check")
        self.assertEqual(events[0][1], "partial")
        self.assertNotIn("Okay", events[0][2])             # counts and reasons only, never the dialogue

    def test_count_mismatch_makes_one_plain_warning_and_no_change_to_the_count(self):
        job, _, _ = self.run_review([row(0, 0, 3)], stated=3)
        self.assertEqual(job["speaker_count_review"], {"stated": 3, "detected": 2, "needs_review": True})
        self.assertEqual(len(job["warnings"]), 1)
        self.assertIn("2 speakers", job["warnings"][0])
        self.assertIn("you said 3", job["warnings"][0])

    def test_matching_count_adds_no_warning(self):
        job, _, _ = self.run_review([row(0, 0, 3)], stated=2)
        self.assertFalse(job.get("warnings"))

    def test_no_turns_means_no_evidence_and_old_fields_are_removed(self):
        rows = [row(0, 0, 3, speaker_confidence="low", speaker_reasons=["short_reply"], speaker_time_coverage=0.1)]
        _, events, n = self.run_review(rows, turns=[])
        self.assertEqual(n, 0)
        self.assertFalse(any(k in rows[0] for k in ld.SPEAKER_REVIEW_FIELDS))
        self.assertEqual(events, [])

    def test_a_failure_in_the_helper_never_stops_the_analysis(self):
        with mock.patch.dict(sys.modules, {"speaker_quality": None}):
            self.assertEqual(ld._review_speakers({"id": "j"}, [row(0, 0, 3)], TURNS, MAP), 0)

    def test_analysis_attaches_the_evidence_after_the_speakers_exist_and_before_saving(self):
        src = inspect.getsource(ld._run_analysis)
        a = src.index("_init_speakers(job, rows)")
        b = src.index("_review_speakers(job, rows, turns, label_map)")
        c = src.index("_write_segments(job, rows)", b)
        self.assertLess(a, b)
        self.assertLess(b, c)
        self.assertLess(c - b, 120)


class EditsDropStaleEvidenceTests(unittest.TestCase):
    def stale(self, **kw):
        return row(1, 0, 3, speaker_confidence="low", speaker_reasons=["short_reply"], speaker_time_coverage=0.2, **kw)

    def test_changing_the_speaker_drops_the_badge_data(self):
        rows = [self.stale(speaker_id="sp1")]
        job = {"id": "j", "status": "editing", "speaker_list": [{"id": "sp1", "name": "A"}, {"id": "sp2", "name": "B"}]}
        with mock.patch.object(ld, "read_segments", return_value=rows), mock.patch.object(ld, "_write_segments"), mock.patch.object(ld, "_save"), \
                mock.patch.object(ld, "_ev"):
            ld.update_segments(job, [{"segment_id": "seg_1", "speaker_id": "sp2"}])
        self.assertFalse(any(k in rows[0] for k in ld.SPEAKER_REVIEW_FIELDS))

    def test_editing_only_the_text_keeps_it(self):
        rows = [self.stale(speaker_id="sp1")]
        job = {"id": "j", "status": "editing", "speaker_list": [{"id": "sp1", "name": "A"}]}
        with mock.patch.object(ld, "read_segments", return_value=rows), mock.patch.object(ld, "_write_segments"), mock.patch.object(ld, "_save"), \
                mock.patch.object(ld, "_ev"):
            ld.update_segments(job, [{"segment_id": "seg_1", "text": "new words"}])
        self.assertEqual(rows[0]["speaker_confidence"], "low")

    def test_moving_a_line_in_time_and_splitting_it_drop_the_data(self):
        self.assertIn("_clear_speaker_review(r)", inspect.getsource(ld.set_line_time))
        self.assertIn("_clear_speaker_review(first)", inspect.getsource(ld.split_line))
        self.assertIn("_clear_speaker_review(second)", inspect.getsource(ld.split_line))


class PublicProjectionAndEditorTests(unittest.TestCase):
    def main_src(self):
        return (ROOT / "main.py").read_text(encoding="utf-8")

    def test_the_projection_is_an_allow_list(self):
        src = self.main_src()
        fn = src[src.index("def _ld_speaker_review"):src.index("def _ld_public_rows")]
        reasons = src[src.index("_LD_SPEAKER_REASONS = ("):src.index("def _ld_speaker_review")]
        scope = {}
        exec(reasons + fn, scope)
        f = scope["_ld_speaker_review"]
        self.assertEqual(f({}), {})
        self.assertEqual(f({"speaker_confidence": "certain"}), {})
        out = f({"speaker_confidence": "low", "speaker_reasons": ["short_reply", "secret_path", "x" * 300], "speaker_time_coverage": 7})
        self.assertEqual(out, {"speaker_confidence": "low", "speaker_reasons": ["short_reply"], "speaker_time_coverage": 1.0})
        self.assertEqual(f({"speaker_confidence": "high", "speaker_time_coverage": "bad"})["speaker_time_coverage"], 0.0)
        self.assertIn("d.update(_ld_speaker_review(r))", src)

    def test_both_editors_show_the_badge_only_for_low_and_use_text_nodes(self):
        for name in ("dub_long_edit.js", "dub_long.html"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("ld-spk-check", text, name)
            self.assertIn("speaker_confidence", text, name)
            self.assertIn("راجع المتحدث", text, name)
            self.assertIn("Check speaker", text, name)
        css = (ROOT / "longdub_editor.css").read_text(encoding="utf-8")
        self.assertIn(".ld-spk-check", css)


if __name__ == "__main__":
    unittest.main()
