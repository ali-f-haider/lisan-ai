"""The user said N people speak. When the speaker vote ends with fewer, the two listening checks look once more (told how few they named),
and if it is still fewer the job asks the user before anything stays merged. Offline, invented data."""
import inspect
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import longdub_service as ld
import speaker_vote_live as svl

ROOT = Path(__file__).resolve().parents[1]
N = 20


def rows(per=5, people=4):
    """Lines 0..19; the detector said Speaker 1 for the first five, Speaker 2 for the next five, and so on."""
    return [{"segment_id": f"seg_{i}", "start": i * 3.0, "end": i * 3.0 + 2.5, "text": f"line {i}", "speaker": f"Speaker {i // per + 1}"}
            for i in range(per * people)]


def labels(groups):
    """{seg_i: letter}: groups = how many lines each letter gets, in order."""
    out, i = {}, 0
    for g, letter in zip(groups, "ABCDEFGH"):
        for _ in range(g):
            out[f"seg_{i}"] = letter
            i += 1
    return out


FIRST = {"dialogue": labels([10, 10]), "audio": labels([10, 10]), "usage": {"usd": 0.05, "calls": 2, "tokens_in": 100, "tokens_out": 10, "tokens_thinking": 5}}


def answer(mapping, people):
    body = {"people": people, "lines": [{"id": k, "speaker": v} for k, v in mapping.items()]}
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}],
            "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 5, "thoughtsTokenCount": 7}}


class FakeAsk:
    """Returns the given mapping for every request and keeps the prompts."""
    def __init__(self, mapping, people):
        self.mapping, self.people, self.prompts = mapping, people, []

    def __call__(self, key, payload, timeout):
        self.prompts.append(payload["contents"][0]["parts"][-1]["text"])
        return answer(self.mapping, self.people), None


class Text(unittest.TestCase):
    def test_the_paragraph_names_how_few_and_how_many(self):
        t = svl.reask_text("dialogue", 2, 4)
        self.assertIn("used only 2 different letters", t)
        self.assertIn("exactly 4 different people speak. Some lines were given to a person who does not say them.", t)
        self.assertIn("use all 4 letters", t)
        self.assertNotIn("Listen again", t)
        self.assertIn("Listen again to the voices", svl.reask_text("audio", 2, 4))

    def test_the_paragraph_names_no_vendor(self):
        low = (svl.reask_text("audio", 2, 4)).lower()
        for word in ("gemini", "google", "openai", "chatgpt", "claude", "anthropic", "model"):
            self.assertNotIn(word, low)

    def test_the_extra_text_goes_after_the_lines_and_nothing_else_changes(self):
        base = svl._payload("dialogue", rows(), None, people=4)["contents"][0]["parts"][-1]["text"]
        more = svl._payload("dialogue", rows(), None, people=4, extra="EXTRA")["contents"][0]["parts"][-1]["text"]
        self.assertEqual(more, base + "EXTRA")
        self.assertEqual(svl._payload("dialogue", rows(), None, people=4, extra=None)["contents"][0]["parts"][-1]["text"], base)

    def test_people_named(self):
        self.assertEqual(svl.people_named({"a": "A", "b": "B", "c": "A"}), 2)
        self.assertEqual(svl.people_named(None), 0)
        self.assertEqual(svl.people_named({}), 0)


