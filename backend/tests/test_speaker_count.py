"""Asking the user "you said N, we found M, change it to M?" and treating the answer as a fact. Offline, invented data."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import longdub_service as ld
import speaker_count as sc

ROOT = Path(__file__).resolve().parents[1]


def turns(*seconds):
    """One long turn per voice, as many seconds as given."""
    out, t = [], 0.0
    for i, s in enumerate(seconds):
        out.append({"start": t, "end": t + s, "speaker": f"V{i}"})
        t += s + 1
    return out


DUTCH = turns(40, 12, 11, 8)        # the clip from the log: four voices, 71 s of speech


class Pure(unittest.TestCase):
    def test_voices_with_real_speech_only(self):
        self.assertEqual(sc.real_voices(DUTCH), [40.0, 12.0, 11.0, 8.0])
        self.assertEqual(sc.real_voices(turns(40, 12, 1.5, 2)), [40.0, 12.0])        # a cough or a one-word voice does not count
        self.assertEqual(sc.real_voices(turns(1, 1)), [1.0])                          # at least one voice always counts
        self.assertEqual(sc.real_voices([]), [])

    def test_a_share_below_four_percent_does_not_count(self):
        self.assertEqual(sc.real_voices(turns(300, 10)), [300.0])                     # 10 s is more than 5 s but only 3% of the speech

    def test_ask_only_when_more_real_voices_than_stated(self):
        a = sc.ask_for(DUTCH, 2)
        self.assertEqual(a, {"stated": 2, "found": 4, "seconds": [40, 12, 11, 8]})
        self.assertIsNone(sc.ask_for(DUTCH, 4))
        self.assertIsNone(sc.ask_for(DUTCH, 5))
        self.assertIsNone(sc.ask_for(turns(40, 12), 3))            # fewer than stated: never asked
        self.assertIsNone(sc.ask_for(turns(40, 12, 1.5), 2))
        self.assertIsNone(sc.ask_for(DUTCH, 0))
        self.assertIsNone(sc.ask_for(DUTCH, "x"))
        self.assertIsNone(sc.ask_for([], 2))

    def test_bad_turns_do_not_raise(self):
        bad = [{"start": "a", "end": 3, "speaker": "x"}, {"end": 3}, None, {"start": 1, "end": 2}]
        self.assertEqual(sc.voice_seconds(bad), {})
        self.assertEqual(sc.clusters(bad), 0)

    def test_the_answer_must_be_one_of_the_two_offered(self):
        ask = {"stated": 2, "found": 4}
        self.assertEqual(sc.valid_choice(2, ask), 2)
        self.assertEqual(sc.valid_choice("4", ask), 4)
        for bad in (3, 0, None, "x", True, 99):
            self.assertIsNone(sc.valid_choice(bad, ask), bad)
        self.assertIsNone(sc.valid_choice(2, None))


class FakeSlot:
    def __init__(self):
        self.taken = self.dropped = 0

    def take(self):
        self.taken += 1

    def drop(self):
        self.dropped += 1


class FindVoices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self.tmp.name)
        self.slot = FakeSlot()
        self.events = []
        self.job = {"id": "j", "uid": "u", "stated_speakers": 2, "status": "analyzing"}
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_find(self, free=DUTCH, exact_result=None, token="tok", ask_enabled=True, fail_all=False):
        fake = mock.MagicMock()

        def get(path, tok, count, min_speakers=None):
            self.calls.append((count, min_speakers))
            if fail_all:
                raise RuntimeError("down")
            if count:
                return exact_result if exact_result is not None else turns(*([30] * count))
            return free
        fake.get_speaker_turns.side_effect = get
        with mock.patch.dict(sys.modules, {"whisper_service": fake}), mock.patch.object(ld, "HF_TOKEN", token), \
                mock.patch.object(ld, "SPEAKER_COUNT_ASK", ask_enabled), \
                mock.patch.object(ld, "_ev", lambda j, step, status="ok", detail="", *a, **k: self.events.append((step, status, detail))), \
                mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_mark", lambda *a, **k: None):
            return ld._find_voices(self.job, self.wd, self.wd / "vocals.wav", self.slot)

    def test_no_speaker_model(self):
        self.assertEqual(self.run_find(token=""), ("skipped", [], False))
        self.assertEqual(self.calls, [])

    def test_pauses_and_asks_when_more_voices_than_stated(self):
        state, tr, exact = self.run_find()
        self.assertEqual(state, "asked")
        self.assertEqual(self.job["status"], "speaker_count")
        self.assertEqual(self.job["speaker_count_ask"], {"stated": 2, "found": 4, "seconds": [40, 12, 11, 8]})
        self.assertEqual(self.calls, [(0, 2)])                                   # one free run with the stated number as a minimum
        self.assertEqual(json.loads((self.wd / "turns_free.json").read_text()), DUTCH)
        self.assertEqual(self.events[-1][0], "speaker_count_asked")
        self.assertIn("you said 2", self.events[-1][2])
        self.assertEqual((self.slot.taken, self.slot.dropped), (1, 1))

    def test_keeping_the_stated_number_detects_again_with_exactly_that_many(self):
        self.run_find()
        self.job.update(status="accepted", speaker_count_choice=2)
        self.calls.clear()
        two = turns(50, 21)
        state, tr, exact = self.run_find(exact_result=two)
        self.assertEqual((state, tr, exact), ("ok", two, True))
        self.assertEqual(self.calls, [(2, None)])                                # the first run is reused, only the exact one runs
        self.assertEqual(self.job["stated_speakers"], 2)
        self.assertNotIn("stated_speakers_asked", self.job)

    def test_taking_the_found_number_reuses_the_first_run_and_changes_the_count(self):
        self.run_find()
        self.job.update(status="accepted", speaker_count_choice=4)
        self.calls.clear()
        state, tr, exact = self.run_find()
        self.assertEqual((state, tr, exact), ("ok", DUTCH, True))
        self.assertEqual(self.calls, [])                                         # four voices already, nothing to run again
        self.assertEqual(self.job["stated_speakers"], 4)
        self.assertEqual(self.job["stated_speakers_asked"], 2)

    def test_matching_count_asks_nothing_and_runs_once(self):
        two = turns(50, 21)
        state, tr, exact = self.run_find(free=two)
        self.assertEqual((state, tr, exact), ("ok", two, True))
        self.assertEqual(self.calls, [(0, 2)])
        self.assertEqual(self.job["status"], "analyzing")

    def test_a_tiny_extra_voice_asks_nothing_but_is_folded_in_by_an_exact_run(self):
        three = turns(50, 21, 1.5)
        state, tr, exact = self.run_find(free=three, exact_result=turns(50, 22))
        self.assertEqual(state, "ok")
        self.assertEqual(self.calls, [(0, 2), (2, None)])
        self.assertEqual(self.job["status"], "analyzing")
        self.assertTrue(exact)

    def test_asking_can_be_switched_off(self):
        state, tr, exact = self.run_find(ask_enabled=False, exact_result=turns(50, 21))
        self.assertEqual(state, "ok")
        self.assertEqual(self.job["status"], "analyzing")
        self.assertEqual(self.calls, [(0, 2), (2, None)])

    def test_a_failing_exact_run_falls_back_to_the_first_result(self):
        self.job["speaker_count_choice"] = 2
        state, tr, exact = self.run_find(exact_result=[])
        self.assertEqual((state, tr, exact), ("ok", DUTCH, False))

    def test_detection_that_cannot_run_is_reported_not_fatal(self):
        state, tr, exact = self.run_find(fail_all=True)
        self.assertEqual((state, tr), ("failed", []))
        self.assertTrue(any("couldn't tell the speakers apart" in w for w in self.job["warnings"]))
        self.assertEqual((self.slot.taken, self.slot.dropped), (1, 1))

    def test_nothing_found(self):
        self.assertEqual(self.run_find(free=[])[0], "failed")


class RunDetection(unittest.TestCase):
    def run_it(self, exact_n, min_n, behaviour):
        fake = mock.MagicMock()
        calls = []

        def get(path, tok, count, min_speakers=None):
            calls.append((count, min_speakers))
            r = behaviour(len(calls))
            if isinstance(r, Exception):
                raise r
            return r
        fake.get_speaker_turns.side_effect = get
        slot = FakeSlot()
        with mock.patch.dict(sys.modules, {"whisper_service": fake}), mock.patch.object(ld, "HF_TOKEN", "t"):
            out = ld._run_detection(slot, "v.wav", exact_n, min_n)
        return out, calls, slot, fake

    def test_exact_first_then_at_least_then_no_hint(self):
        out, calls, slot, fake = self.run_it(2, 0, lambda n: RuntimeError("x") if n < 3 else DUTCH)
        self.assertEqual(out, DUTCH)
        self.assertEqual(calls, [(2, None), (0, 2), (0, None)])
        fake._release_diarization_pipeline.assert_called_once()
        self.assertEqual((slot.taken, slot.dropped), (1, 1))

    def test_minimum_run_for_the_first_look(self):
        out, calls, slot, fake = self.run_it(0, 3, lambda n: DUTCH)
        self.assertEqual(calls, [(0, 3)])

    def test_total_failure_returns_none_and_still_cleans_up(self):
        out, calls, slot, fake = self.run_it(2, 0, lambda n: RuntimeError("x"))
        self.assertIsNone(out)
        self.assertEqual((slot.taken, slot.dropped), (1, 1))
        fake._release_diarization_pipeline.assert_called_once()


class Choose(unittest.TestCase):
    def job(self, **kw):
        j = {"id": "j", "uid": "u", "status": "speaker_count", "speaker_count_ask": {"stated": 2, "found": 4, "seconds": [40, 12, 11, 8]}}
        j.update(kw)
        return j

    def run_choose(self, job, count):
        started = []
        with mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_ev", lambda *a, **k: None), \
                mock.patch.object(ld, "start_worker", lambda i: started.append(i) or True):
            res = ld.choose_speaker_count(job, count)
        return res, started

    def test_a_valid_answer_resumes_the_job(self):
        job = self.job()
        (ok, err), started = self.run_choose(job, 4)
        self.assertTrue(ok)
        self.assertEqual(job["speaker_count_choice"], 4)
        self.assertEqual(job["status"], "accepted")
        self.assertNotIn("speaker_count_ask", job)
        self.assertEqual(started, ["j"])

    def test_keeping_the_stated_number_is_also_an_answer(self):
        job = self.job()
        (ok, err), started = self.run_choose(job, 2)
        self.assertTrue(ok)
        self.assertEqual(job["speaker_count_choice"], 2)

    def test_other_numbers_and_wrong_states_are_refused(self):
        job = self.job()
        (ok, err), started = self.run_choose(job, 3)
        self.assertFalse(ok)
        self.assertEqual(err[1], 400)
        self.assertEqual(started, [])
        self.assertEqual(job["status"], "speaker_count")
        (ok, err), _ = self.run_choose(self.job(status="analyzing"), 4)
        self.assertEqual((ok, err[1]), (False, 409))

    def test_refused_while_a_worker_is_still_running(self):
        with mock.patch.object(ld, "_RUNNING", {"j"}):
            (ok, err), started = self.run_choose(self.job(), 4)
        self.assertEqual((ok, err[1]), (False, 409))


class Wiring(unittest.TestCase):
    def test_the_analysis_stops_when_it_asks(self):
        src = (ROOT / "longdub_service.py").read_text(encoding="utf-8").replace("\r\n", "\n")
        i = src.index("_found, turns, exact = _find_voices(job, wd, vocals_all, slot)")
        self.assertIn('if _found == "asked":\n                return', src[i:i + 400])

    def test_the_answer_reaches_the_page_and_the_route_exists(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/longdub/{job_id}/speaker-count")', main)
        self.assertIn("longdub_service.choose_speaker_count(job, body.get(\"count\"))", main)
        self.assertIn('"speaker_count_ask"', (ROOT / "longdub_service.py").read_text(encoding="utf-8"))

    def test_the_page_asks_in_english_and_arabic(self):
        h = (ROOT / "dub_long.html").read_text(encoding="utf-8")
        self.assertEqual(h.count("spkCountText:"), 2)
        self.assertEqual(h.count("spkCountKeep:"), 2)
        self.assertEqual(h.count("spkCountUse:"), 2)
        self.assertEqual(h.count("spkCountNote:"), 2)
        self.assertIn('/speaker-count"', h)
        self.assertIn('id="spkKeep"', h)
        self.assertIn('id="spkUse"', h)
        self.assertIn('j.status === "speaker_count"', h)
        self.assertIn("speaker_count: \"Waiting for your answer\"", h)
        self.assertIn("speaker_count: \"بانتظار إجابتك\"", h)


if __name__ == "__main__":
    unittest.main()
