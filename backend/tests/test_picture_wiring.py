"""The picture unit as it is wired into the project flow: price, option, refund, stale advice, flags only.
Everything here uses invented records and fake money / fake network; nothing calls a service."""
import copy
import math
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import api_settings
import image_unit
import longdub_service as ld

ROOT = Path(__file__).resolve().parent.parent

CFG = {"fee": 3, "analysis_per_min": 2, "flat": 5, "chars_per_credit": 60, "clone_credits": 5, "merge_credits": 1,
       "speaker_check_flat": 4, "speaker_check_per_min": 0.4, "picture_flat": 4, "picture_per_min": 3.0}


class Unit:
    """Switch the unit on for a test without touching the environment."""
    def __enter__(self):
        self.p = patch.object(image_unit, "ENABLED", True)
        self.p.start()
        return self

    def __exit__(self, *a):
        self.p.stop()


class PriceTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(ld, "_lip_rate", lambda cfg, res: 40.0)       # (another test module may leave the lip-sync helpers replaced)
        p.start()
        self.addCleanup(p.stop)

    def test_price_is_flat_plus_minutes_for_video_only(self):
        with Unit():
            for minutes, credits in ((1, 7), (8, 28), (30, 94), (60, 184)):
                est = ld.compute_estimate(minutes * 60, CFG, 3, has_video=True, picture=True)
                self.assertEqual(est["picture"], credits, minutes)
                base = ld.compute_estimate(minutes * 60, CFG, 3)
                self.assertEqual(est["total"], base["total"] + credits)

    def test_nothing_changes_when_off_audio_or_not_asked(self):
        base = ld.compute_estimate(480, CFG, 3)
        with Unit():
            for kw in ({}, {"has_video": False, "picture": True}, {"has_video": True, "picture": False}):
                self.assertEqual(ld.compute_estimate(480, CFG, 3, **kw), base)
        self.assertEqual(ld.compute_estimate(480, CFG, 3, has_video=True, picture=True), base)       # unit off

    def test_longer_than_the_checks_limit_has_no_picture_price(self):
        with Unit():
            est = ld.compute_estimate(75 * 60, CFG, 3, has_video=True, picture=True)
            self.assertNotIn("picture", est)

    def test_api_percentage_scales_both_parts(self):
        half = api_settings.scale_pricing(dict(CFG), 50)
        self.assertEqual(half["picture_flat"], 2)
        self.assertAlmostEqual(half["picture_per_min"], 1.5)
        triple = api_settings.scale_pricing(dict(CFG), 300)
        self.assertEqual((triple["picture_flat"], triple["picture_per_min"]), (12, 9.0))
        self.assertEqual(api_settings.scale_pricing(dict(CFG), 100)["picture_flat"], 4)
        with Unit():
            self.assertEqual(ld.compute_estimate(480, half, 3, has_video=True, picture=True)["picture"], 2 + 12)


class RefundTests(unittest.TestCase):
    def job(self, paid=28):
        return {"id": "j1", "uid": "u1", "picture_paid": paid, "paid": {"analysis": 40}}

    def run_refund(self, job, fraction, ok=True):
        calls = []

        def fake(j, uid, amount, slot, key):
            calls.append((uid, amount, slot, key))
            return ok
        with patch.object(ld, "_refund", fake), patch.object(ld, "_save"), patch.object(ld, "_ev"):
            return ld._picture_refund(job, fraction), calls

    def test_all_worked_returns_nothing(self):
        got, calls = self.run_refund(self.job(), 0.0)
        self.assertEqual((got, calls), (0, []))

    def test_nothing_usable_returns_everything_once(self):
        job = self.job()
        got, calls = self.run_refund(job, 1.0)
        self.assertEqual(got, 28)
        self.assertEqual(calls, [("u1", 28, "analysis", "picture_unused")])
        again, calls2 = self.run_refund(job, 1.0)
        self.assertEqual((again, calls2), (0, []))

    def test_partial_rounds_up_to_whole_credits_and_never_above_paid(self):
        got, _ = self.run_refund(self.job(28), 1 / 3)
        self.assertEqual(got, math.ceil(28 / 3))
        got, _ = self.run_refund(self.job(7), 5.0)
        self.assertEqual(got, 7)

    def test_target_is_frozen_and_retried_after_a_lost_answer(self):
        job = self.job()
        got, _ = self.run_refund(job, 0.5, ok=False)
        self.assertEqual(got, 0)
        self.assertEqual((job["picture_refund_target"], job["picture_refund_pending"]), (14, 14))
        got, calls = self.run_refund(job, 1.0)           # a later call cannot grow it
        self.assertEqual((got, calls[0][1]), (14, 14))
        self.assertTrue(job["picture_refund_done"])
        self.assertNotIn("picture_refund_pending", job)

    def test_nothing_paid_means_nothing_returned(self):
        got, calls = self.run_refund({"id": "j", "uid": "u"}, 1.0)
        self.assertEqual((got, calls), (0, []))


class OptionTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(ld, "_lip_rate", lambda cfg, res: 40.0)
        p.start()
        self.addCleanup(p.stop)

    def estimated(self, **kw):
        job = {"id": "j", "uid": "u", "status": "estimated", "has_video": True, "picture_option": True, "duration": 480,
               "stated_speakers": 3, "estimate": {}}
        job.update(kw)
        return job

    def test_owner_toggles_before_accepting_and_the_quote_follows(self):
        with Unit(), patch.object(ld, "_save"), patch.object(ld, "_job_pricing", return_value=CFG):
            job = self.estimated()
            ok, err = ld.set_picture_option(job, "u", False)
            self.assertTrue(ok)
            self.assertFalse(job["picture_option"])
            self.assertNotIn("picture", job["estimate"])
            ok, err = ld.set_picture_option(job, "u", True)
            self.assertEqual(job["estimate"]["picture"], 28)

    def test_other_owner_accepted_job_audio_and_off_are_refused(self):
        with Unit(), patch.object(ld, "_save"), patch.object(ld, "_job_pricing", return_value=CFG):
            for job, uid in ((self.estimated(), "x"), (self.estimated(status="accepted"), "u"),
                             (self.estimated(has_video=False), "u")):
                ok, err = ld.set_picture_option(job, uid, False)
                self.assertFalse(ok)
            old = self.estimated()
            del old["picture_option"]
            self.assertFalse(ld.set_picture_option(old, "u", True)[0])      # an old project is never opted in behind the person's back
        self.assertFalse(ld.set_picture_option(self.estimated(), "u", False)[0])        # unit off

    def test_the_toggle_charges_and_asks_nothing(self):
        with Unit(), patch.object(ld, "_save"), patch.object(ld, "_job_pricing", return_value=CFG), \
                patch.object(ld, "_charge") as charge, patch.object(image_unit, "analyse") as analyse:
            ld.set_picture_option(self.estimated(), "u", False)
            charge.assert_not_called()
            analyse.assert_not_called()


class StaleAdviceTests(unittest.TestCase):
    def row(self):
        return {"segment_id": "s1", "speaker": "Speaker 1", "text": "Hello there.", "start": 1.0, "end": 3.0,
                "picture_evidence": True, "picture_gender_check": True, "picture_off_mic": True,
                "picture_split_suggestions": [{"segment_id": "s1"}],
                "speaker_confidence": "low", "speaker_reasons": ["voters_split"]}

    def test_a_text_change_drops_only_the_picture_marks(self):
        r = self.row()
        ld._clear_picture_advice(r)
        self.assertTrue(r["picture_stale"])
        for k in ("picture_evidence", "picture_gender_check", "picture_off_mic", "picture_split_suggestions"):
            self.assertNotIn(k, r)
        self.assertEqual(r["speaker_confidence"], "low")           # the sound-based review is untouched
        self.assertEqual(r["text"], "Hello there.")

    def test_a_speaker_or_time_change_drops_the_whole_review_as_before(self):
        r = self.row()
        ld._clear_speaker_review(r)
        self.assertNotIn("speaker_confidence", r)
        self.assertNotIn("picture_gender_check", r)
        self.assertTrue(r["picture_stale"])

    def test_a_row_without_picture_data_is_not_touched(self):
        r = {"segment_id": "s", "text": "x", "speaker_confidence": "low"}
        before = copy.deepcopy(r)
        ld._clear_picture_advice(r)
        self.assertEqual(r, before)

    def test_speaker_advice_goes_when_no_line_carries_it(self):
        job = {"picture_option": True, "speaker_list": [
            {"name": "Speaker 1", "gender": "male", "picture_gender_check": {"x": 1}, "picture_advice": {"gender": "female"}},
            {"name": "Speaker 2", "gender": "female", "picture_gender_check": {"x": 1}}]}
        rows = [{"speaker": "Speaker 1", "picture_evidence": True},
                {"speaker": "Speaker 2", "picture_evidence": True, "picture_gender_check": True}]
        with patch.object(ld, "_save") as save:
            self.assertTrue(ld._refresh_picture_speakers(job, rows))
            save.assert_called_once()
        s1, s2 = job["speaker_list"]
        self.assertNotIn("picture_gender_check", s1)          # no line of Speaker 1 still carries the mark
        self.assertIn("picture_advice", s1)
        self.assertIn("picture_gender_check", s2)
        self.assertEqual((s1["gender"], s2["gender"]), ("male", "female"))     # a chosen or measured gender is never rewritten
        with patch.object(ld, "_save") as save:
            self.assertFalse(ld._refresh_picture_speakers(job, rows))
            save.assert_not_called()

    def test_refresh_does_nothing_for_projects_without_the_option(self):
        job = {"speaker_list": [{"name": "A", "picture_gender_check": {"x": 1}}]}
        self.assertFalse(ld._refresh_picture_speakers(job, []))
        self.assertIn("picture_gender_check", job["speaker_list"][0])


