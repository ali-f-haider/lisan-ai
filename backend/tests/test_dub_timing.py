"""dub_timing: no generated line is ever cut; the gentlest fix is tried first. Invented data only."""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dub_timing as dt


def L(*pairs):
    return [{"seg": "s%d" % i, "start": a, "dur": b} for i, (a, b) in enumerate(pairs)]


def ends_ok(plan, lines, gap=dt.GAP):
    seq = sorted(lines, key=lambda x: x["start"])
    for a, b in zip(seq, seq[1:]):
        ov = plan["overlap"].get(a["seg"], 0.0)
        end = plan["starts"][a["seg"]] + plan["durs"][a["seg"]] - ov
        if end + gap > plan["starts"][b["seg"]] + 1e-6:
            return False
    return True


class SolveTests(unittest.TestCase):
    def test_lines_that_fit_stay_where_they_are(self):
        lines = L((1.0, 1.0), (3.0, 1.5), (6.0, 0.5))
        p = dt.make_plan(lines, 10.0)
        self.assertTrue(p["ok"])
        self.assertEqual([round(p["starts"]["s%d" % i], 3) for i in range(3)], [1.0, 3.0, 6.0])
        self.assertEqual(p["moved"], 0)

    def test_a_long_line_pushes_the_next_one_a_little(self):
        lines = L((1.0, 2.2), (3.0, 1.0))       # first ends at 3.2, next must start 3.25 (0.25 late)
        p = dt.make_plan(lines, 10.0)
        self.assertTrue(p["ok"])
        self.assertTrue(ends_ok(p, lines))
        self.assertEqual(p["rephrased"], [])
        self.assertLessEqual(p["max_move"], dt.LAG + 1e-6)

    def test_a_long_line_may_start_early_into_the_silence_before_it(self):
        lines = L((1.0, 0.5), (5.0, 2.2), (7.0, 1.0))      # a pause before line 2, the next one is close behind
        p = dt.make_plan(lines, 20.0)
        self.assertTrue(p["ok"])
        self.assertTrue(ends_ok(p, lines))
        self.assertLessEqual(p["starts"]["s1"], 5.0 + 1e-6)

    def test_the_push_stops_at_the_next_pause(self):
        lines = L((1.0, 1.3), (2.0, 1.2), (3.0, 0.9), (10.0, 1.0))
        p = dt.make_plan(lines, 20.0)
        self.assertTrue(p["ok"])
        self.assertAlmostEqual(p["starts"]["s3"], 10.0, places=3)

    def test_the_last_line_has_to_end_inside_the_video(self):
        lines = L((1.0, 1.0), (8.5, 2.0))
        p = dt.make_plan(lines, 10.0)
        self.assertTrue(p["ok"])
        self.assertLessEqual(p["starts"]["s1"] + 2.0, 10.0 + 1e-6)


class LadderTests(unittest.TestCase):
    def test_rephrase_is_asked_before_anything_wider(self):
        lines = L((1.0, 2.5), (2.2, 2.5), (3.4, 2.5))        # far too tight for shifting alone
        asked = []

        def cb(stage, seg, target):
            asked.append((stage, seg))
            return target if stage == "rephrase" else None

        p = dt.make_plan(lines, 20.0, cb)
        self.assertTrue(p["ok"])
        self.assertTrue(p["rephrased"])
        self.assertEqual(asked[0][0], "rephrase")
        self.assertFalse(p["widened"])
        self.assertTrue(ends_ok(p, lines))

    def test_speed_is_the_next_step_when_rephrasing_gives_nothing(self):
        lines = L((1.0, 2.5), (2.2, 2.5), (3.4, 2.5))
        stages = []

        def cb(stage, seg, target):
            stages.append(stage)
            return target if stage == "speed" else None

        p = dt.make_plan(lines, 20.0, cb)
        self.assertTrue(p["ok"])
        self.assertTrue(p["sped"])
        self.assertIn("rephrase", stages)

    def test_overlap_is_the_last_resort_and_is_bounded(self):
        lines = L((1.0, 1.3), (1.2, 1.0))            # two voices that nothing can separate
        p = dt.make_plan(lines, 6.0, None)
        self.assertTrue(p["widened"])
        for v in p["overlap"].values():
            self.assertLessEqual(v, dt.MAX_OVERLAP + 1e-6)

    def test_an_impossible_pile_is_reported_not_hidden(self):
        lines = L(*[(1.0 + 0.1 * i, 3.0) for i in range(6)])
        p = dt.make_plan(lines, 8.0, None)
        self.assertFalse(p["ok"])
        self.assertTrue(p["drifted"])

    def test_nothing_is_asked_when_shifting_is_enough(self):
        calls = []
        lines = L((1.0, 2.1), (3.0, 1.0))
        p = dt.make_plan(lines, 10.0, lambda *a: calls.append(a))
        self.assertTrue(p["ok"])
        self.assertEqual(calls, [])


class PropertyTests(unittest.TestCase):
    def test_random_scenes_never_overlap_when_the_plan_is_ok(self):
        rnd = random.Random(7)
        n_ok = 0
        for _ in range(300):
            t = 0.5
            lines = []
            for i in range(rnd.randint(1, 25)):
                t += rnd.uniform(0.2, 2.5)
                lines.append({"seg": "x%d" % i, "start": t, "dur": rnd.uniform(0.3, 3.2)})
                t += rnd.uniform(0.3, 1.5)
            total = t + 3.0

            def cb(stage, seg, target):
                return target * rnd.uniform(0.9, 1.0) if rnd.random() < 0.6 else None

            p = dt.make_plan(lines, total, cb)
            # every line stays within the move limits it was planned with
            lim_lead = dt.WIDE_LEAD if p["widened"] else dt.LEAD
            lim_lag = dt.DRIFT_LAG if p["drifted"] else (dt.WIDE_LAG if p["widened"] else dt.LAG)
            for x in (lines if p["ok"] else []):
                d = p["starts"][x["seg"]] - x["start"]
                self.assertGreaterEqual(d, -lim_lead - 1.5e-3)
                self.assertLessEqual(d, lim_lag + 1.5e-3)
            order = sorted(lines, key=lambda z: p["starts"][z["seg"]])
            self.assertEqual([z["seg"] for z in order], [z["seg"] for z in sorted(lines, key=lambda z: z["start"])])
            if p["ok"]:
                n_ok += 1
                self.assertTrue(ends_ok(p, lines))
                last = max(lines, key=lambda z: z["start"])
                self.assertLessEqual(p["starts"][last["seg"]] + p["durs"][last["seg"]], total + 1e-6)
        self.assertGreater(n_ok, 200)

    def test_deterministic(self):
        lines = L((1.0, 2.5), (2.2, 2.5), (3.4, 2.5))
        a = dt.make_plan(lines, 20.0, lambda s, g, t: t)
        b = dt.make_plan(lines, 20.0, lambda s, g, t: t)
        self.assertEqual(a, b)

    def test_garbage_does_not_raise(self):
        self.assertTrue(dt.make_plan([], 5.0)["ok"])
        self.assertTrue(dt.solve([], 5.0)["ok"])


if __name__ == "__main__":
    unittest.main()
