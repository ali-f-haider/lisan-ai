"""The live side of the speaker vote: the two listening requests, saved answers, applying the vote, the price line and its refund.
Offline: the listening service is a stand-in function, the data is invented (no film dialogue)."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speaker_vote_live as live
import longdub_service as ld


def rows_for(speakers):
    """speakers: list of names, one per line; lines are 3 s apart."""
    return [{"segment_id": f"seg_{i}", "start": float(i * 3), "end": float(i * 3 + 2), "text": f"invented line number {i}",
             "speaker": s} for i, s in enumerate(speakers)]


def answer(labels, people=None):
    lines = [{"id": f"seg_{i}", "speaker": s} for i, s in enumerate(labels)]
    return {"people": people or len(set(labels)), "lines": lines}


def response(payload, usd_tokens=(100, 50, 20)):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}],
            "usageMetadata": {"promptTokenCount": usd_tokens[0], "candidatesTokenCount": usd_tokens[1], "thoughtsTokenCount": usd_tokens[2]}}


# three people; the detector wrongly gives lines 2, 5 and 8 (short replies) to Speaker 1
TRUTH = ["A", "B", "A", "B", "A", "C", "A", "B", "C", "A", "B", "A", "C", "B", "A", "C", "A", "B", "C", "A"]
APP = ["Speaker 1" if t == "A" else "Speaker 2" if t == "B" else "Speaker 3" for t in TRUTH]
for i in (3, 7, 13):
    APP[i] = "Speaker 1"       # three wrong lines


class FitsAndPromptTests(unittest.TestCase):
    def test_limit_is_the_dubbing_limit(self):
        self.assertTrue(live.fits(60 * 60))
        self.assertTrue(live.fits(8 * 60))
        self.assertFalse(live.fits(61 * 60 + 5))
        self.assertFalse(live.fits(0))
        self.assertFalse(live.fits("x"))

    def test_dialogue_prompt_has_no_audio_words_and_audio_prompt_has(self):
        rows = rows_for(APP[:3])
        self.assertNotIn("isolated voices", live.prompt_for("dialogue", rows))
        self.assertIn("isolated voices", live.prompt_for("audio", rows))
        for kind in ("dialogue", "audio"):
            text = live.prompt_for(kind, rows).lower()
            for vendor in ("gemini", "google", "openai", "anthropic", "claude", "pyannote"):
                self.assertNotIn(vendor, text)
            self.assertIn('"id": "seg_0"', text)

    def test_the_current_speaker_is_never_sent(self):
        text = live.prompt_for("dialogue", rows_for(["Speaker 9"] * 3))
        self.assertNotIn("Speaker 9", text)

    def test_output_room_grows_with_the_number_of_lines(self):
        self.assertGreater(live._limit(700), live._limit(92))
        self.assertGreater(live._limit(92), 16384)           # more than the room the 92-line clip needed
        self.assertLessEqual(live._limit(5000), 65536)
        self.assertEqual(live._limit(92, retry=True), 65536)
        cfg = live._payload("dialogue", rows_for(APP), None)["generationConfig"]
        self.assertEqual(cfg["thinkingConfig"], {"thinkingLevel": "medium"})


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self.tmp.name)
        self.rows = rows_for(APP)
        self.calls = []
        # the voters' letters are arbitrary and different from the app's names
        self.dialogue = [{"A": "q", "B": "r", "C": "s"}[t] for t in TRUTH]
        self.audio = [{"A": "x", "B": "y", "C": "z"}[t] for t in TRUTH]

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, api_key, payload, timeout):
        has_audio = any("inline_data" in p for p in payload["contents"][0]["parts"])
        self.calls.append("audio" if has_audio else "dialogue")
        return response(answer(self.audio if has_audio else self.dialogue)), None

    def run_collect(self, ask=None, with_audio=True, key="k"):
        vocals = self.wd / "vocals.wav"
        vocals.write_bytes(b"x")
        small = self.wd / "v.mp3"
        small.write_bytes(b"y" * 1000)
        with mock.patch.object(live, "shrink_audio", return_value=small if with_audio else None):
            return live.collect("job1", self.rows, vocals, self.wd, key, ask=ask or self.ask)

    def test_two_requests_two_voters_cost_recorded(self):
        out = self.run_collect()
        self.assertEqual(sorted(self.calls), ["audio", "dialogue"])
        self.assertEqual(len(out["dialogue"]), len(self.rows))
        self.assertEqual(len(out["audio"]), len(self.rows))
        self.assertEqual(out["usage"]["calls"], 2)
        self.assertEqual(out["usage"]["tokens_in"], 200)
        self.assertFalse(out["reused"])

    def test_saved_answers_are_reused_after_a_restart(self):
        self.run_collect()
        self.calls.clear()
        again = self.run_collect()
        self.assertEqual(self.calls, [])
        self.assertTrue(again["reused"])
        self.assertEqual(again["usage"]["calls"], 2)

    def test_changed_lines_ask_again(self):
        self.run_collect()
        self.calls.clear()
        self.rows[0]["text"] = "invented line number zero, edited"
        self.run_collect()
        self.assertEqual(len(self.calls), 2)

    def test_no_audio_means_only_the_dialogue_request(self):
        out = self.run_collect(with_audio=False)
        self.assertEqual(self.calls, ["dialogue"])
        self.assertIsNone(out["audio"])

    def test_no_key_means_no_request_and_no_voters(self):
        out = self.run_collect(key="")
        self.assertEqual(self.calls, [])
        self.assertIsNone(out["dialogue"])

    def test_a_failing_request_is_an_unavailable_voter_not_a_crash(self):
        def boom(api_key, payload, timeout):
            raise TimeoutError("x")
        out = self.run_collect(ask=boom)
        self.assertIsNone(out["dialogue"])
        self.assertIsNone(out["audio"])

    def test_an_unusable_answer_is_tried_once_more_with_more_room(self):
        seen = []

        def flaky(api_key, payload, timeout):
            seen.append(payload["generationConfig"]["maxOutputTokens"])
            if len(seen) == 1:
                return {"candidates": [{"content": {"parts": [{"text": "{\"people\": 3, \"lines\": [{\"id\": \"seg_0\", \"spe"}]}}]}, None
            return response(answer(self.dialogue)), None
        out = self.run_collect(ask=flaky, with_audio=False)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1], 65536)
        self.assertIsNotNone(out["dialogue"])

    def test_thinking_parts_are_not_read_as_the_answer(self):
        data = response(answer(self.dialogue))
        data["candidates"][0]["content"]["parts"].insert(0, {"text": "thinking out loud", "thought": True})
        self.assertNotIn("thinking", live._answer_text(data))


class ApplyTests(unittest.TestCase):
    def test_the_vote_fixes_the_detector_and_marks_lines(self):
        rows = rows_for(APP)
        votes = {"dialogue": {f"seg_{i}": {"A": "q", "B": "r", "C": "s"}[t] for i, t in enumerate(TRUTH)},
                 "audio": {f"seg_{i}": {"A": "x", "B": "y", "C": "z"}[t] for i, t in enumerate(TRUTH)}}
        s = live.apply(rows, votes)
        self.assertEqual(s["status"], "ok")
        self.assertEqual(s["changed"], 3)
        self.assertEqual([r["speaker"] for r in rows], APP_FIXED)
        self.assertTrue(all("speaker_vote" in r for r in rows))
        self.assertEqual(s["new_speakers"], [])

    def test_one_voter_alone_changes_nothing(self):
        rows = rows_for(APP)
        before = copy.deepcopy(rows)
        s = live.apply(rows, {"dialogue": {f"seg_{i}": "q" for i in range(len(rows))}, "audio": None})
        self.assertNotEqual(s["status"], "ok")
        self.assertEqual(rows, before)

    def test_malformed_input_never_raises(self):
        rows = rows_for(APP)
        self.assertNotEqual(live.apply(rows, {})["status"], "ok")
        self.assertNotEqual(live.apply([{"nope": 1}], {"dialogue": {}, "audio": {}})["status"], "ok")

    def test_new_person_found_by_two_voters_is_added_and_names_have_no_gap(self):
        # the detector split one real person across two phantom speakers (4 and 5) and missed another real person
        # (like a real film: the missed person has fewer lines than the person the detector merged them into)
        truth = list("ABADAABADAADABADADABDD")
        app = [{"A": "Speaker 1", "B": "Speaker 2", "D": "Speaker 1"}[t] for t in truth]
        app[4] = "Speaker 4"
        app[5] = "Speaker 4"
        app[9] = "Speaker 5"
        rows = rows_for(app)
        votes = {"dialogue": {f"seg_{i}": t.lower() for i, t in enumerate(truth)},
                 "audio": {f"seg_{i}": t.upper() + t.upper() for i, t in enumerate(truth)}}
        s = live.apply(rows, votes)
        self.assertEqual(s["status"], "ok")
        self.assertEqual(len(s["new_speakers"]), 1)
        self.assertEqual(s["merged"], 2)
        rename = live.compact_names(rows)
        self.assertEqual(sorted({r["speaker"] for r in rows}), ["Speaker 1", "Speaker 2", "Speaker 3"])
        self.assertTrue(rename)

    def test_compact_names_leaves_custom_names_and_is_stable(self):
        rows = rows_for(["Speaker 1", "Anna", "Speaker 6", "Speaker 6", "Speaker 3"])
        rename = live.compact_names(rows)
        self.assertEqual(rename, {"Speaker 3": "Speaker 2", "Speaker 6": "Speaker 3"})
        self.assertEqual([r["speaker"] for r in rows], ["Speaker 1", "Anna", "Speaker 3", "Speaker 3", "Speaker 2"])
        self.assertEqual(live.compact_names(rows), {})

    def test_review_from_vote(self):
        self.assertIsNone(live.review_from_vote({}))
        sure = {"speaker_vote": {"badge": False, "reasons": ["tie_broken_by_dialogue"]}}
        self.assertEqual(live.review_from_vote(sure), {"speaker_confidence": "high", "speaker_reasons": []})
        split = {"speaker_vote": {"badge": True, "reasons": ["voters_split"]}}
        self.assertEqual(live.review_from_vote(split), {"speaker_confidence": "low", "speaker_reasons": ["voters_split"]})
        unruled = {"speaker_vote": {"badge": False, "reasons": ["insufficient_line_voters"]}}
        self.assertIsNone(live.review_from_vote(unruled))


APP_FIXED = ["Speaker 1" if t == "A" else "Speaker 2" if t == "B" else "Speaker 3" for t in TRUTH]


@mock.patch.object(ld, "_lip_rate", lambda cfg, res=None: 40.0)
class PriceLineTests(unittest.TestCase):
    CFG = {"fee": 3, "analysis_per_min": 2, "flat": 10, "chars_per_credit": 60, "clone_credits": 5, "merge_credits": 1,
           "speaker_check_flat": 4, "speaker_check_per_min": 0.4}

    def test_the_confirmed_prices(self):
        for minutes, credits in ((7.8, 8), (30, 16), (60, 28)):
            est = ld.compute_estimate(minutes * 60, self.CFG, 3)
            self.assertEqual(est["speaker_check"], credits, minutes)

    def test_it_is_part_of_the_total_and_absent_when_not_configured(self):
        with_it = ld.compute_estimate(600, self.CFG, 3)
        without = ld.compute_estimate(600, {k: v for k, v in self.CFG.items() if not k.startswith("speaker_check")}, 3)
        self.assertEqual(with_it["total"] - without["total"], with_it["speaker_check"])
        self.assertEqual(without["speaker_check"], 0)

    def test_no_charge_for_a_video_the_check_cannot_run_on(self):
        self.assertEqual(ld.compute_estimate(75 * 60, self.CFG, 3)["speaker_check"], 0)


class VoteStepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self.tmp.name)
        self.job = {"id": "j1", "uid": "u1", "analysis": {"audio_duration": 480.0}, "speaker_check_paid": 8, "paid": {"analysis": 20}}
        self.events = []
        self.p = [mock.patch.object(ld, "_ev", lambda j, step, status="ok", detail="", credits=None: self.events.append((step, status, detail))),
                  mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_mark", lambda *a, **k: None)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        self.tmp.cleanup()

    def votes(self):
        return {"dialogue": {f"seg_{i}": {"A": "q", "B": "r", "C": "s"}[t] for i, t in enumerate(TRUTH)},
                "audio": {f"seg_{i}": {"A": "x", "B": "y", "C": "z"}[t] for i, t in enumerate(TRUTH)},
                "usage": {"usd": 0.047, "calls": 2, "tokens_in": 20000, "tokens_out": 3000, "tokens_thinking": 9000}, "reused": False}

    def test_a_good_vote_changes_lines_keeps_the_charge_and_logs_counts_only(self):
        rows = rows_for(APP)
        refund = mock.Mock()
        with mock.patch.object(live, "collect", return_value=self.votes()), mock.patch.object(ld, "_speaker_check_refund", refund):
            s = ld._vote_speakers(self.job, rows, self.wd, self.wd / "v.wav")
        self.assertEqual(s["status"], "ok")
        self.assertEqual([r["speaker"] for r in rows], APP_FIXED)
        refund.assert_not_called()
        step, status, detail = self.events[-1]
        self.assertEqual((step, status), ("speaker_vote", "ok"))
        self.assertIn("3 lines moved", detail)
        self.assertIn("$0.0470", detail)
        self.assertNotIn("invented line", detail)

    def test_a_vote_that_cannot_act_leaves_every_line_and_returns_the_charge(self):
        rows = rows_for(APP)
        before = copy.deepcopy(rows)
        refund = mock.Mock()
        bad = {"dialogue": None, "audio": None, "usage": {"usd": 0.01, "calls": 2}, "reused": False}
        with mock.patch.object(live, "collect", return_value=bad), mock.patch.object(ld, "_speaker_check_refund", refund):
            s = ld._vote_speakers(self.job, rows, self.wd, self.wd / "v.wav")
        self.assertNotEqual(s["status"], "ok")
        self.assertEqual(rows, before)
        refund.assert_called_once()

    def test_an_error_inside_never_stops_the_analysis(self):
        rows = rows_for(APP)
        with mock.patch.object(live, "collect", side_effect=RuntimeError("boom")), mock.patch.object(ld, "_speaker_check_refund", mock.Mock()):
            s = ld._vote_speakers(self.job, rows, self.wd, self.wd / "v.wav")
        self.assertEqual(s["status"], "error")

    def test_a_video_outside_the_limit_is_skipped_and_refunded(self):
        self.job["analysis"]["audio_duration"] = 90 * 60.0
        refund = mock.Mock()
        collect = mock.Mock()
        with mock.patch.object(live, "collect", collect), mock.patch.object(ld, "_speaker_check_refund", refund):
            s = ld._vote_speakers(self.job, rows_for(APP), self.wd, self.wd / "v.wav")
        collect.assert_not_called()
        self.assertEqual(s["status"], "skipped")
        refund.assert_called_once()

    def test_review_uses_the_vote_for_voted_lines_and_a_new_speaker_gets_the_plain_message(self):
        rows = rows_for(APP)
        with mock.patch.object(live, "collect", return_value=self.votes()):
            vote = ld._vote_speakers(self.job, rows, self.wd, self.wd / "v.wav")
        vote["new_speakers"] = ["Speaker 4"]
        job = {"id": "j", "stated_speakers": 2}
        ld._init_speakers(job, rows)
        ld._review_speakers(job, rows, [], {}, vote=vote)
        self.assertTrue(all(r["speaker_confidence"] in ("low", "high") for r in rows))
        self.assertEqual(job["warnings"], [live.ADDED_SPEAKER_MESSAGE])
        self.assertEqual(job["speaker_count_review"], {"stated": 2, "detected": 3, "needs_review": True})

    def test_a_user_edit_drops_the_vote_evidence_too(self):
        r = {"speaker_vote": {"badge": True}, "speaker_confidence": "low", "speaker_reasons": ["voters_split"]}
        ld._clear_speaker_review(r)
        self.assertEqual(r, {})


class RefundTests(unittest.TestCase):
    def test_refund_once_and_only_when_confirmed(self):
        job = {"id": "j", "uid": "u", "speaker_check_paid": 8}
        with mock.patch.object(ld, "_refund", return_value=True) as rf, mock.patch.object(ld, "_save"), mock.patch.object(ld, "_ev"):
            self.assertEqual(ld._speaker_check_refund(job, "x"), 8)
            self.assertEqual(ld._speaker_check_refund(job, "x"), 0)       # already returned
        rf.assert_called_once_with(job, "u", 8, "analysis", "speaker_check_unused")
        job2 = {"id": "j", "uid": "u", "speaker_check_paid": 8}
        with mock.patch.object(ld, "_refund", return_value=False), mock.patch.object(ld, "_save"), mock.patch.object(ld, "_ev"):
            self.assertEqual(ld._speaker_check_refund(job2, "x"), 0)
        self.assertEqual(job2["speaker_check_paid"], 8)
        self.assertEqual(job2["speaker_check_refund_pending"], 8)

    def test_nothing_paid_nothing_refunded(self):
        with mock.patch.object(ld, "_refund") as rf:
            self.assertEqual(ld._speaker_check_refund({"id": "j", "uid": "u"}, "x"), 0)
        rf.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class AcceptChargesTheCheckTests(unittest.TestCase):
    def accept(self, est):
        job = {"id": "j1", "uid": "u1", "status": "estimated", "estimate": est, "paid": {"fee": 3, "analysis": 0, "dub": 0},
               "reserved_output_bytes": 0}
        charged = []

        def charge(j, slot, uid, amount, action):
            charged.append((slot, amount, action))
            return True
        hooks = NS(capacity=lambda j, b: (True, "", 200), get_credits=lambda uid: 1000)
        with mock.patch.object(ld, "Hooks", hooks), mock.patch.object(ld, "_charge", charge), mock.patch.object(ld, "_debit_ok", lambda r: bool(r)), \
                mock.patch.object(ld, "_save"), mock.patch.object(ld, "_ev"), mock.patch.object(ld, "start_worker"):
            ok, err = ld.accept(job, "u1", agreed=True)
        return job, charged, ok

    def test_the_check_is_charged_with_the_analysis_and_remembered(self):
        job, charged, ok = self.accept({"analysis": 16, "flat": 10, "speaker_check": 8, "total": 99})
        self.assertTrue(ok)
        self.assertEqual(charged, [("analysis", 34, "long_dub_analysis")])
        self.assertEqual(job["paid"]["analysis"], 34)
        self.assertEqual(job["speaker_check_paid"], 8)

    def test_an_older_estimate_without_the_check_is_charged_as_before(self):
        job, charged, ok = self.accept({"analysis": 16, "flat": 10, "total": 99})
        self.assertEqual(charged, [("analysis", 26, "long_dub_analysis")])
        self.assertEqual(job["speaker_check_paid"], 0)


class SourceRulesTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_the_dubbing_limit_is_the_check_limit(self):
        src = (self.ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"max_min": longdub_service.speaker_vote_live.ENGINE_MAX_MIN', src)

    def test_customer_words_name_no_vendor(self):
        words = [live.ADDED_SPEAKER_MESSAGE]
        page = (self.ROOT / "dub_long.html").read_text(encoding="utf-8")
        for key in ("estSpkRow", "estSpkSub"):
            for line in page.splitlines():
                if key + ":" in line:
                    words.append(line[line.index(key + ":"):line.index(key + ":") + 220])
        for w in words:
            for vendor in ("gemini", "google", "openai", "anthropic", "claude", "pyannote", "whisper", "demucs"):
                self.assertNotIn(vendor, w.lower())

    def test_the_voiceprint_voter_is_not_used_yet(self):
        src = (self.ROOT / "speaker_vote_live.py").read_text(encoding="utf-8")
        self.assertIn('"voice": None', src)