class AdviceTests(unittest.TestCase):
    def test_a_conflict_is_a_flag_and_nothing_else_changes(self):
        job = {"picture_option": True, "speaker_list": [
            {"name": "Speaker 1", "gender": "male", "voice_id": "v-male", "gender_source": "pitch"}]}
        rows = [{"segment_id": f"s{i}", "speaker": "Speaker 1", "text": "x", "start": i, "end": i + 0.9,
                 "emotion": "neutral", "arabic_text": "نص"} for i in range(4)]
        before_rows = copy.deepcopy(rows)
        attrs = {"Speaker 1": {"gender": "female", "age": "adult", "lines": 4}}
        result = {"status": "ok", "cuts": [], "chunks": []}
        with Unit(), patch.object(image_unit, "person_attributes", return_value=attrs), \
                patch.object(image_unit, "gender_conflicts", return_value={"Speaker 1": {"picture": "female", "voice": "male"}}):
            ld._picture_advice(job, rows, result)
        sp = job["speaker_list"][0]
        self.assertIn("picture_gender_check", sp)
        self.assertEqual((sp["gender"], sp["voice_id"]), ("male", "v-male"))
        for r, b in zip(rows, before_rows):
            for k in ("segment_id", "speaker", "text", "start", "end", "emotion", "arabic_text"):
                self.assertEqual(r[k], b[k])
        self.assertEqual([r.get("picture_gender_check") for r in rows], [True, None, None, None])     # one mark per speaker

    # ---- what the picture shows without doubt is set; anything less only flags
    def sure(self, g="female", lines=4, seen=None, agree=1.0):
        return {"Speaker 1": {"gender": g, "age": None, "gender_lines": lines, "gender_observed": lines if seen is None else seen,
                              "gender_agreement": agree}}

    def advise(self, speaker, attrs, decide=True):
        job = {"picture_option": True, "speaker_list": [speaker]}
        rows = [{"segment_id": f"s{i}", "speaker": speaker["name"], "text": "x", "start": i, "end": i + 0.9} for i in range(4)]
        with Unit(), patch.object(image_unit, "person_attributes", return_value=attrs), patch.object(image_unit, "GENDER_DECIDE", decide):
            ld._picture_advice(job, rows, {"status": "ok", "cuts": [], "chunks": []})
        return job["speaker_list"][0], rows

    def test_a_clear_picture_sets_the_gender_before_the_translation(self):
        sp, rows = self.advise({"name": "Speaker 1", "gender": "female", "gender_source": "pitch", "gender_uncertain": True}, self.sure("male"))
        self.assertEqual((sp["gender"], sp["gender_source"], sp["gender_uncertain"]), ("male", "seen", False))
        self.assertNotIn("picture_gender_check", sp)                      # nothing left to doubt: no flag
        self.assertFalse(any(r.get("picture_gender_check") for r in rows))

    def test_a_clear_picture_that_agrees_makes_the_gender_certain(self):
        sp, _ = self.advise({"name": "Speaker 1", "gender": "male", "gender_source": "pitch", "gender_uncertain": True}, self.sure("male"))
        self.assertEqual((sp["gender"], sp["gender_uncertain"]), ("male", False))

    def test_a_speaker_with_no_gender_yet_gets_it(self):
        sp, _ = self.advise({"name": "Speaker 1", "gender": "", "gender_source": "", "gender_uncertain": True}, self.sure("female"))
        self.assertEqual((sp["gender"], sp["gender_source"]), ("female", "seen"))

    def test_a_doubtful_picture_only_flags(self):
        for attrs in (self.sure("female", lines=2),                       # too few sightings
                      self.sure("female", lines=4, seen=5, agree=0.8),     # one sighting showed something else or was unclear
                      self.sure("female", lines=4, seen=4, agree=0.75)):
            sp, rows = self.advise({"name": "Speaker 1", "gender": "male", "gender_source": "pitch", "gender_uncertain": True}, attrs)
            self.assertEqual((sp["gender"], sp["gender_source"]), ("male", "pitch"), attrs)
        sp, rows = self.advise({"name": "Speaker 1", "gender": "male", "gender_source": "pitch", "gender_uncertain": True}, self.sure("female", lines=4, seen=5, agree=0.8))
        self.assertIn("picture_gender_check", sp)                          # (the 80% case is a suspicion: flagged)
        self.assertTrue(rows[0].get("picture_gender_check"))

    def test_the_persons_own_choice_is_never_changed(self):
        sp, _ = self.advise({"name": "Speaker 1", "gender": "male", "gender_source": "chosen", "gender_uncertain": False}, self.sure("female"))
        self.assertEqual((sp["gender"], sp["gender_source"]), ("male", "chosen"))
        self.assertIn("picture_gender_check", sp)                          # still told to the person

    def test_a_gender_the_words_proved_is_flagged_not_overruled(self):
        sp, rows = self.advise({"name": "Speaker 1", "gender": "male", "gender_source": "text", "gender_uncertain": False}, self.sure("female"))
        self.assertEqual((sp["gender"], sp["gender_source"]), ("male", "text"))
        self.assertIn("picture_gender_check", sp)
        self.assertTrue(rows[0].get("picture_gender_check"))

    def test_the_switch_turns_the_decision_off(self):
        sp, _ = self.advise({"name": "Speaker 1", "gender": "male", "gender_source": "pitch", "gender_uncertain": True}, self.sure("female"), decide=False)
        self.assertEqual((sp["gender"], sp["gender_source"]), ("male", "pitch"))
        self.assertIn("picture_gender_check", sp)

    def test_certain_genders_needs_every_sighting_to_agree(self):
        c = image_unit.certain_genders
        self.assertEqual(c(self.sure("male")), {"Speaker 1": "male"})
        self.assertEqual(c(self.sure("male", lines=2)), {})
        self.assertEqual(c(self.sure("male", lines=4, seen=5, agree=0.8)), {})
        self.assertEqual(c({"S": {"gender": None, "gender_lines": 5, "gender_observed": 5, "gender_agreement": 1.0}}), {})
        self.assertEqual(c(None), {})
        self.assertEqual(c({"S": "junk"}), {})

    def test_a_failed_or_missing_result_adds_nothing(self):
        job = {"speaker_list": [{"name": "A"}]}
        rows = [{"segment_id": "s", "speaker": "A"}]
        for result in (None, {"status": "failed"}, {"status": "off"}, "junk"):
            ld._picture_advice(job, rows, result)
        self.assertEqual(rows, [{"segment_id": "s", "speaker": "A"}])

    def test_picture_text_goes_to_the_translator_only_when_the_option_is_on_and_the_line_is_fresh(self):
        job = {"picture_option": False, "has_video": True}
        with Unit():
            self.assertEqual(ld._picture_context(job, [{"segment_id": "s"}]), "")
            job["picture_option"] = True
            job["analysis"] = {"picture_unit": {"chunks": []}}
            self.assertEqual(ld._picture_context(job, [{"segment_id": "s", "picture_stale": True}]), "")

    def test_mood_hint_is_ignored_for_stale_lines_and_when_off(self):
        job = {"picture_option": True, "analysis": {"picture_unit": {"chunks": [
            {"answer": {"lines": [{"id": "s", "delivery": "shout", "confidence": "high"}]}}]}}}
        row = {"segment_id": "s", "emotion": "neutral"}
        self.assertIsNone(ld._picture_mood(job, row))                       # unit off
        with Unit():
            self.assertIsNone(ld._picture_mood(job, {**row, "picture_stale": True}))
            self.assertIsNone(ld._picture_mood(job, {**row, "emotion": "angry"}))     # a chosen style is never replaced
            job["picture_option"] = False
            self.assertIsNone(ld._picture_mood(job, row))


