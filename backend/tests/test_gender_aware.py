"""Gender-aware Arabic translation: who speaks, who is spoken to (gender_context.py, the translator prompt, the long dub and the short dub).
Offline: the listening check and the translator are fakes, the audio is synthetic."""
import json, sys, tempfile, unittest, wave
from pathlib import Path
from unittest import mock
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gender_context as gc
import gemini_service
import longdub_service as ld
from models import Segment


def tone_wav(path, hz, seconds=40.0, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    x = 0.4 * np.sin(2 * np.pi * hz * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t) ** 2)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def fake_cut(src, start, dur, out):
    """Copies the asked piece of a wav (or just writes bytes for an mp3 name)."""
    if str(out).endswith(".mp3"):
        Path(out).write_bytes(b"mp3")
        return
    with wave.open(str(src)) as r:
        sr = r.getframerate(); r.setpos(int(start * sr)); data = r.readframes(int(dur * sr))
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(data)


SEGS = [{"speaker": "A", "start": 0, "end": 20}, {"speaker": "B", "start": 20, "end": 40}]


class DetectTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.addCleanup(self.dir.cleanup)
        self.src = Path(self.dir.name) / "v.wav"; tone_wav(self.src, 120)

    def detect(self, segs=SEGS, chosen=None, describe=None, src="default"):
        return gc.detect_genders("j", self.src if src == "default" else src, segs, chosen, work=Path(self.dir.name) / "w", cut=fake_cut, describe=describe)

    def test_the_persons_choice_is_certain_and_is_never_listened_over(self):
        called = []
        out = self.detect(chosen={"A": "female", "B": "male"}, describe=lambda j, p: called.append(p) or {"gender": "male"})
        self.assertEqual(out["A"], {"gender": "female", "source": "chosen", "uncertain": False})
        self.assertEqual(called, [])

    def test_the_listener_decides_for_the_others_and_says_when_it_is_unsure(self):
        out = self.detect(segs=SEGS[:1], describe=lambda j, p: {"gender": "male", "uncertain": False})                      # agrees with the 120 Hz pitch
        self.assertEqual((out["A"]["gender"], out["A"]["source"], out["A"]["uncertain"]), ("male", "heard", False))
        out = self.detect(segs=SEGS[:1], describe=lambda j, p: {"gender": "female", "uncertain": True})                     # the listener is unsure: the pitch decides
        self.assertEqual((out["A"]["gender"], out["A"]["source"], out["A"]["uncertain"]), ("male", "pitch", True))

    def test_listener_and_pitch_disagreeing_is_unsure(self):
        out = self.detect(segs=SEGS[:1], describe=lambda j, p: {"gender": "female", "uncertain": False})     # a 120 Hz voice heard as female
        self.assertEqual((out["A"]["gender"], out["A"]["uncertain"]), ("female", True))

    def test_without_a_listener_the_pitch_is_used_and_marked_unsure(self):
        out = self.detect(segs=SEGS[:1])
        self.assertEqual((out["A"]["gender"], out["A"]["source"], out["A"]["uncertain"]), ("male", "pitch", True))

    def test_no_audio_or_a_failing_listener_never_raises_and_leaves_gender_unknown(self):
        self.assertEqual(self.detect(src=None)["A"], {"gender": "", "source": "", "uncertain": True})
        def boom(j, p):
            raise RuntimeError("down")
        out = self.detect(segs=SEGS[:1], describe=boom)
        self.assertEqual(out["A"]["source"], "pitch")


class ContextTests(unittest.TestCase):
    ROWS = [{"segment_id": f"s{i}", "start": i, "speaker": sp, "text": t} for i, (sp, t) in enumerate(
        [("A", "How are you?"), ("B", "Tired."), ("A", "You should rest."), ("C", "Hello everyone.")])]
    G = {"A": {"gender": "male"}, "B": {"gender": "female"}, "C": {"gender": ""}}

    def test_each_line_sees_its_neighbours_with_speaker_and_gender(self):
        ctx = gc.build_context(self.ROWS, ["s2"], self.G)["s2"]
        self.assertEqual(ctx["speaker_gender"], "male")
        self.assertEqual([(x["speaker"], x["gender"]) for x in ctx["before"]], [("A", "male"), ("B", "female")])
        self.assertEqual([(x["speaker"], x["gender"]) for x in ctx["after"]], [("C", "unknown")])

    def test_table_says_how_sure_each_gender_is(self):
        table = gc.speaker_table({"A": {"gender": "female", "source": "chosen", "uncertain": False}, "B": {"gender": "male", "source": "heard", "uncertain": True},
                                  "C": {"gender": "", "source": "", "uncertain": True}})
        self.assertIn("certain", table["A"]); self.assertIn("probably", table["B"]); self.assertEqual(table["C"], "unknown")

    def test_flags_are_read_defensively(self):
        self.assertEqual(gc.read_flags({"addressee": "Female", "gender_check": True}), ("female", True))
        self.assertEqual(gc.read_flags({"addressee": "x", "gender_check": "yes"}), ("", False))


