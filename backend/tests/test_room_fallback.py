"""A dub must not stay bone dry just because the original's room could not be measured well (very short clips, speech without pauses).
Born from a 2.5 minute diner scene: 'room 1: only 3 phrase(s) measured, too few to be sure -- nothing added'.
The scene check (scene_context.py) decides those cases: a sure place as before, and since 1.96.1 also a place it is not sure of
when that place echoes (Prophet Ahmed chapter 1: every place came back "not sure" and 24 lines stayed dry, a cave among them),
then softer still. Outdoors, a car or a studio stay dry."""
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

    def test_three_phrases_and_an_unsure_place_that_echoes_are_used_more_softly(self):
        plan, _a, why = self.plan(3, scenes=scene("medium_room", "low"))
        sure, _a2, _w2 = self.plan(3, scenes=scene("medium_room"))
        self.assertEqual(len(plan), 1)
        self.assertAlmostEqual(plan[0]["wet_db"], sure[0]["wet_db"] + ra.UNSURE_PLACE_DB, places=3)
        self.assertLessEqual(plan[0]["rt60"], sc.SETTINGS["medium_room"][1])        # the place's own ceiling, not one size up
        self.assertIn("not sure", why)

    def test_three_phrases_and_an_unsure_place_that_does_not_echo_add_nothing(self):
        for key in ("outdoors", "studio", "vehicle", "unknown"):
            plan, _a, why = self.plan(3, scenes=scene(key, "low"))
            self.assertEqual(plan, [], key)

    def test_only_the_lines_in_a_place_that_echoes_get_it(self):
        scenes = [{"t0": 0.0, "t1": 15.0, "key": "outdoors", "conf": "low", "place": "desert"},
                  {"t0": 15.0, "t1": 100.0, "key": "small_room", "conf": "low", "place": "cave"}]
        got = {}
        plan, _a, why = ra.make_plan(profile(3), settings(scenes=scenes), LINES, got)
        self.assertEqual(len(plan), 1)
        self.assertEqual(len(plan[0]["windows"]), 5)                     # s5..s9 are in the cave
        self.assertTrue(got["s0"].startswith("dry (desert"))
        self.assertTrue(got["s9"].startswith("cave"))
        self.assertIn("dry in desert", why)

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

    def test_places_that_do_not_ring_get_nothing(self):
        for scenes in (scene("outdoors"), scene("studio"), scene("unknown"), scene("outdoors", "low")):
            plan, _a, _w = self.plan(scenes)
            self.assertEqual(plan, [])

    def test_an_unsure_place_that_echoes_gets_a_softer_preset(self):
        plan, _a, why = self.plan(scene("medium_room", "low"))
        sure = self.plan(scene("medium_room"))[0]
        self.assertEqual(len(plan), 1)
        self.assertAlmostEqual(plan[0]["wet_db"], sure[0]["wet_db"] + ra.UNSURE_PLACE_DB, places=3)
        self.assertIn("not sure", why)

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


class LineRooms(unittest.TestCase):
    def test_every_line_gets_a_decision_for_the_log(self):
        got = {}
        ra.make_plan(profile(10), settings(scenes=scene("medium_room")), LINES, got)
        self.assertEqual(set(got), {l["sid"] for l in LINES})
        self.assertTrue(all("s" in v for v in got.values()))

    def test_a_dry_room_is_named_dry(self):
        got = {}
        p = profile(10)
        p["groups"][0]["dry"] = True
        ra.make_plan(p, settings(), LINES, got)
        self.assertEqual(set(got.values()), {"dry (no echo in the original)"})

    def test_the_log_joins_neighbouring_lines_of_the_same_room(self):
        import longdub_service as ld
        lines = [{"sid": f"s{i}", "orig_mid": i * 10.0} for i in range(4)]
        got = ld._room_lines_text(lines, {"s0": "small room, 0.31 s", "s1": "small room, 0.31 s", "s2": "dry (desert)", "s3": "cave, 0.42 s, softly"})
        self.assertEqual(got, "0:00-0:10 small room, 0.31 s (2 lines); 0:20-0:20 dry (desert) (1 line); 0:30-0:30 cave, 0.42 s, softly (1 line)")
        self.assertEqual(ld._room_lines_text(lines, {}), "")

class OneStudio(unittest.TestCase):
    def test_one_sure_studio_for_a_whole_film_is_not_trusted(self):
        note = {}
        got = sc._clean_scenes([{"t0": 0, "t1": 797, "setting": "studio", "confidence": "high", "place": "recording studio"}], 797.2, note)
        self.assertEqual(got[0]["conf"], "low")
        self.assertTrue(note.get("one_studio"))
        self.assertGreaterEqual(sc.limit_for(got[0])[0], 0.43)        # the cave's measured 0.43 s is no longer cut to a studio's 0.3 s

    def test_a_short_studio_clip_and_a_studio_part_stay_sure(self):
        self.assertEqual(sc._clean_scenes([{"t0": 0, "t1": 60, "setting": "studio", "confidence": "high", "place": "podcast"}], 60, {})[0]["conf"], "high")
        two = sc._clean_scenes([{"t0": 0, "t1": 400, "setting": "studio", "confidence": "high", "place": "narration"},
                                {"t0": 400, "t1": 797, "setting": "outdoors", "confidence": "high", "place": "desert"}], 797, {})
        self.assertEqual([x["conf"] for x in two], ["high", "high"])


if __name__ == "__main__":
    unittest.main()
