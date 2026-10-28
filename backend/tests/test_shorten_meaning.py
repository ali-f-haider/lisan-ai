"""Shortening a line that is too long for its time must not change what it means (Air Force One clip, 28 Oct 2026: the old
shortening broke 12 of 30 lines, e.g. «يُرِيدُونَ إِطْلَاقَ رَادِك» = 'they want to fire Radek'). A separate meaning check turns down a
shorter wording that lost something; the next ask is told what was lost; a line that cannot be shortened safely keeps its
wording. Shortening costs 1 credit per line from a reserve in the dubbing price; the rest is given back."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gemini_service as g
import longdub_service as ls

FULL = "يُرِيدُونَ إِطْلَاقَ سَرَاحِ الجِنِرَالِ رَادِك وَالْيَوْمَ قَبْلَ الْغَدِ"
ROWS = [
    {"segment_id": "seg_1", "start": 5.0, "end": 6.0, "text": "Who are they?", "arabic_text": "مَنْ هُمْ؟", "speaker": "President"},
    {"segment_id": "seg_2", "start": 6.2, "end": 8.0, "text": "They want General Radek released, today.", "arabic_text": FULL, "speaker": "Vice President"},
    {"segment_id": "seg_3", "start": 8.2, "end": 9.0, "text": "Radek?", "arabic_text": "رَادِك؟", "speaker": "President"},
]


class MeaningCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "lines").mkdir()
        (self.d / "fit").mkdir()
        self.saved = (g.shorten_arabic_line, g.check_shortened_arabic, ls._tts_with_retry, ls._fit_line, ls._ev)
        self.asks, self.checks, self.spoken, self.events = [], [], [], []
        self.answers = []          # what the shortener answers, in order
        self.verdicts = []         # what the meaning check answers, in order
        g.shorten_arabic_line = lambda jid, en, ar, max_letters, key, **kw: (self.asks.append(kw) or self.answers.pop(0))
        g.check_shortened_arabic = lambda jid, en, before, after, key, **kw: (self.checks.append((before, after, kw)) or self.verdicts.pop(0))
        ls._tts_with_retry = lambda voice, text, *a, **k: (self.spoken.append(text) or (b"x", ""))
        ls._ev = lambda job, step, status, detail="", *a: self.events.append((step, status, detail))

        def fit(raw, out, slot, room, loud_ref, start):
            out.write_bytes(b"w")
            return {"dur": 1.7, "tempo": 1.0, "warn": False, "gain_db": 0.0, "raw": 1.7, "slot": slot, "room": room}
        ls._fit_line = fit

    def tearDown(self):
        g.shorten_arabic_line, g.check_shortened_arabic, ls._tts_with_retry, ls._fit_line, ls._ev = self.saved
        self.tmp.cleanup()

    def run_it(self, job=None):
        job = job if job is not None else {"id": "j"}
        meta = {"dur": 2.6, "tempo": 1.3, "warn": False, "gain_db": 0.0, "raw": 3.4, "slot": 1.8, "room": 2.0}
        return job, ls._rephrase_to_fit(job, dict(ROWS[1]), "", meta, 1.8, 2.0, "v", self.d / "x.wav", self.d, cap=1.1, rows_all=ROWS)

    def test_a_wording_that_lost_meaning_is_not_spoken_and_the_next_ask_is_told_why(self):
        self.answers = ["يُرِيدُونَ إِطْلَاقَ رَادِك", "يُرِيدُونَ إِطْلَاقَ سَرَاحِ رَادِك الْيَوْمَ"]
        self.verdicts = [{"same": False, "lost": "freeing became firing"}, {"same": True, "lost": ""}]
        job, (meta, text) = self.run_it()
        self.assertEqual(text, "يُرِيدُونَ إِطْلَاقَ سَرَاحِ رَادِك الْيَوْمَ")
        self.assertEqual(self.spoken, [text])                     # the rejected wording was never spoken
        self.assertEqual(self.asks[1]["rejected"], [{"text": "يُرِيدُونَ إِطْلَاقَ رَادِك", "lost": "freeing became firing"}])
        self.assertEqual(self.checks[0][0], FULL)                 # always compared with the reviewed line
        self.assertTrue(any("does not say the same" in e[2] for e in self.events))
        self.assertEqual(job["dub"]["shorten_asked"], ["seg_2"])  # one line, one shortening, however many asks

    def test_when_no_wording_keeps_the_meaning_the_line_keeps_its_own(self):
        self.answers = ["إِطْلَاقُ رَادِك", "رَادِك"]
        self.verdicts = [{"same": False, "lost": "a"}, {"same": False, "lost": "b"}]
        _, (meta, text) = self.run_it()
        self.assertIsNone(text)
        self.assertEqual(self.spoken, [])
        self.assertEqual(meta["dur"], 2.6)
        self.assertEqual(len(self.asks), 1 + ls.SHORTEN_MEANING_RETRIES)

    def test_no_answer_from_the_meaning_check_counts_as_not_checked(self):
        self.answers = ["يُرِيدُونَ رَادِك"]
        self.verdicts = [None]
        _, (meta, text) = self.run_it()
        self.assertIsNone(text)
        self.assertEqual(self.spoken, [])
        self.assertEqual(len(self.asks), 1)

    def test_the_lines_around_it_are_given_to_both_requests(self):
        self.answers = ["يُرِيدُونَ إِطْلَاقَ سَرَاحِ رَادِك الْيَوْمَ"]
        self.verdicts = [{"same": True, "lost": ""}]
        self.run_it()
        ctx = self.asks[0]["context"]
        self.assertEqual([x["english"] for x in ctx["before"]], ["Who are they?"])
        self.assertEqual([x["english"] for x in ctx["after"]], ["Radek?"])
        self.assertEqual(self.checks[0][2]["context"], ctx)


class Prompts(unittest.TestCase):
    def test_the_shortening_prompt_puts_the_meaning_first_and_shows_the_scene(self):
        seen = []
        saved = (g.call_gemini, g.record_gemini)
        g.call_gemini = lambda key, payload, timeout=90, models=None: (seen.append((payload, models)) or
                                                                     ({"candidates": [{"content": {"parts": [{"text": '{"arabic_text": "x"}'}]}}]}, None))
        g.record_gemini = lambda *a: None
        try:
            out = g.shorten_arabic_line("j", "They want General Radek released.", FULL, 12, "k",
                                        context=ls._shorten_context(ROWS, "seg_2"), rejected=[{"text": "يُرِيدُونَ إِطْلَاقَ رَادِك", "lost": "freeing"}])
            v = g.check_shortened_arabic("j", "en", FULL, "يُرِيدُونَ إِطْلَاقَ رَادِك", "k")
        finally:
            g.call_gemini, g.record_gemini = saved
        self.assertEqual(out, "x")
        self.assertIsNone(v)                         # an answer without a "same" field is no answer
        shorten, check = (p["contents"][0]["parts"][0]["text"] for p, _ in seen)
        self.assertIn("THE MEANING COMES FIRST", shorten)
        self.assertIn("إِطْلَاقُ رَادِك", check)     # the meaning check knows the broken expression
        self.assertEqual([m for _, m in seen], [g._shorten_models()] * 2)

    def test_the_shortening_prompt_carries_the_context_and_the_rejected_try(self):
        seen = {}
        saved = (g.call_gemini, g.record_gemini)
        g.call_gemini = lambda key, payload, timeout=90, models=None: (seen.setdefault("t", payload["contents"][0]["parts"][0]["text"]), (None, "x"))[1]
        g.record_gemini = lambda *a: None
        try:
            g.shorten_arabic_line("j", "en", FULL, 12, "k", context=ls._shorten_context(ROWS, "seg_2"),
                                  rejected=[{"text": "يُرِيدُونَ إِطْلَاقَ رَادِك", "lost": "freeing"}])
        finally:
            g.call_gemini, g.record_gemini = saved
        self.assertIn("إِطْلَاقُ سَرَاحِ", seen["t"])
        self.assertIn("Who are they?", seen["t"])
        self.assertIn("REJECTED", seen["t"])


class Price(unittest.TestCase):
    def test_the_reserve_is_per_minute_and_off_when_shortening_is_off(self):
        self.assertEqual(ls._shorten_reserve({"shorten_per_min": 4}, 441.2), 30)
        self.assertEqual(ls._shorten_reserve({"shorten_per_min": 0}, 441.2), 0)
        saved = ls.REPHRASE_ENABLED
        ls.REPHRASE_ENABLED = False
        try:
            self.assertEqual(ls._shorten_reserve({"shorten_per_min": 4}, 441.2), 0)
        finally:
            ls.REPHRASE_ENABLED = saved

    def test_the_estimate_counts_it(self):
        saved = ls._lip_rate
        ls._lip_rate = lambda cfg, res: 0.0          # other tests replace the lip-sync price table
        try:
            e = ls.compute_estimate(441.2, {"shorten_per_min": 4})
            e0 = ls.compute_estimate(441.2, {"shorten_per_min": 0})
        finally:
            ls._lip_rate = saved
        self.assertEqual(e["shorten"], 30)
        self.assertEqual(e["total"] - e0["total"], 30)

    def test_the_strongest_model_shortens_first(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("real_config_for_shorten", Path(__file__).resolve().parents[1] / "config.py")
        cfg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cfg)
        self.assertEqual(cfg.SHORTEN_MODELS[0], "gemini-4-argon")

    def test_the_unused_part_is_given_back_once(self):
        backs, evs = [], []
        saved = (ls._refund, ls._remaining, ls._ev)
        ls._refund = lambda job, uid, amount, slot, key: (backs.append((amount, slot, key)) or True)
        ls._remaining = lambda job, slot: 999
        ls._ev = lambda *a: evs.append(a)
        try:
            job = {"id": "j", "dub_plan": {"shorten": 30}}
            dub = {"shorten_asked": [f"seg_{i}" for i in range(12)]}
            ls._shorten_refund(job, "u", dub)
            ls._shorten_refund(job, "u", dub)
            self.assertEqual(backs, [(18, "dub", "shorten_unused")])
            job2, dub2 = {"id": "k", "dub_plan": {"shorten": 30}}, {"shorten_asked": [f"s{i}" for i in range(40)]}
            ls._shorten_refund(job2, "u", dub2)
            self.assertEqual(len(backs), 1)
            self.assertTrue(dub2["shorten_settled"])
        finally:
            ls._refund, ls._remaining, ls._ev = saved


if __name__ == "__main__":
    unittest.main()
