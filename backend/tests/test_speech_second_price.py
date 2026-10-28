"""The second listen is charged like the speaker check (flat + per minute, at Argon's December 2026 price) and returned when it cannot run."""
import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import longdub_service as ld
import speech_second


@mock.patch.object(ld, "_lip_rate", lambda cfg, res=None: 40.0)
class Price(unittest.TestCase):
    CFG = {"fee": 3, "analysis_per_min": 2, "flat": 10, "chars_per_credit": 60, "clone_credits": 5, "merge_credits": 1,
           "speaker_check_flat": ld.SPEAKER_CHECK_FLAT, "speaker_check_per_min": ld.SPEAKER_CHECK_PER_MIN,
           "speech_check_flat": ld.SPEECH_CHECK_FLAT, "speech_check_per_min": ld.SPEECH_CHECK_PER_MIN}

    def test_december_prices_for_the_test_clip(self):
        est = ld.compute_estimate(488.8, self.CFG, 13)
        self.assertEqual(est["speech_check"], 19)          # 2 + 2 per minute: about $0.155 of Argon at the December price
        self.assertEqual(est["speaker_check"], 25)         # 4 + 2.5 per minute: about $0.195 of Argon at the December price
        without = ld.compute_estimate(488.8, {k: v for k, v in self.CFG.items() if not k.startswith("speech_check")}, 13)
        self.assertEqual(est["total"] - without["total"], 19)

    def test_not_charged_when_switched_off_or_too_long(self):
        with mock.patch.object(speech_second, "ENABLED", False):
            self.assertEqual(ld.compute_estimate(488.8, self.CFG, 3)["speech_check"], 0)
        self.assertEqual(ld.compute_estimate(75 * 60, self.CFG, 3)["speech_check"], 0)

    def test_paid_with_the_analysis_and_in_the_api_breakdown(self):
        import api_money, api_jobs
        est = {"analysis": 16, "flat": 10, "speaker_check": 25, "speech_check": 19, "total": 200}
        n = api_money.numbers_analysis(est, {})
        self.assertEqual(n["fixed"], 16 + 10 + 25 + 19)
        self.assertIn(("second_listen", 19), n["breakdown"])
        self.assertEqual(api_jobs.estimate_view({"id": "j", "estimate": est, "paid": {}, "status": "estimated"})["analysis_due_credits"], 70)
        src = inspect.getsource(ld)
        self.assertIn('int(est.get("speech_check", 0) or 0)', src[src.index("need_now ="):src.index("need_now =") + 400])
        self.assertIn('job["speech_check_paid"] = int(est.get("speech_check", 0) or 0)', src)


class Refund(unittest.TestCase):
    def run_listener(self, heard, job=None):
        refunds = []
        job = job or {"id": "j", "uid": "u", "analysis": {"audio_duration": 100}, "speech_check_paid": 19}
        with mock.patch.object(ld, "GEMINI_API_KEY", "k"), mock.patch.object(ld, "_mark", lambda *a, **k: None), \
                mock.patch.object(ld, "_ev", lambda *a, **k: None), \
                mock.patch.object(ld, "_speech_check_refund", lambda j, why: refunds.append(why)), \
                mock.patch.object(speech_second, "listen", lambda *a, **k: heard):
            voc = mock.MagicMock(); voc.exists.return_value = True
            rows = [{"segment_id": "seg_0", "start": 0.0, "end": 1.0, "text": "hello there friend", "speaker": "Speaker 1",
                     "words": [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(["hello", "there", "friend"])]}]
            out = ld._second_listener(job, rows, Path("."), voc, [], {})
        return out, refunds

    def test_returned_when_no_usable_answer(self):
        out, refunds = self.run_listener({"lines": None, "model": None})
        self.assertEqual(len(refunds), 1)
        self.assertEqual(len(out), 1)

    def test_kept_when_it_ran(self):
        out, refunds = self.run_listener({"lines": [{"start": 0.0, "end": 1.0, "text": "Hello there, friend."}], "model": "gemini-4-argon", "usd": 0.1, "seconds": 3})
        self.assertEqual(refunds, [])

    def test_returned_with_a_subtitle(self):
        _, refunds = self.run_listener({"lines": None}, job={"id": "j", "uid": "u", "subtitle": {"name": "x.srt"}, "speech_check_paid": 19})
        self.assertEqual(len(refunds), 1)


class Wiring(unittest.TestCase):
    def test_price_settings_and_page_line(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        self.assertIn('"speech_check_flat": int(_num(os.environ.get("SPEECH_CHECK_FLAT_CREDITS")', main)
        self.assertIn('"speech_check_flat": p["speech_check_flat"]', main)
        html = (root / "dub_long.html").read_text(encoding="utf-8")
        self.assertIn("qli(T.estSpeechRow, e.speech_check, T.estSpeechSub)", html)
        self.assertIn("(e.speech_check || 0)", html)
        import api_settings
        self.assertIn("speech_check_flat", api_settings._INT_CREDIT_KEYS)
        self.assertIn("speech_check_per_min", api_settings._FLOAT_CREDIT_KEYS)


if __name__ == "__main__":
    unittest.main()