def answer(items):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(items)}]}}]}, None


class PromptTests(unittest.TestCase):
    def run_translate(self, **kw):
        seen = {}
        def fake(key, payload, timeout=0):
            seen["p"] = payload["contents"][0]["parts"][0]["text"]
            return answer([{"segment_id": "a", "arabic_text": "x", "emotion": "neutral", "addressee": "female", "gender_check": True}])
        with mock.patch.object(gemini_service, "call_gemini", fake), mock.patch.object(gemini_service, "record_gemini"):
            res = gemini_service.translate_segments("j", [Segment(segment_id="a", start=0, end=2, speaker="S1", text="You look tired.")], "k", **kw)
        return seen["p"], res

    def test_the_rules_the_speakers_and_the_neighbours_reach_the_translator(self):
        gender = {"speakers": {"S1": "male (certain: chosen by the user)", "S2": "female (heard, from the voice)"},
                  "lines": {"a": {"speaker_gender": "male", "before": [], "after": [{"speaker": "S2", "gender": "female", "english": "Yes."}]}}}
        prompt, res = self.run_translate(gender=gender)
        for needle in ("GENDER AND NUMBER", "S2", "female (heard", '"speaker_gender": "male"', '"after": [{"speaker": "S2"', "gender_check", "addressee", "أنتِ"):
            self.assertIn(needle, prompt)
        self.assertEqual(res["translated_segments"][0]["addressee"], "female")

    def test_without_gender_the_prompt_is_the_old_one(self):
        prompt, _ = self.run_translate()
        self.assertNotIn("GENDER AND NUMBER", prompt); self.assertNotIn("addressee", prompt)
        self.assertIn('{"segment_id": "...", "arabic_text": "Arabic text with Tashkeel", "emotion": "neutral"}', prompt)