class EditPathTests(unittest.TestCase):
    """The real editing calls, with the segment file replaced by a list."""
    def make(self):
        rows = [{"segment_id": "s1", "speaker": "Speaker 1", "speaker_id": "sp1", "text": "Hello there.", "start": 1.0, "end": 3.0,
                 "picture_evidence": True, "picture_gender_check": True, "speaker_confidence": "low", "speaker_reasons": ["voters_split"]},
                {"segment_id": "s2", "speaker": "Speaker 2", "speaker_id": "sp2", "text": "Yes.", "start": 4.0, "end": 5.0,
                 "picture_evidence": True}]
        job = {"id": "j", "status": "editing", "picture_option": True,
               "speaker_list": [{"id": "sp1", "name": "Speaker 1", "picture_gender_check": {"x": 1}, "picture_advice": {"gender": "female"}},
                                {"id": "sp2", "name": "Speaker 2"}]}
        return job, rows

    def edit(self, edits):
        job, rows = self.make()
        written = []
        with patch.object(ld, "read_segments", lambda j: rows), patch.object(ld, "_write_segments", lambda j, r: written.append(r)), \
                patch.object(ld, "_save"), patch.object(ld, "_ev"):
            ok, n = ld.update_segments(job, edits)
        return job, rows, ok

    def test_new_english_words_drop_the_picture_marks_but_keep_the_sound_review(self):
        job, rows, ok = self.edit([{"segment_id": "s1", "text": "Hello, friend."}])
        r = rows[0]
        self.assertTrue(ok)
        self.assertTrue(r["picture_stale"])
        self.assertNotIn("picture_gender_check", r)
        self.assertEqual(r["speaker_confidence"], "low")
        self.assertNotIn("picture_gender_check", job["speaker_list"][0])       # nothing carries the speaker's mark any more

    def test_the_same_words_or_only_arabic_change_nothing_about_the_picture(self):
        job, rows, ok = self.edit([{"segment_id": "s1", "text": "Hello there.", "arabic_text": "مرحبا"}])
        r = rows[0]
        self.assertTrue(r["picture_evidence"])
        self.assertTrue(r["picture_gender_check"])
        self.assertNotIn("picture_stale", r)
        self.assertIn("picture_gender_check", job["speaker_list"][0])

    def test_a_speaker_change_drops_everything_about_that_line(self):
        job, rows, ok = self.edit([{"segment_id": "s1", "speaker_id": "sp2"}])
        self.assertNotIn("speaker_confidence", rows[0])
        self.assertTrue(rows[0]["picture_stale"])

    def test_moving_a_line_in_time_drops_its_picture_marks_and_the_speakers_mark(self):
        job, rows = self.make()
        with patch.object(ld, "read_segments", lambda j: rows), patch.object(ld, "_write_segments", lambda j, r: None), \
                patch.object(ld, "_save"), patch.object(ld, "_ev"), patch.object(ld, "_total_secs", lambda j: 100.0):
            ok, err, out = ld.set_line_time(job, "s1", 1.5, 3.2)
        self.assertTrue(ok, err)
        self.assertTrue(rows[0]["picture_stale"])
        self.assertNotIn("picture_gender_check", rows[0])
        self.assertNotIn("picture_gender_check", job["speaker_list"][0])
        self.assertTrue(rows[1]["picture_evidence"])