class Reask(unittest.TestCase):
    def go(self, d, ask, votes=None, people=4, key="k", audio=False):
        with mock.patch.object(svl, "_audio_b64", lambda *a, **k: "AAAA" if audio else None):
            return svl.reask("j", rows(), votes or FIRST, "v.wav", d, key, people, ask=ask)

    def test_a_check_that_named_too_few_is_asked_again_and_a_better_answer_replaces_it(self):
        with tempfile.TemporaryDirectory() as d:
            ask = FakeAsk(labels([5, 5, 5, 5]), 4)
            out = self.go(d, ask)
            self.assertEqual(out["asked"], ["dialogue"])
            self.assertEqual(out["changed"], ["dialogue"])
            self.assertEqual(svl.people_named(out["dialogue"]), 4)
            self.assertEqual(out["audio"], FIRST["audio"])           # the audio check was not asked: no audio to send
            self.assertTrue(all("used only 2 different letters" in p and "exactly 4 different people speak" in p for p in ask.prompts))

    def test_both_checks_when_there_is_audio(self):
        with tempfile.TemporaryDirectory() as d:
            ask = FakeAsk(labels([5, 5, 5, 5]), 4)
            out = self.go(d, ask, audio=True)
            self.assertEqual(sorted(out["asked"]), ["audio", "dialogue"])
            self.assertEqual(sorted(out["changed"]), ["audio", "dialogue"])
            self.assertEqual(len(ask.prompts), 2)
            self.assertEqual(out["usage"]["calls"], 2)
            self.assertGreater(out["usage"]["tokens_in"], 0)

    def test_an_answer_that_names_no_more_people_never_replaces_the_old_one(self):
        with tempfile.TemporaryDirectory() as d:
            out = self.go(d, FakeAsk(labels([10, 10]), 2))
            self.assertEqual(out["asked"], ["dialogue"])
            self.assertEqual(out["changed"], [])
            self.assertEqual(out["dialogue"], FIRST["dialogue"])

    def test_nothing_is_asked_when_the_checks_already_named_enough(self):
        with tempfile.TemporaryDirectory() as d:
            ask = FakeAsk(labels([5, 5, 5, 5]), 4)
            v = {"dialogue": labels([5, 5, 5, 5]), "audio": labels([5, 5, 5, 5])}
            out = self.go(d, ask, votes=v)
            self.assertEqual(out["asked"], [])
            self.assertEqual(ask.prompts, [])

    def test_only_the_check_that_named_too_few_is_asked(self):
        with tempfile.TemporaryDirectory() as d:
            ask = FakeAsk(labels([5, 5, 5, 5]), 4)
            v = {"dialogue": labels([10, 10]), "audio": labels([5, 5, 5, 5])}
            out = self.go(d, ask, votes=v, audio=True)
            self.assertEqual(out["asked"], ["dialogue"])
            self.assertEqual(len(ask.prompts), 1)

    def test_no_number_one_person_no_key_or_switched_off_asks_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            for people, key in ((None, "k"), (1, "k"), (0, "k"), ("x", "k"), (4, "")):
                ask = FakeAsk(labels([5, 5, 5, 5]), 4)
                out = self.go(d, ask, people=people, key=key)
                self.assertEqual((out["asked"], ask.prompts), ([], []), (people, key))
            ask = FakeAsk(labels([5, 5, 5, 5]), 4)
            with mock.patch.object(svl, "REASK", False):
                out = self.go(d, ask)
            self.assertEqual((out["asked"], ask.prompts), ([], []))

    def test_saved_answers_are_reused_so_a_resume_never_pays_twice(self):
        with tempfile.TemporaryDirectory() as d:
            ask = FakeAsk(labels([5, 5, 5, 5]), 4)
            first = self.go(d, ask)
            n = len(ask.prompts)
            self.assertFalse(first["reused"])
            again = self.go(d, ask)
            self.assertTrue(again["reused"])
            self.assertEqual(len(ask.prompts), n)
            self.assertEqual(again["dialogue"], first["dialogue"])
            self.assertEqual(again["changed"], ["dialogue"])

    def test_another_number_asks_again(self):
        with tempfile.TemporaryDirectory() as d:
            ask = FakeAsk(labels([5, 5, 5, 5]), 4)
            self.go(d, ask)
            n = len(ask.prompts)
            self.go(d, FakeAsk(labels([5, 5, 5, 5]), 4), people=3)
            ask3 = FakeAsk(labels([7, 7, 6]), 3)
            out = self.go(d, ask3, people=3)
            self.assertEqual(len(ask3.prompts), 0 if out["reused"] else 1)

    def test_a_failing_or_unusable_answer_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            def boom(key, payload, timeout):
                raise RuntimeError("down")
            out = self.go(d, boom)
            self.assertEqual(out["changed"], [])
            self.assertEqual(out["dialogue"], FIRST["dialogue"])
        with tempfile.TemporaryDirectory() as d:
            junk = lambda key, payload, timeout: ({"candidates": [{"content": {"parts": [{"text": "no json"}]}}]}, None)
            out = self.go(d, junk)
            self.assertEqual(out["changed"], [])
        with tempfile.TemporaryDirectory() as d:
            out = svl.reask("j", None, None, None, d, "k", 4, ask=None)
            self.assertEqual(out["changed"], [])


def job(**kw):
    j = {"id": "j", "stated_speakers": 4, "analysis": {"audio_duration": 100}, "source_language": "en"}
    j.update(kw)
    return j


