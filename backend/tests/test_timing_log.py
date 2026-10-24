"""The per-line timing report written into the long dub's job log (timing_log.py)."""
import os
import re
import unittest

import timing_log as tl

BACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def line(seg, o0, o1, start=None, allowed=None, tempo=1.0, raw=None, trim=False):
    start = o0 if start is None else start
    allowed = (o1 - o0) if allowed is None else allowed
    return {"seg": seg, "orig_start": o0, "start": start, "end": o1, "allowed": allowed, "dur": allowed,
            "tempo": tempo, "raw": raw if raw is not None else allowed * tempo, "trim": trim}


class Clock(unittest.TestCase):
    def test_format(self):
        self.assertEqual(tl.clock(0), "0:00.0")
        self.assertEqual(tl.clock(49.1), "0:49.1")
        self.assertEqual(tl.clock(125.46), "2:05.5")
        self.assertEqual(tl.clock(59.96), "1:00.0")          # no "0:60.0"
        self.assertEqual(tl.clock(None), "0:00.0")


class Facts(unittest.TestCase):
    def test_plain_line_has_no_flags(self):
        f = tl.facts(line("seg_1", 10.0, 12.0))
        self.assertEqual(f["flags"], [])
        self.assertAlmostEqual(f["late"], 0.0)

    def test_fast_cut_moved_late_reworded(self):
        it = line("seg_2", 20.0, 21.0, start=20.3, allowed=1.6, tempo=1.25, raw=2.0, trim=True)
        f = tl.facts(it, {"seg_2": {}})
        self.assertIn("cut", f["flags"])
        self.assertIn("fast", f["flags"])
        self.assertIn("late", f["flags"])
        self.assertIn("reworded", f["flags"])
        self.assertTrue(any(x.startswith("moved +0.30") for x in f["flags"]))
        self.assertAlmostEqual(f["late"], 0.9, places=2)

    def test_natural_length_falls_back_to_dub_length_times_speed(self):
        it = line("seg_3", 0, 2)
        del it["raw"]
        it["dur"], it["tempo"] = 1.6, 1.25
        self.assertAlmostEqual(tl.facts(it)["natural"], 2.0)

    def test_bad_numbers_never_raise(self):
        f = tl.facts({"seg": None, "start": "x", "end": float("nan"), "tempo": None, "allowed": "?"})
        self.assertEqual(f["sid"], "")
        self.assertEqual(f["tempo"], 1.0)


class Row(unittest.TestCase):
    def test_row_text(self):
        it = line("seg_15", 49.1, 50.7, tempo=1.25, raw=1.9)
        r = tl.row(tl.facts(it, {"seg_15": {}}))
        self.assertEqual(r, "seg_15 0:49.1-0:50.7 > 0:49.1-0:50.7, x1.25, natural 1.9s for 1.6s, ends +0.0s, [fast, reworded]")


class Report(unittest.TestCase):
    def test_empty(self):
        s, st, rows = tl.report([])
        self.assertEqual(rows, [])
        self.assertEqual(st, "info")

    def test_small_job_lists_every_line(self):
        kept = [line("seg_%d" % i, i * 4.0, i * 4.0 + 2.0) for i in range(40)]
        kept[5] = line("seg_5", 20.0, 22.0, tempo=1.25, raw=2.5)
        s, st, rows = tl.report(kept, {})
        self.assertEqual(st, "ok")
        text = " | ".join(rows)
        self.assertEqual(len(re.findall(r"seg_\d+ ", text)), 40)
        self.assertIn("40 lines", s)
        self.assertIn("1 at x1.2 or more", s)
        self.assertIn("a row for every line", s)

    def test_cut_makes_the_summary_partial(self):
        kept = [line("a", 0, 2), line("b", 3, 4, allowed=0.8, tempo=1.25, raw=1.5, trim=True)]
        s, st, rows = tl.report(kept)
        self.assertEqual(st, "partial")
        self.assertIn("1 cut short", s)

    def test_long_job_lists_only_flagged_lines_worst_first_capped(self):
        kept = []
        for i in range(400):
            kept.append(line("seg_%d" % i, i * 5.0, i * 5.0 + 3.0))
        for i in range(0, 400, 2):            # 200 fast lines of different speeds
            kept[i] = line("seg_%d" % i, i * 5.0, i * 5.0 + 3.0, tempo=1.2 + (i % 20) / 100.0, raw=3.6)
        kept[7] = line("seg_7", 35.0, 38.0, tempo=1.25, raw=4.5, trim=True, allowed=2.9)       # the worst: a cut line
        s, st, rows = tl.report(kept)
        text = " | ".join(rows)
        self.assertEqual(st, "partial")
        self.assertEqual(len(re.findall(r"seg_\d+ ", text)), tl.MAX_ROWS)
        self.assertIn("seg_7 ", text)                              # the cut line is always among them
        self.assertIn("most notable of the 201 lines with a flag", s)
        self.assertNotIn("seg_1 ", text)                           # an ordinary line is not listed
        starts = [float(m.group(1)) * 60 + float(m.group(2)) for m in re.finditer(r"seg_\d+ (\d+):(\d+\.\d)-", text)]
        self.assertEqual(starts, sorted(starts))                   # rows come in video order

    def test_every_event_fits_the_log(self):
        kept = [line("seg_%d" % i, i * 5.0, i * 5.0 + 3.0, tempo=1.25, raw=4.0) for i in range(300)]
        s, st, rows = tl.report(kept, {"seg_%d" % i: {} for i in range(300)})
        self.assertLessEqual(len(s), 1500)
        self.assertTrue(rows)
        self.assertTrue(all(len(r) <= 1500 for r in rows))
        self.assertLessEqual(len(rows), 8)

    def test_summary_numbers(self):
        kept = [line("a", 0, 2), line("b", 3, 5, tempo=1.25, raw=2.5), line("c", 6, 8, start=6.3, allowed=2.0)]
        s, st, rows = tl.report(kept)
        self.assertIn("highest x1.25", s)
        self.assertIn("2 left at normal speed", s)
        self.assertIn("1 moved by 0.15s or more (up to 0.30s)", s)


class Wiring(unittest.TestCase):
    def test_mix_step_writes_the_events_and_cannot_break_the_job(self):
        with open(os.path.join(BACK, "longdub_service.py"), encoding="utf-8") as f:
            src = f.read().replace("\r\n", "\n")
        i = src.index("import timing_log")
        block = src[i - 60:i + 520]
        self.assertIn("timing_log.report(kept, dub.get(\"rephrased\") or {})", block)
        self.assertIn('_ev(job, "timing_summary"', block)
        self.assertIn('_ev(job, "timing_lines"', block)
        self.assertIn("except Exception as ex_t", block)
        # it sits right after the existing timing_report event, inside the same mix step
        self.assertLess(src.index('"timing_report"'), i)
        self.assertLess(i, src.index("# 3b. optional lip-sync"))


if __name__ == "__main__":
    unittest.main()
