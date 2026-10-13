"""A generated line that still ends up well under its target level is reported with its cause (the gain limit or the peak limit).

Measuring only: none of these tests, and none of the new code, changes what gain a line gets."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_level as vl
import longdub_service as ld


def old_gain(o_db, d_db, d_peak=None, anchor=None, gain_max=8.0, peak_ceil=-1.0):
    """voice_gain exactly as it was written before the shortfall report was added."""
    if o_db is None or d_db is None:
        return 0.0
    target = float(o_db)
    if anchor is not None:
        target = float(anchor) + max(-vl.LEVEL_SPREAD_DB, min(vl.LEVEL_SPREAD_DB, vl.LEVEL_FOLLOW * (float(o_db) - float(anchor))))
    g = max(-gain_max, min(gain_max, target - float(d_db)))
    if d_peak is not None and peak_ceil is not None:
        g = min(g, peak_ceil - float(d_peak))
    return g


class GainIsUnchangedTests(unittest.TestCase):
    def test_voice_gain_matches_the_old_formula_everywhere(self):
        n = 0
        for o in range(-60, -9, 4):
            for d in range(-60, -9, 4):
                for peak in (None, d + 6, d + 14, -3.0, 0.5):
                    for anchor in (None, -40.0, -25.0):
                        for gm, pc in ((8.0, -1.0), (10.0, None), (4.0, -6.0)):
                            self.assertEqual(vl.voice_gain(o, d, peak, anchor, gm, pc), old_gain(o, d, peak, anchor, gm, pc))
                            n += 1
        self.assertGreater(n, 5000)


class ShortfallTests(unittest.TestCase):
    def test_a_line_that_reaches_its_target_has_no_shortfall(self):
        self.assertEqual(vl.shortfall(-30, -36, d_peak=-20), (0.0, None))

    def test_a_line_that_is_louder_than_its_target_has_no_shortfall(self):
        self.assertEqual(vl.shortfall(-50, -30, d_peak=-20), (0.0, None))
        self.assertEqual(vl.shortfall(-50, -10, d_peak=-5), (0.0, None))       # turned down, even when the cut is limited

    def test_missing_levels_are_not_reported(self):
        self.assertEqual(vl.shortfall(None, -30), (0.0, None))
        self.assertEqual(vl.shortfall(-30, None), (0.0, None))

    def test_the_gain_limit_is_named(self):
        miss, limit = vl.shortfall(-22, -40, d_peak=-30)          # wants +18, may only have +8
        self.assertEqual(limit, "gain")
        self.assertAlmostEqual(miss, 10.0)

    def test_the_peak_limit_is_named(self):
        miss, limit = vl.shortfall(-22, -30, d_peak=-4)           # wants +8, but the peak is already at -4 dB: only +3
        self.assertEqual(limit, "peak")
        self.assertAlmostEqual(miss, 5.0)

    def test_a_line_already_over_the_peak_ceiling_is_a_peak_case(self):
        miss, limit = vl.shortfall(-22, -30, d_peak=1.0)
        self.assertEqual(limit, "peak")
        self.assertGreater(miss, 8.0)

    def test_the_peak_is_ignored_when_there_is_no_ceiling(self):
        self.assertEqual(vl.shortfall(-22, -30, d_peak=-4, peak_ceil=None), (0.0, None))
        miss, limit = vl.shortfall(-10, -30, d_peak=-4, gain_max=10.0, peak_ceil=None)
        self.assertEqual(limit, "gain")
        self.assertAlmostEqual(miss, 10.0)

    def test_the_target_follows_the_speakers_usual_level(self):
        # the original is far above the usual level, but the dub only follows LEVEL_FOLLOW of that, never more than the spread
        miss, limit = vl.shortfall(-10, -45, d_peak=-30, anchor=-30.0)
        target = -30.0 + min(vl.LEVEL_SPREAD_DB, vl.LEVEL_FOLLOW * 20.0)
        self.assertEqual(limit, "gain")
        self.assertAlmostEqual(miss, (target + 45.0) - 8.0)


class ShortfallsTests(unittest.TestCase):
    def items(self):
        quiet = [{"key": f"ok{i}", "speaker": "s1", "orig": -30.0 + (i % 3), "dub": -33.0, "peak": -15.0} for i in range(8)]
        return quiet + [
            {"key": "cap", "speaker": "s1", "orig": -30.0, "dub": -48.0, "peak": -30.0},
            {"key": "peak", "speaker": "s1", "orig": -30.0, "dub": -36.0, "peak": -4.0},
            {"key": "loud", "speaker": "s1", "orig": -30.0, "dub": -20.0, "peak": -5.0},
            {"key": "nolevel", "speaker": "s1", "orig": None, "dub": -40.0},
        ]

    def test_only_the_lines_well_under_target_are_listed(self):
        short = vl.shortfalls(self.items())
        self.assertEqual(sorted(short), ["cap", "peak"])
        self.assertEqual(short["cap"]["limit"], "gain")
        self.assertEqual(short["peak"]["limit"], "peak")
        self.assertGreater(short["cap"]["miss"], short["peak"]["miss"])

    def test_the_threshold_can_be_changed(self):
        self.assertEqual(vl.shortfalls(self.items(), min_miss=50.0), {})

    def test_the_gains_are_the_same_with_or_without_the_report(self):
        items = self.items()
        gains, anchors, stats = vl.level_lines(items)
        for it in items:
            if it["orig"] is not None:
                expected = old_gain(it["orig"], it["dub"], it.get("peak"), vl.speaker_anchors({"s1": [i["orig"] for i in items]})["s1"])
                self.assertEqual(gains[it["key"]], expected, it["key"])


class StatsAndTextTests(unittest.TestCase):
    def run_lines(self, items):
        return vl.level_lines(items)[2]

    def items(self, extra=()):
        base = [{"key": f"a{i}", "speaker": "s1", "orig": -30.0, "dub": -31.0, "peak": -15.0} for i in range(6)]
        return base + list(extra)

    def test_stats_count_the_lines_and_their_causes(self):
        stats = self.run_lines(self.items([
            {"key": "x", "speaker": "s1", "orig": -30.0, "dub": -50.0, "peak": -30.0},
            {"key": "y", "speaker": "s1", "orig": -30.0, "dub": -36.0, "peak": -3.0},
        ]))["s1"]
        self.assertEqual(stats["under_target"], 2)
        self.assertEqual(stats["under_by_gain"], 1)
        self.assertEqual(stats["under_by_peak"], 1)
        self.assertGreater(stats["worst_miss"], 3.0)

    def test_the_log_line_mentions_them(self):
        stats = self.run_lines(self.items([{"key": "x", "speaker": "s1", "orig": -30.0, "dub": -50.0, "peak": -30.0}]))
        text = vl.describe(stats, {"s1": "Judge"})
        self.assertIn("Judge", text)
        self.assertIn("1 stay more than 3 dB under their target", text)
        self.assertIn("held by the gain limit", text)

    def test_the_log_line_is_exactly_as_before_when_nothing_is_under_target(self):
        stats = self.run_lines(self.items())
        self.assertEqual(stats["s1"]["under_target"], 0)
        self.assertNotIn("under their target", vl.describe(stats))
        self.assertTrue(vl.describe(stats).endswith("median change +1.0 dB)"))

    def test_describe_copes_with_stats_from_before_the_report_existed(self):
        old = {"s1": {"lines": 5, "anchor": -30.0, "orig_spread": 1.0, "dub_spread": 1.0, "exact_spread": 1.0, "at_limit": 0,
                      "up_at_limit": 0, "down_at_limit": 0, "median_gain": 0.0}}
        self.assertNotIn("under their target", vl.describe(old))

    def test_the_summary_lists_the_worst_first_and_trims_a_long_list(self):
        short = {f"k{i}": {"miss": 3.0 + i, "limit": "gain" if i % 2 else "peak"} for i in range(14)}
        text = vl.describe_shortfalls(short, {"k13": "2:31 Judge"}, most=3)
        self.assertTrue(text.startswith("14 line(s) end up more than 3 dB under their target level: 2:31 Judge 16.0 dB under (gain limit); k12 15.0 dB under (peak limit)"))
        self.assertTrue(text.endswith(", and 11 more"))
        self.assertEqual(vl.describe_shortfalls({}), "")


class LongDubWiringTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.saved = ld._ev
        ld._ev = lambda job, step, status, detail, **k: self.events.append((step, status, detail))
        self.job = {"speaker_list": [{"id": "s1", "name": "Judge"}]}
        self.rows = [{"segment_id": f"seg{i}", "speaker_id": "s1", "start": 60.0 * i + 31} for i in range(8)]
        self.lines = {f"seg{i}": {"original_mean_db": -30.0, "dub_level_db": -31.0, "dub_peak_db": -15.0} for i in range(6)}
        self.lines["seg6"] = {"original_mean_db": -30.0, "dub_level_db": -50.0, "dub_peak_db": -30.0}     # needs far more than the limit
        self.lines["seg7"] = {"original_mean_db": -30.0, "dub_level_db": -36.0, "dub_peak_db": -3.0}      # the peak stops the boost
        self.dub = {"lines": self.lines}

    def tearDown(self):
        ld._ev = self.saved

    def test_the_cause_is_kept_on_the_line_and_logged(self):
        ld._level_voices(self.job, self.dub, self.rows)
        self.assertEqual(self.lines["seg6"]["level_limit"], "gain")
        self.assertEqual(self.lines["seg7"]["level_limit"], "peak")
        self.assertGreater(self.lines["seg6"]["level_miss_db"], self.lines["seg7"]["level_miss_db"])
        self.assertNotIn("level_limit", self.lines["seg0"])
        steps = [e[0] for e in self.events]
        self.assertEqual(steps, ["voice_levels", "voice_level_shortfalls"])
        text = self.events[1][2]
        self.assertIn("6:31 Judge", text)          # the line at 6 minutes 31 seconds
        self.assertIn("7:31 Judge", text)
        self.assertIn("gain limit", text)
        self.assertIn("peak limit", text)

    def test_the_gains_are_what_they_were_before(self):
        ld._level_voices(self.job, self.dub, self.rows)
        items = [{"key": k, "speaker": "s1", "orig": m["original_mean_db"], "dub": m["dub_level_db"], "peak": m["dub_peak_db"]} for k, m in self.lines.items()]
        anchor = vl.speaker_anchors({"s1": [i["orig"] for i in items]})["s1"]
        for it in items:
            self.assertEqual(self.lines[it["key"]]["gain_db"], round(old_gain(it["orig"], it["dub"], it["peak"], anchor, ld.GAIN_MAX_DB, ld.PEAK_CEIL_DB), 1))

    def test_nothing_is_logged_when_every_line_reaches_its_target(self):
        for k in ("seg6", "seg7"):
            self.lines[k].update(dub_level_db=-31.0, dub_peak_db=-15.0)
        ld._level_voices(self.job, self.dub, self.rows)
        self.assertEqual([e[0] for e in self.events], ["voice_levels"])
        self.assertFalse(any("level_limit" in m for m in self.lines.values()))

    def test_old_marks_are_cleared_when_the_lines_are_levelled_again(self):
        ld._level_voices(self.job, self.dub, self.rows)
        self.assertIn("level_limit", self.lines["seg6"])
        self.lines["seg6"].update(dub_level_db=-31.0, dub_peak_db=-15.0)       # the line was generated again, louder
        ld._level_voices(self.job, self.dub, self.rows)
        self.assertNotIn("level_limit", self.lines["seg6"])
        self.assertNotIn("level_miss_db", self.lines["seg6"])

    def test_a_row_without_a_time_is_named_by_its_id(self):
        self.rows[6].pop("start")
        ld._level_voices(self.job, self.dub, self.rows)
        self.assertIn("seg6 Judge", self.events[1][2])

    def test_garbage_still_never_raises(self):
        ld._level_voices({}, {"lines": None}, self.rows)


if __name__ == "__main__":
    unittest.main()