class VoteStep(unittest.TestCase):
    """The second look and the question. These tests start from a detector that found the user's number; since that case now keeps the
    detector's speakers (KeepDetected in test_speaker_stated_fact), they run with that switch off."""
    def setUp(self):
        p = mock.patch.object(ld, "KEEP_DETECTED", False)
        p.start()
        self.addCleanup(p.stop)

    def run_step(self, j, second, first=FIRST, extra_patch=None):
        r = rows()
        events = []
        asked = []

        def fake_ask(key, payload, timeout):
            asked.append(1)
            return answer(second, max(len(set(second.values())), 1)), None

        with mock.patch.object(svl, "collect", lambda *a, **k: dict(first, reused=False)), \
                mock.patch.object(svl, "_default_ask", fake_ask), mock.patch.object(svl, "_audio_b64", lambda *a, **k: "AAAA"), \
                mock.patch.object(ld, "GEMINI_API_KEY", "k"), mock.patch.object(ld, "_mark", lambda *a, **k: None), \
                mock.patch.object(ld, "_ev", lambda job, step, status, detail, *a, **k: events.append((step, status, detail))), \
                mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_speaker_check_refund", lambda *a, **k: None), \
                tempfile.TemporaryDirectory() as d:
            summary = ld._vote_speakers(j, r, Path(d), Path("v.wav"))
        return summary, r, events, asked

    def test_the_second_look_brings_the_missing_people_back(self):
        j = job()
        summary, r, events, asked = self.run_step(j, labels([5, 5, 5, 5]))
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(len({x["speaker"] for x in r}), 4)
        self.assertNotIn("fewer", summary)
        ev = [e for e in events if e[0] == "speaker_vote"][0][2]
        self.assertIn("second look by dialogue, audio", ev)
        self.assertIn("people with lines 2 -> 4 of 4", ev)
        self.assertGreater(len(asked), 0)

    def test_still_fewer_after_the_second_look_is_reported_not_hidden(self):
        j = job()
        summary, r, events, asked = self.run_step(j, labels([7, 7, 6]))
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(len({x["speaker"] for x in r}), 3)
        self.assertEqual(summary["fewer"]["stated"], 4)
        self.assertEqual(summary["fewer"]["found"], 3)
        self.assertEqual(sum(summary["fewer"]["lines"]), 20)
        self.assertEqual(summary["fewer"]["lines"], sorted(summary["fewer"]["lines"], reverse=True))

    def test_a_second_look_that_does_not_help_leaves_the_first_result(self):
        j = job()
        summary, r, events, asked = self.run_step(j, labels([10, 10]))
        self.assertEqual(len({x["speaker"] for x in r}), 2)
        self.assertEqual(summary["fewer"], {"stated": 4, "found": 2, "lines": [10, 10]})
        first = {x["segment_id"]: x["speaker"] for x in r}
        again, r2, _e, _a = self.run_step(job(), labels([10, 10]))
        self.assertEqual({x["segment_id"]: x["speaker"] for x in r2}, first)

    def test_no_stated_number_means_no_second_look_and_no_question(self):
        j = job()
        j.pop("stated_speakers")
        summary, r, events, asked = self.run_step(j, labels([5, 5, 5, 5]))
        self.assertEqual(asked, [])
        self.assertNotIn("fewer", summary)

    def test_the_vote_gets_the_number_it_was_made_for_even_after_the_user_changes_it(self):
        seen = []
        j = job()
        with mock.patch.object(svl, "collect", lambda *a, **k: seen.append(k.get("people")) or dict(FIRST)), \
                mock.patch.object(svl, "reask", lambda *a, **k: {"changed": [], "asked": [], "usage": {}}), \
                mock.patch.object(ld, "_mark", lambda *a, **k: None), mock.patch.object(ld, "_ev", lambda *a, **k: None), \
                mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_speaker_check_refund", lambda *a, **k: None):
            ld._vote_speakers(j, rows(), Path("."), Path("v.wav"))
            self.assertEqual(j["speaker_vote_people"], 4)
            j["stated_speakers"] = 2              # the user chose "continue with 2"
            ld._vote_speakers(j, rows(), Path("."), Path("v.wav"))
        self.assertEqual(seen, [4, 4])

    def test_a_second_look_with_the_same_number_of_people_does_not_replace_the_first_result(self):
        """Only a better answer (more people with lines) is taken; an equal one keeps the first, because nothing says it is better."""
        def apply(r, v, allow_new=True):
            for x in r:
                x["speaker"] = "Speaker " + str(ord(v["dialogue"][x["segment_id"]]) - 64)
            return {"status": "ok", "changed": 0, "badged": 0, "new_speakers": [], "merged": 0, "voters": [], "by_line": {}}

        first = {"dialogue": labels([10, 10]), "audio": labels([10, 10]), "usage": {}}
        more = {"changed": ["dialogue"], "asked": ["dialogue"], "dialogue": labels([6, 14]), "audio": first["audio"], "usage": {}, "reused": False}
        r = rows()
        with mock.patch.object(svl, "collect", lambda *a, **k: dict(first)), mock.patch.object(svl, "reask", lambda *a, **k: more), \
                mock.patch.object(svl, "apply", apply), mock.patch.object(ld, "GEMINI_API_KEY", "k"), \
                mock.patch.object(ld, "_mark", lambda *a, **k: None), mock.patch.object(ld, "_ev", lambda *a, **k: None), \
                mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_speaker_check_refund", lambda *a, **k: None):
            summary = ld._vote_speakers(job(), r, Path("."), Path("v.wav"))
        self.assertEqual([x["speaker"] for x in r].count("Speaker 1"), 10)
        self.assertEqual(summary["fewer"], {"stated": 4, "found": 2, "lines": [10, 10]})

    def test_the_cost_of_the_second_look_is_counted(self):
        j = job()
        self.run_step(j, labels([5, 5, 5, 5]))
        self.assertGreater(float(j["analysis"]["speaker_vote"]["usd"]), 0.05 - 1e-9)


