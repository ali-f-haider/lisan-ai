"""A dub must not stay bone dry just because the original's room could not be measured well (very short clips, speech without pauses).
Born from a 2.5 minute diner scene: 'room 1: only 3 phrase(s) measured, too few to be sure -- nothing added'.
The scene check (scene_context.py) has to be SURE of the kind of place for anything to be added in those cases."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import room_acoustics as ra
import scene_context as sc

ROOT = Path(__file__).resolve().parents[1]
TRIM = -4.0
LINES = [{"sid": f"s{i}", "t0": i * 3.0, "t1": i * 3.0 + 2.0, "orig_mid": i * 3.0 + 1.0} for i in range(10)]


def profile(n, rt=0.7, wet=-17.0):
    conf = "ok" if n >= ra.MIN_EVENTS_OK else "low"
    return {"ok": True, "groups": [{"id": 0, "n": n, "dry": False, "confidence": conf, "label": "room", "rt60": rt, "wet_db": wet}],
            "regions": [[0.0, 1e9, 0]]}


def unmeasured(note="no usable phrase ends"):
    return {"ok": False, "groups": [], "regions": [], "note": note}


def scene(key, conf="high", place="diner"):
    return [{"t0": 0.0, "t1": 100.0, "key": key, "conf": conf, "place": place}]


def settings(scenes=None, thin=True, fallback=True):
    s = {"mode": "auto", "require_ok": True, "trim_db": TRIM, "thin_ok": thin, "scene_fallback": fallback}
    if scenes is not None:
        s["scenes"] = scenes
    return s


class Preset(unittest.TestCase):
    def test_a_sure_place_that_rings_gets_a_cautious_room_under_its_ceiling(self):
        rt, wet = sc.preset_for({"key": "medium_room", "conf": "high"})
        self.assertEqual((rt, wet), (0.7, -18.5))
        lim = sc.limit_for({"key": "medium_room", "conf": "high"})
        self.assertLess(rt, lim[0])
        self.assertLess(wet, lim[1])
        self.assertEqual(sc.preset_for({"key": "huge_space", "conf": "high"}), (2.45, -12.5))

    def test_unsure_unknown_and_dry_places_get_none(self):
        self.assertIsNone(sc.preset_for({"key": "medium_room", "conf": "low"}))
        self.assertIsNone(sc.preset_for({"key": "unknown", "conf": "high"}))
        self.assertIsNone(sc.preset_for(None))
        for dry in ("outdoors", "vehicle", "studio"):
            self.assertIsNone(sc.preset_for({"key": dry, "conf": "high"}), dry)


class ThinMeasurement(unittest.TestCase):
    def plan(self, n, **kw):
        return ra.make_plan(profile(n), settings(**kw), LINES)

    def test_three_phrases_and_a_sure_place_are_used_softly(self):
        plan, _a, why = self.plan(3, scenes=scene("medium_room"))
        self.assertEqual(len(plan), 1)
        full, _a2, _w2 = self.plan(10, scenes=scene("medium_room"))
        self.assertLess(plan[0]["wet_db"], full[0]["wet_db"])            # softer than a well measured room
        self.assertIn("scene check is sure", why)

    def test_three_phrases_and_an_unsure_place_add_nothing_as_before(self):
        plan, _a, why = self.plan(3, scenes=scene("medium_room", "low"))
        self.assertEqual(plan, [])
        self.assertIn("too few to be sure", why)

    def test_three_phrases_and_no_scene_add_nothing(self):
        self.assertEqual(self.plan(3)[0], [])
        self.assertEqual(self.plan(3, scenes=[])[0], [])

    def test_switched_off_it_is_the_old_behaviour(self):
        self.assertEqual(self.plan(3, scenes=scene("medium_room"), thin=False)[0], [])

    def test_the_scene_ceiling_still_applies(self):
        plan, _a, _w = ra.make_plan(profile(3, rt=2.5, wet=-5.0), settings(scenes=scene("medium_room")), LINES)
        self.assertLessEqual(plan[0]["rt60"], 1.0)

    def test_a_well_measured_room_is_unchanged(self):
        a = ra.make_plan(profile(10), settings(scenes=scene("medium_room")), LINES)
        b = ra.make_plan(profile(10), {"mode": "auto", "require_ok": True, "trim_db": TRIM, "scenes": scene("medium_room")}, LINES)
        self.assertEqual(a[0], b[0])


class NothingMeasurable(unittest.TestCase):
    def plan(self, scenes, **kw):
        return ra.make_plan(unmeasured(), settings(scenes=scenes, **kw), LINES)

    def test_a_sure_diner_gets_the_preset_on_every_line(self):
        plan, assign, why = self.plan(scene("medium_room"))
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["rt60"], 0.7)
        self.assertAlmostEqual(plan[0]["wet_db"], -18.5 + TRIM + ra.RENDER_TRIM_DB, places=3)
        self.assertEqual(len(plan[0]["windows"]), len(LINES))
        self.assertEqual(set(assign), {l["sid"] for l in LINES})
        self.assertIn("scene fallback", why)
        self.assertIn("diner", why)

    def test_two_places_get_their_own_room(self):
        scenes = [{"t0": 0.0, "t1": 15.0, "key": "small_room", "conf": "high", "place": "kitchen"},
                  {"t0": 15.0, "t1": 100.0, "key": "large_room", "conf": "high", "place": "hall"}]
        plan, assign, _w = self.plan(scenes)
        self.assertEqual(len(plan), 2)
        self.assertEqual(sorted(p["rt60"] for p in plan), [0.42, 1.33])
        self.assertNotEqual(assign["s0"], assign["s9"])

    def test_places_that_do_not_ring_or_are_unsure_get_nothing(self):
        for scenes in (scene("outdoors"), scene("studio"), scene("medium_room", "low"), scene("unknown")):
            plan, _a, _w = self.plan(scenes)
            self.assertEqual(plan, [])

    def test_no_scene_or_switched_off_gets_nothing(self):
        self.assertEqual(self.plan(None)[0], [])
        self.assertEqual(self.plan(scene("medium_room"), fallback=False)[0], [])

    def test_the_users_choice_still_counts(self):
        loud = ra.make_plan(unmeasured(), dict(settings(scenes=scene("medium_room")), trim_db=-4.0), LINES)[0][0]["wet_db"]
        soft = ra.make_plan(unmeasured(), dict(settings(scenes=scene("medium_room")), trim_db=-12.0), LINES)[0][0]["wet_db"]
        self.assertAlmostEqual(loud - soft, 8.0, places=3)
        self.assertEqual(ra.make_plan(unmeasured(), {"mode": "off", "scene_fallback": True, "scenes": scene("medium_room")}, LINES)[0], [])

    def test_manual_mode_is_untouched(self):
        plan, _a, why = ra.make_plan(None, {"mode": "manual", "rt60": 1.0, "wet_db": -15.0}, LINES)
        self.assertEqual(len(plan), 1)
        self.assertIn("manual", why)


class Wiring(unittest.TestCase):
    def test_the_long_dub_asks_for_both_and_has_a_switch(self):
        src = (ROOT / "longdub_service.py").read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertIn('"thin_ok": ROOM_FALLBACK, "scene_fallback": ROOM_FALLBACK', src)
        self.assertIn('LONGDUB_ROOM_FALLBACK', src)

    def test_the_short_dub_is_not_touched(self):
        for p in ROOT.glob("*.py"):
            if p.name in ("longdub_service.py", "room_acoustics.py", "scene_context.py"):
                continue
            t = p.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("scene_fallback", t, p.name)


if __name__ == "__main__":
    unittest.main()