class FailSafeTests(unittest.TestCase):
    def test_the_analysis_call_site_cannot_stop_the_dub(self):
        src = (ROOT / "longdub_service.py").read_text(encoding="utf-8")
        a = src.index("_picture = _run_picture(job, rows, wd)")
        self.assertIn("try:", src[a - 260:a])
        self.assertIn("except Exception", src[a:a + 260])
        b = src.index("_picture_advice(job, rows, _picture)")
        self.assertIn("try:", src[b - 80:b])

    def test_cut_room_is_off_unless_asked_for(self):
        self.assertFalse(ld.PICTURE_CUT_ROOM)
        src = (ROOT / "longdub_service.py").read_text(encoding="utf-8")
        self.assertIn("if PICTURE_CUT_ROOM:", src)

    def test_unit_is_off_by_default(self):
        self.assertFalse(image_unit.ENABLED)


class PageTests(unittest.TestCase):
    def test_public_wording_goes_live_together_with_the_switch(self):
        """The help, privacy and assistant texts about the picture may be on the site only while the unit is on by default."""
        src = (ROOT / "image_unit.py").read_text(encoding="utf-8")
        on_by_default = "os.environ.get('IMAGE_UNIT', '1')" in src
        priv = (ROOT / "privacy.html").read_text(encoding="utf-8")
        help_ = (ROOT / "help.html").read_text(encoding="utf-8")
        facts = (ROOT / "assistant_service.py").read_text(encoding="utf-8")
        said = ["Optional picture analysis" in priv, "تحليل الصورة (اختياري)" in priv, "Also analyse the picture" in help_,
                "تحليل الصورة أيضاً" in help_, "PICTURE ANALYSIS" in facts]
        self.assertEqual(said, [on_by_default] * 5)

    def test_the_ready_wording_is_kept_for_the_day_it_goes_live(self):
        folder = ROOT / "docs" / "picture-go-live"
        text = "".join((folder / n).read_text(encoding="utf-8") for n in ("privacy.html.patch", "help.html.patch", "assistant_service.py.patch"))
        for needle in ("Optional picture analysis", "تحليل الصورة (اختياري)", "Also analyse the picture", "تحليل الصورة أيضاً", "PICTURE ANALYSIS"):
            self.assertIn(needle, text)
        for bad in ("Gemini", "Google", "OpenAI", "$"):
            self.assertNotIn(bad, text)

    def test_the_upload_page_sends_nothing_new_unless_the_unit_is_available(self):
        html = (ROOT / "dub_long.html").read_text(encoding="utf-8")
        self.assertIn("cfg.picture_available", html)
        self.assertIn("/picture-option", html)

    def test_main_exposes_only_a_yes_no(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"picture_available": image_unit.enabled()', main)
        self.assertNotIn('"picture_usd"', main)


class VoteWiringTests(unittest.TestCase):
    """The picture reaches the vote only as one more voter, only when the project opted in and the result is usable."""
    def run_vote(self, option, picture):
        import tempfile
        import speaker_vote_live as svl
        from test_speaker_merge_ask import FIRST, job, labels, rows
        seen = []
        real = svl.apply

        def spy(rows_, votes, **kw):
            seen.append(kw)
            return real(rows_, votes, **kw)
        j = job()
        j["picture_option"] = option
        j.setdefault("analysis", {})["picture_unit"] = picture
        first = dict(FIRST, dialogue=labels([5, 5, 5, 5]), audio=labels([5, 5, 5, 5]))
        with patch.object(svl, "collect", lambda *a, **k: dict(first, reused=False)), patch.object(svl, "apply", spy), \
                patch.object(ld, "GEMINI_API_KEY", "k"), patch.object(ld, "_mark", lambda *a, **k: None), \
                patch.object(ld, "_ev", lambda *a, **k: None), patch.object(ld, "_save", lambda j: None), \
                patch.object(ld, "_speaker_check_refund", lambda *a, **k: None), tempfile.TemporaryDirectory() as d:
            ld._vote_speakers(j, rows(), Path(d), Path("v.wav"))
        return seen

    def test_a_usable_picture_is_handed_to_the_vote(self):
        pic = {"status": "ok", "chunks": [], "cuts": []}
        with Unit():
            seen = self.run_vote(True, pic)
        self.assertTrue(seen)
        self.assertTrue(all(kw.get("picture") is pic for kw in seen))

    def test_no_picture_means_exactly_the_old_call(self):
        pic = {"status": "ok", "chunks": [], "cuts": []}
        with Unit():
            for option, p in ((False, pic), (True, {"status": "failed"}), (True, None), (None, pic)):
                seen = self.run_vote(option, p)
                self.assertTrue(seen)
                self.assertTrue(all("picture" not in kw for kw in seen), (option, p))
        for kw in self.run_vote(True, pic):               # unit off
            self.assertNotIn("picture", kw)


class ApiQuoteTests(unittest.TestCase):
    """A job made through the public API pays the same analysis charge the website does, so the API's quote and balance check must include it."""
    def est(self, **kw):
        base = {"analysis": 16, "flat": 5, "speaker_check": 8, "total": 120}
        base.update(kw)
        return base

    def test_the_picture_part_is_in_the_amount_due_and_the_breakdown(self):
        import api_money
        n = api_money.numbers_analysis(self.est(picture=28), {})
        self.assertEqual(n["fixed"], 16 + 5 + 8 + 28)
        self.assertIn(("picture_analysis", 28), n["breakdown"])
        self.assertEqual(sum(v for _c, v in n["breakdown"]), n["fixed"])

    def test_an_estimate_without_it_is_exactly_as_before(self):
        import api_money
        n = api_money.numbers_analysis(self.est(), {})
        self.assertEqual(n["fixed"], 29)
        self.assertEqual([c for c, _v in n["breakdown"]], ["analysis", "processing", "speaker_check"])

    def test_the_job_view_shows_the_same_due_amount(self):
        import api_jobs
        view = api_jobs.estimate_view({"id": "j", "estimate": self.est(picture=28), "paid": {}, "status": "estimated"})
        self.assertEqual(view["analysis_due_credits"], 57)
        view = api_jobs.estimate_view({"id": "j", "estimate": self.est(), "paid": {}, "status": "estimated"})
        self.assertEqual(view["analysis_due_credits"], 29)


class PublicViewTests(unittest.TestCase):
    def job(self):
        return {"id": "a" * 8, "status": "editing", "name": "x", "uid": "u", "picture_option": True, "picture_paid": 28,
                "analysis": {"picture_unit": {"charged_usd": 0.1, "chunks": [{"answer": {"screen_text": ["x"]}}]}},
                "speaker_list": [{"id": "s", "name": "A", "picture_gender_check": {"a": 1}, "picture_advice": {"gender": "female"}}]}

    def test_private_numbers_and_raw_answers_never_leave_the_server(self):
        with Unit():
            v = ld.public_view(self.job())
        for key in ("analysis", "picture_paid", "picture_unit", "picture_refund_target"):
            self.assertNotIn(key, v)
        self.assertNotIn("0.1", repr(v))
        self.assertTrue(v["picture_option"])
        self.assertIn("picture_gender_check", v["speaker_list"][0])

    def test_switching_the_unit_off_hides_appearance_advice_everywhere_in_the_view(self):
        v = ld.public_view(self.job())
        self.assertEqual(v["speaker_list"], [{"id": "s", "name": "A"}])

    def test_an_old_project_gets_no_new_field(self):
        job = self.job()
        del job["picture_option"]
        with Unit():
            self.assertNotIn("picture_option", ld.public_view(job))


class ReadBackTests(unittest.TestCase):
    def test_a_saved_gender_mark_is_not_shown_when_the_unit_or_the_mark_is_off(self):
        import json
        import tempfile
        rows = [{"segment_id": "s", "text": "x", "picture_gender_check": True, "arabic_text": "ن"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "segments.json"
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            with patch.object(ld, "_seg_path", lambda job: path):
                self.assertNotIn("picture_gender_check", ld.read_segments({})[0])          # unit off
                with Unit():
                    self.assertTrue(ld.read_segments({})[0]["picture_gender_check"])
                    with patch.object(image_unit, "GENDER_MODE", "off"):
                        self.assertNotIn("picture_gender_check", ld.read_segments({})[0])
                        self.assertEqual(ld.read_segments({})[0]["arabic_text"], "ن")


class PriceParsingTests(unittest.TestCase):
    def setUp(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        m = re.search(r"def _picture_num\(value, default\):.*?\n\n\n", src, re.S)
        scope = {}
        exec(m.group(0).replace("\r", ""), scope)
        self.num = scope["_picture_num"]

    def test_only_finite_non_negative_numbers_are_taken(self):
        self.assertEqual(self.num("3.5", 3), 3.5)
        for bad in ("nan", "inf", "-inf", "x", None, "", True):
            self.assertEqual(self.num(bad, 3), 3.0, bad)
        self.assertEqual(self.num("-4", 3), 0.0)


if __name__ == "__main__":
    unittest.main()