class LongDubTests(unittest.TestCase):
    def job(self, **kw):
        j = {"id": "j", "status": "editing", "speaker_list": [{"id": "sp1", "name": "A"}, {"id": "sp2", "name": "B", "gender": "female", "gender_source": "heard", "gender_uncertain": True}]}
        j.update(kw)
        return j

    def rows(self):
        return [dict(segment_id=f"seg_{i}", start=i * 3, end=i * 3 + 2, speaker=("A" if i % 2 == 0 else "B"), speaker_id=("sp1" if i % 2 == 0 else "sp2"),
                     text=f"line {i}", arabic_text=f"old {i}") for i in range(6)]

    def test_the_translation_gets_the_gender_context_and_the_flags_come_back(self):
        j, rows = self.job(), self.rows()
        seen = {}
        def fake(job_id, segs, key, **kw):
            seen.update(kw)
            return {"status": "success", "translated_segments": [{"segment_id": s.segment_id, "arabic_text": "ar", "emotion": "neutral", "addressee": "male", "gender_check": s.segment_id == "seg_1"} for s in segs]}
        flags = {}
        with mock.patch("gemini_service.translate_segments", fake):
            got = ld._translate_batch("j", rows[:2], gender=ld._gender_args(j, rows, rows[:2]), flags=flags)
        self.assertEqual(flags, {"seg_0": ("male", False), "seg_1": ("male", True)})
        self.assertIn("seg_1", seen["gender"]["lines"]); self.assertEqual(seen["gender"]["speakers"]["A"], "unknown")
        ld._apply_gender_flags(rows[:2], flags)
        self.assertEqual((rows[0].get("gender_check"), rows[1].get("gender_check"), rows[0]["addressee"]), (None, True, "male"))
        ld._apply_gender_flags(rows[:2], {"seg_1": ("male", False)})
        self.assertNotIn("gender_check", rows[1])

    def test_the_speaker_panel_gender_is_optional_and_a_choice_is_certain(self):
        j = self.job()
        with mock.patch.object(ld, "read_segments", return_value=self.rows()), mock.patch.object(ld, "_write_segments"), mock.patch.object(ld, "_save"), mock.patch.object(ld, "_ev"):
            self.assertTrue(ld.set_speakers(j, [{"id": "sp1", "name": "A"}, {"id": "sp2", "name": "B"}])[0])       # nothing said: unchanged
            self.assertEqual(j["speaker_list"][1]["gender_source"], "heard")
            ld.set_speakers(j, [{"id": "sp1", "name": "A", "gender": "male"}, {"id": "sp2", "name": "B", "gender": "female"}])
            self.assertEqual([(s["gender"], s["gender_source"], s["gender_uncertain"]) for s in j["speaker_list"]], [("male", "chosen", False), ("female", "chosen", False)])
            ld.set_speakers(j, [{"id": "sp1", "name": "A", "gender": ""}, {"id": "sp2", "name": "B", "gender": "female"}])    # back to Auto
            self.assertEqual((j["speaker_list"][0]["gender"], j["speaker_list"][0]["gender_source"]), ("", ""))
            self.assertEqual(j["speaker_list"][1]["gender_source"], "chosen")
            ld.set_speakers(j, [{"id": "sp1", "name": "A", "gender": "robot"}, {"id": "sp2", "name": "B", "gender": "female"}])
            self.assertEqual(j["speaker_list"][0]["gender"], "")

    def test_detection_keeps_what_the_person_chose_and_never_raises(self):
        j = self.job()
        j["speaker_list"][0].update(gender="male", gender_source="chosen", gender_uncertain=False)
        seen = {}
        def fake(job_id, src, segs, chosen, **kw):
            seen["chosen"] = chosen
            return {"A": {"gender": "male", "source": "chosen", "uncertain": False}, "B": {"gender": "female", "source": "heard", "uncertain": False}}
        with mock.patch.object(gc, "detect_genders", fake), mock.patch.object(ld, "_ev"), mock.patch.object(ld, "_wd", return_value=Path(tempfile.gettempdir())):
            ld._detect_speaker_genders(j, self.rows(), None)
        self.assertEqual(seen["chosen"], {"A": "male"})
        self.assertEqual((j["speaker_list"][1]["gender"], j["speaker_list"][1]["gender_uncertain"]), ("female", False))
        with mock.patch.object(gc, "detect_genders", side_effect=RuntimeError("x")), mock.patch.object(ld, "_ev"), mock.patch.object(ld, "_wd", return_value=Path(tempfile.gettempdir())):
            ld._detect_speaker_genders(j, self.rows(), None)

    def test_editing_the_arabic_or_confirming_clears_the_mark_and_changing_the_speaker_does_too(self):
        def edit(e, **extra):
            rows = [dict(segment_id="seg_1", start=0, end=3, speaker="A", speaker_id="sp1", text="x", arabic_text="y", gender_check=True, **extra)]
            job = self.job()
            with mock.patch.object(ld, "read_segments", return_value=rows), mock.patch.object(ld, "_write_segments"), mock.patch.object(ld, "_save"), mock.patch.object(ld, "_ev"):
                ld.update_segments(job, [dict(segment_id="seg_1", **e)])
            return rows[0]
        self.assertNotIn("gender_check", edit(dict(arabic_text="z")))
        self.assertNotIn("gender_check", edit(dict(gender_ok=True)))
        self.assertNotIn("gender_check", edit(dict(speaker_id="sp2")))
        self.assertTrue(edit(dict(text="x2"))["gender_check"])

    def test_changing_a_gender_translates_the_speakers_lines_and_their_neighbours_again_in_steps(self):
        j = self.job(); j["speaker_list"][0].update(gender="female", gender_source="chosen", gender_uncertain=False)
        rows = self.rows(); rows[0]["ar_set"] = True            # typed by hand: never replaced
        store = {"rows": rows}
        calls = []
        def fake_batch(job_id, batch, glossary=None, paces=None, gender=None, flags=None):
            calls.append([r["segment_id"] for r in batch])
            for r in batch:
                flags[r["segment_id"]] = ("female", False)
            return {r["segment_id"]: ("new " + r["segment_id"], "neutral") for r in batch}
        with mock.patch.object(ld, "read_segments", side_effect=lambda job: json.loads(json.dumps(store["rows"]))), \
                mock.patch.object(ld, "_write_segments", side_effect=lambda job, r: store.update(rows=r)), mock.patch.object(ld, "_translate_batch", fake_batch), \
                mock.patch.object(ld, "_ev"), mock.patch.object(ld, "GENDER_REDO_MAX", 3):
            ok, res = ld.retranslate_for_gender(j, "sp1")
            self.assertEqual((ok, res["done"], res["remaining"], res["skipped_by_hand"]), (True, 3, 2, 1))
            ok, res = ld.retranslate_for_gender(j, "sp1")
            self.assertEqual((res["done"], res["remaining"]), (2, 0))
            ok, res = ld.retranslate_for_gender(j, "sp1")
            self.assertEqual((res["done"], res["remaining"]), (0, 0))
        by = {r["segment_id"]: r for r in store["rows"]}
        self.assertEqual(by["seg_0"]["arabic_text"], "old 0")
        self.assertEqual(by["seg_2"]["arabic_text"], "new seg_2")
        self.assertEqual(ld.retranslate_for_gender(j, "nope")[1][1], 404)
        j["status"] = "done"; self.assertFalse(ld.retranslate_for_gender(j, "sp1")[0])


