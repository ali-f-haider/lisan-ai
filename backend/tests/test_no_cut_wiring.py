"""The long dub never cuts a generated line: the pass that moves/rewords/speeds lines, and the timeline that uses its answer.
Offline, invented data, no audio files."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import longdub_service as ld


def row(i, a, b, speaker="s1"):
    return {"segment_id": f"seg_{i}", "start": a, "end": b, "speaker_id": speaker, "arabic_text": "كلمة " * 6, "text": "hello", "emotion": "neutral"}


def meta(i, start, dur, tempo=1.0):
    return {"seg": f"seg_{i}", "start": start, "dur": dur, "tempo": tempo, "raw": dur, "slot": 1.0, "room": 1.0, "warn": False}


def item(seg, start, dur):
    return {"seg": seg, "start": start, "dur": dur, "gain_db": 0.0}


class PlanTimelineTests(unittest.TestCase):
    def test_without_a_plan_the_old_behaviour_is_kept(self):
        kept, chunks, dropped = ld._plan_timeline([item("a", 1.0, 2.0), item("b", 2.5, 1.0)], 10.0)
        self.assertTrue(kept[0]["trim"])
        self.assertFalse(kept[1]["trim"])

    def test_a_plan_moves_the_lines_and_nothing_is_trimmed(self):
        lines = [item("a", 1.0, 1.7), item("b", 2.5, 1.0)]
        kept, chunks, dropped = ld._plan_timeline(lines, 10.0, {"a": 0.9, "b": 2.7})
        self.assertFalse(any(k["trim"] for k in kept))
        self.assertAlmostEqual(kept[0]["start"], 0.9)
        self.assertAlmostEqual(kept[0]["orig_start"], 1.0)

    def test_an_allowed_overlap_is_played_not_cut(self):
        lines = [item("a", 1.0, 1.7), item("b", 2.5, 1.0)]
        kept, _c, _d = ld._plan_timeline(lines, 10.0, None, {"a": 0.4})
        self.assertFalse(kept[0]["trim"])
        self.assertAlmostEqual(kept[0]["allowed"], 1.7)

    def test_a_stretch_never_starts_inside_a_line_that_is_still_playing(self):
        lines = [item("a", 0.0, 50.0), item("b", 46.0, 1.0), item("c", 60.0, 1.0)]
        kept, chunks, _d = ld._plan_timeline(lines, 100.0, None, {"a": 5.0})
        for g, s0, s1 in chunks:
            for it in g:
                self.assertLessEqual(it["start"] + it["allowed"], s1 / float(ld.SAMPLE_RATE) + 1e-6)


class PassTests(unittest.TestCase):
    def run_pass(self, rows, metas, total=30.0, rephrase=None, speed=None):
        dub = {"lines": {m["seg"]: dict(m) for m in metas}, "rephrased": {}}
        events = []
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(ld, "_ev", lambda j, step, status="ok", detail="", *a, **k: events.append((step, status, detail))), \
                mock.patch.object(ld, "_rephrase_to_fit", rephrase or (lambda *a, **k: (a[3], None))), \
                mock.patch.object(ld, "_speed_up_fit", speed or (lambda *a, **k: None)):
            plan = ld._no_cut_pass({"id": "j"}, dub, rows, total, lambda spid: "v", Path(td) / "x.wav", Path(td), {})
        return plan, dub, events

    def test_lines_that_fit_are_left_alone(self):
        rows = [row(0, 1, 2), row(1, 4, 5)]
        plan, dub, ev = self.run_pass(rows, [meta(0, 1, 1.0), meta(1, 4, 1.0)])
        self.assertTrue(plan["ok"])
        self.assertEqual(dub["place"], {"seg_0": 1.0, "seg_1": 4.0})
        self.assertEqual(ev[0][0], "no_cut_plan")

    def test_a_small_overrun_is_solved_by_moving_not_by_rewriting(self):
        rows = [row(0, 1, 2), row(1, 3, 4)]
        called = []
        plan, dub, ev = self.run_pass(rows, [meta(0, 1, 2.2), meta(1, 3, 1.0)],
                                      rephrase=lambda *a, **k: called.append(1) or (a[3], None))
        self.assertTrue(plan["ok"])
        self.assertEqual(called, [])
        self.assertGreater(dub["place"]["seg_1"], 3.0)

    def test_a_crowded_scene_gets_shorter_wording_even_for_short_lines(self):
        rows = [row(0, 1, 2), row(1, 2.2, 3.2), row(2, 3.4, 4.4)]
        metas = [meta(0, 1, 2.5), meta(1, 2.2, 2.5), meta(2, 3.4, 2.5)]
        seen = []

        def reph(job, r, tag, m, slot, room, voice, loud, d, cap=None, gap=None, min_letters=None):
            seen.append(min_letters)
            m2 = dict(m)
            m2["dur"] = round(room - 0.05, 3)
            return m2, "نص أقصر"

        plan, dub, ev = self.run_pass(rows, metas, rephrase=reph)
        self.assertTrue(plan["ok"])
        self.assertTrue(seen and all(x == ld.NOCUT_MIN_LETTERS for x in seen))
        self.assertTrue(dub["rephrased"])
        self.assertTrue(all(v["before"] for v in dub["rephrased"].values()))

    def test_the_final_lengths_reach_the_mix(self):
        rows = [row(0, 1, 2), row(1, 2.2, 3.2), row(2, 3.4, 4.4)]
        metas = [meta(0, 1, 2.5), meta(1, 2.2, 2.5), meta(2, 3.4, 2.5)]

        def reph(job, r, tag, m, slot, room, *a, **k):
            m2 = dict(m)
            m2["dur"] = round(room - 0.05, 3)
            return m2, "نص أقصر"

        plan, dub, ev = self.run_pass(rows, metas, rephrase=reph)
        for sid, m in dub["lines"].items():
            self.assertAlmostEqual(m["dur"], plan["durs"][sid], places=2)


class SourceChecks(unittest.TestCase):
    def test_the_pass_runs_before_the_voices_are_levelled(self):
        src = (Path(ld.__file__)).read_text(encoding="utf-8")
        self.assertLess(src.index("_no_cut_pass(job, dub, rows"), src.index("        _level_voices(job, dub, rows)"))
        self.assertIn('_plan_timeline(items, total, dub.get("place"), dub.get("overlap"))', src)


if __name__ == "__main__":
    unittest.main()
