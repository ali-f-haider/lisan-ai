"""The per-line speaker record in the job log (speaker_trace.py). Offline, invented data."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speaker_trace as st
import speaker_vote_live as live

ROOT = Path(__file__).resolve().parents[1]


def line(i, a, b, text, spk):
    return {"segment_id": f"seg_{i}", "start": a, "end": b, "text": text, "speaker": spk}


class Pure(unittest.TestCase):
    def test_names_and_clock(self):
        self.assertEqual(st.short("Speaker 2"), "S2")
        self.assertEqual(st.short("Waitress Anna"), "Waitre")
        self.assertEqual(st.short(None), "?")
        self.assertEqual(st.clock(80.04), "1:20.0")
        self.assertEqual(st.clock(59.97), "1:00.0")

    def test_a_moved_line_and_a_split_vote_are_marked(self):
        rows = [line(3, 17.3, 20.5, "How's your face?", "Speaker 2"), line(4, 20.5, 21.0, "Fine.", "Speaker 1")]
        det = {"seg_3": "Speaker 2", "seg_4": "Speaker 2"}
        by = {"seg_3": {"by": ["app", "dialogue", "audio"], "support": 3, "present": 3},
              "seg_4": {"by": ["dialogue", "audio"], "support": 2, "present": 3}}
        stage = st.build(rows, det, by)
        summary, events = st.report(rows, stage)
        text = " // ".join(events)
        self.assertIn('seg_3 0:17.3-0:20.5 "How\'s your face?" | detector S2 | check S2 (detector+dialogue+audio, 3/3) | final S2', text)
        self.assertIn('seg_4 0:20.5-0:21.0 "Fine." | detector S2 | check S1 <- moved (dialogue+audio, 2/3) split | final S1', text)
        self.assertIn("the speaker check moved 1", summary)
        self.assertIn("1 where the voters were split", summary)

    def test_a_change_after_the_check_is_marked(self):
        stage = st.build([line(0, 1, 2, "Hi", "Speaker 1")], {"seg_0": "Speaker 1"}, {"seg_0": {"by": ["app", "dialogue", "audio"], "support": 3, "present": 3}})
        final = [line(0, 1, 2, "Hi", "Speaker 2")]
        _, events = st.report(final, stage)
        self.assertIn("| final S2 <- after", events[0])

    def test_lines_joined_by_the_clean_up_take_the_stage_with_the_most_overlap(self):
        rows = [line(0, 0.0, 2.0, "Well", "Speaker 1"), line(1, 2.0, 6.0, "I think so", "Speaker 2")]
        stage = st.build(rows, {"seg_0": "Speaker 1", "seg_1": "Speaker 2"}, {})
        final = [line(0, 0.0, 6.0, "Well I think so", "Speaker 2")]
        f = st.facts(final[0], stage)
        self.assertEqual(f["vote"], "Speaker 2")                 # 4 s of the 6 are the second line

    def test_when_the_check_did_not_run_only_the_final_speaker_is_shown(self):
        summary, events = st.report([line(0, 1, 2, "Hi", "Speaker 1")], [])
        self.assertIn("did not run", summary)
        self.assertIn("| detector - | check - | final S1", events[0])

    def test_long_videos_list_only_the_lines_that_changed(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2, f"line {i}", "Speaker 1") for i in range(100)]
        det = {r["segment_id"]: "Speaker 1" for r in rows}
        det["seg_7"] = "Speaker 2"
        stage = st.build(rows, det, {})
        summary, events = st.report(rows, stage)
        self.assertIn("rows for the 1 lines where something changed", summary)
        self.assertEqual(len(events), 1)
        self.assertIn("seg_7", events[0])

    def test_every_event_fits_the_log_and_nothing_raises(self):
        rows = [line(i, i * 2.0, i * 2.0 + 1.5, "A rather long sentence that goes on and on %d" % i, "Speaker %d" % (1 + i % 2)) for i in range(60)]
        stage = st.build(rows, {r["segment_id"]: "Speaker 1" for r in rows}, {})
        summary, events = st.report(rows, stage)
        self.assertLessEqual(len(summary), 1500)
        self.assertTrue(events)
        for e in events:
            self.assertLessEqual(len(e), 1400)
        for bad in (None, [], [None, 3], [{"start": "x", "end": None, "text": None, "speaker": None}]):
            st.report(bad, bad)                                    # no exception


class WithTheRealVote(unittest.TestCase):
    def test_apply_tells_who_backed_each_line(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2, f"t{i}", "Speaker 1" if i % 2 == 0 else "Speaker 2") for i in range(10)]
        want = {r["segment_id"]: r["speaker"] for r in rows}
        want["seg_4"] = "Speaker 2"                                # dialogue and audio both say Speaker 2 for a line the detector gave to 1
        votes = {"dialogue": dict(want), "audio": dict(want)}
        summary = live.apply(rows, votes, allow_new=False)
        self.assertEqual(summary["status"], "ok")
        by = summary["by_line"]
        self.assertEqual(set(by), {r["segment_id"] for r in rows})
        self.assertEqual(sorted(by["seg_4"]["by"]), ["audio", "dialogue"])
        self.assertEqual((by["seg_4"]["support"], by["seg_4"]["present"]), (2, 3))
        self.assertEqual(sorted(by["seg_0"]["by"]), ["app", "audio", "dialogue"])
        det = {r["segment_id"]: ("Speaker 1" if i % 2 == 0 else "Speaker 2") for i, r in enumerate(rows)}
        stage = st.build(rows, det, by)
        e = [x for x in stage if x["text"] == "t4"][0]
        self.assertEqual((e["det"], e["vote"]), ("Speaker 1", "Speaker 2"))


class RenumberingIsNotAMove(unittest.TestCase):
    """The detector found 3 voices for 2 people; the check merges one and the speakers are renumbered. Before this was handled the log
    called almost every line "moved" (the detector's S2 became the final S1) although the check had changed nothing."""

    def scene(self):
        # detector: Speaker 1 and Speaker 2 are both the man (the detector split him in two), Speaker 3 is the boy
        det = ["Speaker 2", "Speaker 3", "Speaker 1", "Speaker 3", "Speaker 2", "Speaker 3", "Speaker 1", "Speaker 3", "Speaker 2", "Speaker 3"]
        rows = [line(i, i * 3.0, i * 3.0 + 2, f"t{i}", d) for i, d in enumerate(det)]
        truth = {r["segment_id"]: ("A" if d != "Speaker 3" else "B") for r, d in zip(rows, det)}
        return rows, {r["segment_id"]: r["speaker"] for r in rows}, {"dialogue": dict(truth), "audio": dict(truth)}

    def run_it(self):
        rows, det, votes = self.scene()
        summary = live.apply(rows, votes, allow_new=True)
        kept = {r["speaker"] for r in rows}
        summary["rename"] = live.compact_names(rows)
        carried = st.carry(det, kept, summary["rename"])
        return rows, det, summary, carried

    def test_the_check_merged_one_voice_and_renumbered(self):
        rows, det, summary, carried = self.run_it()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(sorted({r["speaker"] for r in rows}), ["Speaker 1", "Speaker 2"])
        self.assertEqual(len({v for v in carried.values() if v.startswith("~")}), 1)      # exactly one detector voice was merged away

    def test_only_the_merged_voice_is_marked_and_nothing_is_called_moved(self):
        rows, det, summary, carried = self.run_it()
        stage = st.build(rows, carried, summary["by_line"])
        sm, events = st.report(rows, stage)
        text = " // ".join(events)
        self.assertIn("the speaker check moved 0", sm)
        self.assertIn("merged into another", sm)
        self.assertIn("*", text)
        self.assertNotIn("<- moved", text)

    def test_the_old_way_would_have_called_them_moved(self):
        rows, det, summary, _ = self.run_it()
        stage = st.build(rows, det, summary["by_line"])         # the detector's raw names, not carried over
        sm, _ = st.report(rows, stage)
        self.assertNotIn("the speaker check moved 0", sm)

    def test_a_real_move_is_still_a_move(self):
        rows = [line(i, i * 3.0, i * 3.0 + 2, f"t{i}", "Speaker 1" if i % 2 == 0 else "Speaker 2") for i in range(10)]
        det = {r["segment_id"]: r["speaker"] for r in rows}
        want = dict(det)
        want["seg_4"] = "Speaker 2"
        summary = live.apply(rows, {"dialogue": dict(want), "audio": dict(want)}, allow_new=False)
        kept = {r["speaker"] for r in rows}
        carried = st.carry(det, kept, live.compact_names(rows))
        sm, events = st.report(rows, st.build(rows, carried, summary["by_line"]))
        self.assertIn("the speaker check moved 1", sm)
        self.assertEqual(" // ".join(events).count("<- moved"), 1)

    def test_names_and_edge_cases(self):
        self.assertEqual(st.short("~Speaker 3"), "S3*")
        self.assertEqual(st.carry({"a": None, "b": "Speaker 2"}, {"Speaker 2"}, {"Speaker 2": "Speaker 1"}), {"a": None, "b": "Speaker 1"})
        self.assertEqual(st.carry({"a": "Speaker 1"}, {"Speaker 2"}, None), {"a": "~Speaker 1"})
        self.assertEqual(st.carry(None, None, None), {})


class Wiring(unittest.TestCase):
    def test_the_analysis_writes_the_trace_after_the_review(self):
        src = (ROOT / "longdub_service.py").read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertIn("import speaker_trace\n", src)
        i = src.index("_review_speakers(job, rows, turns, label_map, vote=vote)")
        j = src.index("_write_segments(job, rows)", i)
        self.assertIn('_ev(job, "speaker_trace", "info", _t_sum)', src[j:j + 700])
        self.assertIn('_ev(job, "speaker_trace_lines", "info", _t_row)', src[j:j + 700])
        self.assertIn("_det = speaker_trace.snapshot(rows)", src)
        self.assertIn('summary["trace"] = speaker_trace.build(rows, speaker_trace.carry(_det, _kept, summary.get("rename")), summary.get("by_line"))', src)
        self.assertIn('_kept = {r["speaker"] for r in rows}', src)


if __name__ == "__main__":
    unittest.main()