class ShortDubTests(unittest.TestCase):
    def load(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        fn = src[src.index("def _short_gender_context"):src.index('@app.post("/api/detect_emotions")')]
        scope = {"GEMINI_API_KEY": "k", "resolve_job_audio": lambda j: None}
        exec(fn, scope)
        return scope

    class Req:
        job_id = "job1"
        segments = [Segment(segment_id="a", start=0, end=2, speaker="S1", text="You look tired."), Segment(segment_id="b", start=2, end=3, speaker="S2", text="Yes.")]
        speaker_genders = {"S1": "male", "S2": "robot"}
        heard_genders = {"S2": {"gender": "female", "uncertain": True}}
        context = [{"segment_id": "a", "start": 0, "end": 2, "speaker": "S1", "text": "You look tired."}, {"segment_id": "b", "start": 2, "end": 3, "speaker": "S2", "text": "Yes."},
                   {"segment_id": "z", "start": 9, "end": 10, "speaker": "S3", "text": "Hello."}]

    def test_choice_first_then_what_was_heard_then_a_listening_check_for_the_rest(self):
        scope = self.load()
        with mock.patch("gender_context.detect_genders", return_value={"S3": {"gender": "female", "source": "heard", "uncertain": False}}) as det:
            args, genders = scope["_short_gender_context"](self.Req)
        self.assertEqual([r["speaker"] for r in det.call_args[0][2]], ["S3"])        # only the speaker nobody knows is listened to
        self.assertEqual((genders["S1"]["source"], genders["S2"]["source"], genders["S2"]["uncertain"], genders["S3"]["gender"]), ("chosen", "heard", True, "female"))
        self.assertEqual(args["lines"]["a"]["after"][0]["gender"], "female")
        self.assertIn("certain", args["speakers"]["S1"])

    def test_the_page_sends_the_gender_and_the_server_answers_with_it(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        for needle in ("speaker_genders: Dict[str, str]", "heard_genders: Dict[str, dict]", "context: List[dict]", 'result["speaker_genders"] = genders', "gender=gender)"):
            self.assertIn(needle, main)
        js = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("segments: unlocked, gemini_api_key", js)
        self.assertEqual(js.count("JSON.stringify(translateBody(unlocked"), 4)
        self.assertEqual(js.count("adoptHeardGenders(data);"), 4)
        for needle in ("seg.gender_check = item.gender_check === true", "gender-check", "راجع الجنس"):
            self.assertIn(needle, js)


class LongDubPageTests(unittest.TestCase):
    def test_speaker_panel_has_an_optional_gender_and_lines_get_the_check_button(self):
        html = (ROOT / "dub_long.html").read_text(encoding="utf-8")
        for needle in ("function genderSelectHtml", 'data-gen="', "function redoForGender", "/speakers/", "/retranslate", "gender: s.gender", "function genderBadge",
                       "d.gender_ok = true", "Check gender", "راجع الجنس", "var genMark = genderBadge(s)", "Optional."):
            self.assertIn(needle, html)
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('d["gender_check"] = bool(r.get("gender_check"))', main)
        self.assertIn('@app.post("/api/longdub/{job_id}/speakers/{speaker_id}/retranslate")', main)


class TrialScriptTests(unittest.TestCase):
    def test_the_gold_cases_are_well_formed_and_recognise_right_and_wrong_forms(self):
        import re, gender_trial as t
        for c in t.CASES:
            re.compile(c["must"]); [re.compile(p) for p in c["must_not"]]
            self.assertTrue(0 <= c["target"] < len(c["lines"]))
        woman_tired, man_tired = t.CASES[0], t.CASES[1]
        self.assertTrue(re.search(woman_tired["must"], t.strip("أَنَا مُتْعَبَةٌ جِدًّا.")))
        self.assertTrue(re.search(woman_tired["must_not"][0], t.strip("أَنَا مُتْعَبٌ جِدًّا.")))
        self.assertTrue(re.search(man_tired["must"], t.strip("أَنَا مُتْعَبٌ جِدًّا.")))
        self.assertTrue(re.search(t.CASES[6]["must"], "أُحِبُّكِ.") and not re.search(t.CASES[6]["must_not"][0], "أُحِبُّكِ."))


if __name__ == "__main__":
    unittest.main()