class Pause(unittest.TestCase):
    def test_the_question_comes_right_after_the_vote_and_before_anything_is_kept(self):
        src = inspect.getsource(ld._run_analysis)
        v = src.index("vote = _vote_speakers(job, rows, wd, vocals_all)")
        q = src.index('vote.get("fewer")', v)
        r = src.index("return", q)
        p = src.index("pitch_check.run(rows, vocals_all)", v)
        i = src.index("_init_speakers(job, rows)", v)
        w = src.index("_write_segments(job, rows)", v)
        self.assertLess(v, q)
        self.assertLess(r, p)
        self.assertLess(r, i)
        self.assertLess(r, w)
        block = src[q - 12:r + 10]
        self.assertIn('not job.get("speaker_merge_choice")', block)
        self.assertIn("SPEAKER_COUNT_ASK", block)
        self.assertIn('job["status"] = "speaker_count"', block)
        self.assertIn('job["speaker_merge_ask"]', block)
        self.assertIn('"speaker_merge_asked"', block)

    def test_the_question_is_a_public_field(self):
        self.assertIn("speaker_merge_ask", ld.PUBLIC_FIELDS)
        self.assertIn("speaker_count_ask", ld.PUBLIC_FIELDS)

    def test_the_route_calls_the_answer_function(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        i = src.index('@app.post("/api/longdub/{job_id}/speaker-merge")')
        block = src[i:i + 700]
        self.assertIn("longdub_service.choose_speaker_merge(job, body.get(\"choice\"))", block)
        self.assertIn("_ld_job(request, job_id)", block)


class Answer(unittest.TestCase):
    def paused(self, **kw):
        j = {"id": "j", "status": "speaker_count", "stated_speakers": 7, "speaker_merge_ask": {"stated": 7, "found": 3, "lines": [60, 30, 5]}}
        j.update(kw)
        return j

    def choose(self, j, choice):
        calls = []
        with mock.patch.object(ld, "_save", lambda j: calls.append("save")), mock.patch.object(ld, "_ev", lambda *a, **k: calls.append(a[1])), \
                mock.patch.object(ld, "start_worker", lambda i: calls.append(("start", i))):
            res = ld.choose_speaker_merge(j, choice)
        return res, calls

    def test_use_continues_with_the_smaller_number(self):
        j = self.paused()
        res, calls = self.choose(j, "use")
        self.assertEqual(res, (True, None))
        self.assertEqual(j["stated_speakers"], 3)
        self.assertEqual(j["stated_speakers_asked"], 7)
        self.assertEqual(j["speaker_merge_choice"], "use")
        self.assertNotIn("speaker_merge_ask", j)
        self.assertEqual(j["status"], "accepted")
        self.assertIn(("start", "j"), calls)
        self.assertIn("speaker_merge_chosen", calls)

    def test_keep_leaves_the_number_and_the_slots(self):
        j = self.paused()
        res, calls = self.choose(j, "keep")
        self.assertEqual(res, (True, None))
        self.assertEqual(j["stated_speakers"], 7)
        self.assertNotIn("stated_speakers_asked", j)
        self.assertEqual(j["speaker_merge_choice"], "keep")
        self.assertEqual(j["status"], "accepted")

    def test_only_while_the_job_waits_and_only_the_two_answers(self):
        for kw in ({"status": "editing"}, {"status": "accepted"}, {"speaker_merge_ask": None}):
            j = self.paused(**kw)
            res, calls = self.choose(j, "use")
            self.assertEqual(res[0], False, kw)
            self.assertEqual(res[1][1], 409)
            self.assertNotIn(("start", "j"), calls)
        for bad in (None, "", "yes", 3, True, "KEEP"):
            j = self.paused()
            res, calls = self.choose(j, bad)
            self.assertEqual((res[0], res[1][1]), (False, 400), bad)
            self.assertEqual(j["status"], "speaker_count")
            self.assertEqual(j["speaker_merge_ask"]["found"], 3)

    def test_the_detection_question_is_not_answered_by_this_route_and_back(self):
        j = {"id": "j", "status": "speaker_count", "stated_speakers": 2, "speaker_count_ask": {"stated": 2, "found": 4}}
        res, _calls = self.choose(j, "use")
        self.assertEqual(res[0], False)
        k = self.paused()
        with mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_ev", lambda *a, **k2: None), mock.patch.object(ld, "start_worker", lambda i: None):
            ok, problem = ld.choose_speaker_count(k, 7)
        self.assertFalse(ok)

    def test_empty_slots_follow_the_choice(self):
        r = rows(per=10, people=3)
        keep, use = self.paused(), self.paused()
        self.choose(keep, "keep")
        self.choose(use, "use")
        ld._init_speakers(keep, [dict(x) for x in r])
        ld._init_speakers(use, [dict(x) for x in r])
        self.assertEqual(len(keep["speaker_list"]), 7)
        self.assertEqual(len(use["speaker_list"]), 3)


class Page(unittest.TestCase):
    html = (ROOT / "dub_long.html").read_text(encoding="utf-8")

    def test_both_languages_have_the_words(self):
        for key in ("spkMergeText", "spkMergeNote", "spkMergeKeep", "spkMergeUse"):
            self.assertEqual(self.html.count(f"    {key}:"), 2, key)

    def test_the_arabic_words_are_arabic(self):
        for line in self.html.splitlines():
            if line.strip().startswith(("spkMergeText:", "spkMergeNote:", "spkMergeKeep:", "spkMergeUse:")) and "ذكرتَ" in line + "":
                self.assertRegex(line, r"[؀-ۿ]")
        ar = [l for l in self.html.splitlines() if l.strip().startswith("spkMerge") and re.search(r"[؀-ۿ]", l)]
        self.assertEqual(len(ar), 4)

    def test_the_buttons_send_the_two_answers_to_the_merge_route(self):
        self.assertIn('j.speaker_merge_ask || j.speaker_count_ask', self.html)
        self.assertIn('spkMerge ? "keep" : spkAsk.stated', self.html)
        self.assertIn('spkMerge ? "use" : spkAsk.found', self.html)
        self.assertIn('(spkMerge ? "/speaker-merge" : "/speaker-count")', self.html)
        self.assertIn("{choice: pair[1]}", self.html)
        self.assertIn("{count: pair[1]}", self.html)

    def test_the_detection_question_page_is_unchanged(self):
        self.assertIn("T.spkCountText(spk.stated, spk.found)", self.html)
        self.assertIn("T.spkCountKeep(spk.stated)", self.html)
        self.assertIn("T.spkCountUse(spk.found)", self.html)

    def test_customer_words_name_no_vendor(self):
        for line in self.html.splitlines():
            if line.strip().startswith("spkMerge"):
                for word in ("Gemini", "Google", "OpenAI", "Claude", "Anthropic", "pyannote"):
                    self.assertNotIn(word, line)


if __name__ == "__main__":
    unittest.main()
