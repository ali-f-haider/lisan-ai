"""The room sound can never go beyond what a real place can have, even when the scene check is unsure or missing.

Born from a film dinner scene (Spanish, long dub): the measurement read a 0.71 s room with the tail only 4.3 dB under the voice,
the scene check said 'dining room' but was not sure, so there was no limit and the dub sounded far more echoey than the original."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import room_acoustics as ra
import scene_context as sc

NOTE_DEFAULT_TRIM = -4.0


def profile(rt, wet, n=10):
    return {"ok": True, "groups": [{"id": 0, "n": n, "dry": False, "confidence": "ok", "label": "room", "rt60": rt, "wet_db": wet}],
            "regions": [[0.0, 1e9, 0]]}


LINES = [{"sid": f"s{i}", "t0": i * 3.0, "t1": i * 3.0 + 2.0, "orig_mid": i * 3.0 + 1.0} for i in range(10)]


def plan_for(rt, wet, scenes=None, trim=NOTE_DEFAULT_TRIM):
    settings = {"mode": "auto", "require_ok": True, "trim_db": trim}
    if scenes is not None:
        settings["scenes"] = scenes
    plan, _assign, why = ra.make_plan(profile(rt, wet), settings, LINES)
    return plan, why


def scene(key, conf, place="x"):
    return [{"t0": 0.0, "t1": 100.0, "key": key, "conf": conf, "place": place}]


class Limits(unittest.TestCase):
    def test_sure_known_place_keeps_its_own_limit(self):
        self.assertEqual(sc.limit_for({"key": "medium_room", "conf": "high"}), (1.0, -17.0))
        self.assertEqual(sc.limit_for({"key": "huge_space", "conf": "high"}), (3.5, -11.0))

    def test_unsure_place_is_one_size_bigger(self):
        self.assertEqual(sc.limit_for({"key": "medium_room", "conf": "low"}), (1.9, -14.0))
        self.assertEqual(sc.limit_for({"key": "outdoors", "conf": "low"}), (0.6, -21.0))
        self.assertEqual(sc.limit_for({"key": "tiled_room", "conf": "low"}), (1.9, -14.0))
        self.assertEqual(sc.limit_for({"key": "huge_space", "conf": "low"}), (3.5, -11.0))     # nothing bigger

    def test_no_usable_place_gets_the_limit_of_any_place(self):
        for s in (None, {}, {"key": "unknown", "conf": "high"}, {"key": "nonsense", "conf": "low"}):
            self.assertEqual(sc.limit_for(s), (3.5, -11.0))

    def test_limits_only_lower(self):
        self.assertEqual(sc.apply_limit(0.3, -30.0, {"key": "medium_room", "conf": "high"}), (0.3, -30.0, False))
        rt, wet, changed = sc.apply_limit(2.0, -5.0, {"key": "medium_room", "conf": "low"})
        self.assertEqual((rt, wet, changed), (1.9, -14.0, True))


class Plan(unittest.TestCase):
    def test_the_film_dinner_scene(self):
        plan, why = plan_for(0.71, -4.3, scene("medium_room", "low", "dining room"))
        self.assertEqual(len(plan), 1)
        self.assertAlmostEqual(plan[0]["wet_db"], -14.0 - 4.0 + ra.RENDER_TRIM_DB)      # was -8.3 + 1 before the change
        self.assertAlmostEqual(plan[0]["rt60"], 0.71)
        self.assertIn("not sure", why)
        self.assertIn("limited to", why)

    def test_no_scene_check_at_all(self):
        plan, why = plan_for(0.71, -4.3, None)
        self.assertAlmostEqual(plan[0]["wet_db"], -11.0 - 4.0 + ra.RENDER_TRIM_DB)
        self.assertIn("place unknown", why)

    def test_scene_check_answered_but_unknown_place(self):
        plan, _ = plan_for(0.71, -4.3, scene("unknown", "low"))
        self.assertAlmostEqual(plan[0]["wet_db"], -11.0 - 4.0 + ra.RENDER_TRIM_DB)

    def test_sure_place_limit_still_wins(self):
        plan, _ = plan_for(0.71, -4.3, scene("medium_room", "high"))
        self.assertAlmostEqual(plan[0]["wet_db"], -17.0 - 4.0 + ra.RENDER_TRIM_DB)

    def test_a_really_big_room_is_not_flattened(self):
        plan, why = plan_for(2.6, -12.0, None)           # a hall: long and moderately loud, nothing to limit
        self.assertAlmostEqual(plan[0]["rt60"], 2.6)
        self.assertAlmostEqual(plan[0]["wet_db"], -12.0 - 4.0 + ra.RENDER_TRIM_DB)
        self.assertNotIn("limited to", why)

    def test_a_dry_measurement_is_untouched(self):
        plan, why = plan_for(0.3, -28.0, scene("medium_room", "low"))
        self.assertAlmostEqual(plan[0]["wet_db"], -28.0 - 4.0 + ra.RENDER_TRIM_DB)
        self.assertNotIn("limited to", why)

    def test_lighter_choices_still_subtract(self):
        a, _ = plan_for(0.71, -4.3, scene("medium_room", "low"), trim=-4.0)
        b, _ = plan_for(0.71, -4.3, scene("medium_room", "low"), trim=-12.0)
        self.assertAlmostEqual(a[0]["wet_db"] - b[0]["wet_db"], 8.0)

    def test_manual_mode_is_not_touched(self):
        plan, _a, _w = ra.make_plan(None, {"mode": "manual", "rt60": 3.0, "wet_db": -5.0}, LINES)
        self.assertAlmostEqual(plan[0]["wet_db"], -5.0 + ra.RENDER_TRIM_DB)


class Coverage(unittest.TestCase):
    def raw(self, *items):
        return [{"t0": a, "t1": b, "setting": k, "confidence": c, "place": "p"} for a, b, k, c in items]

    def test_a_short_answer_is_stretched_over_the_whole_video(self):
        out = sc._clean_scenes(self.raw((0, 1, "medium_room", "low")), 83.0)
        self.assertEqual((out[0]["t0"], out[0]["t1"]), (0.0, 83.0))
        self.assertIsNotNone(sc.scene_at(out, 60.0))

    def test_gaps_are_shared_and_ends_reach_the_edges(self):
        out = sc._clean_scenes(self.raw((5, 20, "small_room", "high"), (40, 60, "huge_space", "high")), 100.0)
        self.assertEqual(out[0]["t0"], 0.0)
        self.assertEqual(out[0]["t1"], out[1]["t0"])
        self.assertAlmostEqual(out[0]["t1"], 30.0)
        self.assertEqual(out[1]["t1"], 100.0)

    def test_a_good_answer_and_an_empty_one_are_left_alone(self):
        good = sc._clean_scenes(self.raw((0, 30, "small_room", "high"), (30, 83, "outdoors", "high")), 83.0)
        self.assertEqual([(s["t0"], s["t1"]) for s in good], [(0.0, 30.0), (30.0, 83.0)])
        self.assertEqual(sc._clean_scenes([], 83.0), [])
        self.assertEqual(sc._cover([], 83.0), [])

    def test_the_limit_now_follows_the_lines_of_the_whole_dub(self):
        out = sc._clean_scenes(self.raw((0, 1, "medium_room", "low")), 83.0)
        plan, why = plan_for(0.71, -4.4, out)
        self.assertAlmostEqual(plan[0]["wet_db"], -14.0 - 4.0 + ra.RENDER_TRIM_DB)
        self.assertNotIn("place unknown", why)


if __name__ == "__main__":
    unittest.main()
