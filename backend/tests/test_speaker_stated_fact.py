"""The number of speakers the user gives is a fact: voice detection is asked for exactly that many voices, the two Gemini checks are
told exactly how many people speak, the vote may never add another speaker, and nothing warns "we found N, you said M". Offline."""
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import longdub_service as ld
import speaker_vote_live as svl


def rows(n, speaker=lambda i: "Speaker 1" if i % 2 == 0 else "Speaker 2"):
    return [{"segment_id": f"seg_{i}", "start": i * 3.0, "end": i * 3.0 + 2.5, "speaker": speaker(i), "text": f"line {i}"} for i in range(n)]


class Prompts(unittest.TestCase):
    def test_exact_number_replaces_several(self):
        for kind in ("dialogue", "audio"):
            p = svl.prompt_for(kind, rows(4), people=2)
            self.assertIn("Exactly 2 different people speak.", p)
            self.assertNotIn("Several different people", p)
            self.assertIn("exactly 2 people speak, so use only 2 letters", p)
            self.assertIn('{"people": 2,', p)
            self.assertIn("LINES:", p)

    def test_no_number_leaves_the_wording_as_it_was(self):
        p = svl.prompt_for("dialogue", rows(4))
        self.assertIn("Several different people speak.", p)
        self.assertIn("Do not invent extra people unless clearly needed.", p)
        self.assertIn('{"people": 3,', p)
        self.assertEqual(svl.prompt_for("dialogue", rows(4), people=None), p)
        self.assertEqual(svl.prompt_for("dialogue", rows(4), people=0), p)
        self.assertEqual(svl.prompt_for("dialogue", rows(4), people="x"), p)

    def test_one_speaker_grammar(self):
        p = svl.prompt_for("dialogue", rows(4), people=1)
        self.assertIn("Exactly 1 different person speaks.", p)
        self.assertIn("exactly 1 person speaks, so use only 1 letter.", p)

    def test_other_languages_get_the_same_sentence(self):
        for lang in ("es", "nl", "de"):
            p = svl.prompt_for("audio", rows(4), source_language=lang, people=3)
            self.assertIn("Exactly 3 different people speak.", p, lang)
            self.assertIn('{"people": 3,', p, lang)
            self.assertNotIn("Several different people", p, lang)

    def test_the_request_carries_it(self):
        payload = svl._payload("dialogue", rows(3), None, people=2)
        self.assertIn("Exactly 2 different people speak.", payload["contents"][0]["parts"][-1]["text"])


class Collect(unittest.TestCase):
    def collect(self, d, people, asked):
        def ask(key, payload, timeout):
            asked.append(payload["contents"][0]["parts"][-1]["text"])
            ids = [f"seg_{i}" for i in range(4)]
            body = json.dumps({"people": 2, "lines": [{"id": s, "speaker": "AB"[i % 2]} for i, s in enumerate(ids)]})
            return {"candidates": [{"content": {"parts": [{"text": body}]}}], "usageMetadata": {}}, None
        return svl.collect("j", rows(4), None, d, "key", ask=ask, people=people)

    def test_saved_answers_are_reused_only_for_the_same_number(self):
        with tempfile.TemporaryDirectory() as d:
            asked = []
            first = self.collect(d, 2, asked)
            self.assertFalse(first["reused"])
            self.assertTrue(asked and all("Exactly 2 different people speak." in a for a in asked))
            n = len(asked)
            self.assertTrue(self.collect(d, 2, asked)["reused"])
            self.assertEqual(len(asked), n)
            again = self.collect(d, 3, asked)                      # another number: asked again, never the old answers
            self.assertFalse(again["reused"])
            self.assertIn("Exactly 3 different people speak.", asked[-1])


def three_group_votes(n=20):
    """App says 2 speakers; dialogue and audio both see a third person in the last 5 lines."""
    label = lambda i: "A" if i < 10 else ("B" if i < 15 else "C")
    lines = {f"seg_{i}": label(i) for i in range(n)}
    return {"dialogue": dict(lines), "audio": dict(lines)}


class Apply(unittest.TestCase):
    def app_rows(self, n=20):
        return rows(n, lambda i: "Speaker 1" if i < 10 else "Speaker 2")

    def test_without_the_fact_a_new_speaker_can_be_added(self):
        r = self.app_rows()
        s = svl.apply(r, three_group_votes())
        self.assertEqual(s["status"], "ok")
        self.assertEqual(len(s["new_speakers"]), 1)
        self.assertEqual(len({x["speaker"] for x in r}), 3)

    def test_with_the_fact_no_speaker_is_ever_added(self):
        r = self.app_rows()
        s = svl.apply(r, three_group_votes(), allow_new=False)
        self.assertEqual(s["status"], "ok")
        self.assertEqual(s["new_speakers"], [])
        self.assertEqual({x["speaker"] for x in r}, {"Speaker 1", "Speaker 2"})


class FillTheGap(unittest.TestCase):
    """The user said 3 people, the detector found 2, both checks name a third person on 5+ lines: the vote may add exactly that one."""
    def app_rows(self, n=20):
        return rows(n, lambda i: "Speaker 1" if i < 10 else "Speaker 2")

    def test_the_vote_adds_a_speaker_up_to_the_gap_and_no_further(self):
        r = self.app_rows()
        s = svl.apply(r, three_group_votes(), allow_new=True, max_new=1)
        self.assertEqual(len(s["new_speakers"]), 1)
        self.assertEqual(len({x["speaker"] for x in r}), 3)

    def test_a_gap_of_zero_adds_nobody(self):
        r = self.app_rows()
        s = svl.apply(r, three_group_votes(), allow_new=True, max_new=0)
        self.assertEqual(s["new_speakers"], [])
        self.assertEqual({x["speaker"] for x in r}, {"Speaker 1", "Speaker 2"})

    def test_two_extra_letters_with_a_gap_of_one_add_only_one(self):
        label = lambda i: "A" if i < 8 else ("B" if i < 14 else ("C" if i < 20 else "D"))
        lines = {f"seg_{i}": label(i) for i in range(26)}
        r = rows(26, lambda i: "Speaker 1" if i < 8 else "Speaker 2")
        s = svl.apply(r, {"dialogue": dict(lines), "audio": dict(lines)}, allow_new=True, max_new=1)
        self.assertLessEqual(len(s["new_speakers"]), 1)
        self.assertLessEqual(len({x["speaker"] for x in r}), 3)

    def test_the_service_passes_the_gap_when_the_stated_number_is_higher(self):
        seen = {}

        def fake_apply(r, votes, allow_new=True, **kw):
            seen.update(allow_new=allow_new, **kw)
            return {"status": "skipped"}

        job = {"id": "j", "stated_speakers": 4, "analysis": {"audio_duration": 100}}
        with mock.patch.object(svl, "collect", lambda *a, **k: {"dialogue": None, "audio": None, "usage": {}}), mock.patch.object(svl, "apply", fake_apply), \
                mock.patch.object(ld, "_mark", lambda *a, **k: None), mock.patch.object(ld, "_ev", lambda *a, **k: None), \
                mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_speaker_check_refund", lambda *a, **k: None):
            ld._vote_speakers(job, rows(4), Path("."), Path("v.wav"))
        self.assertTrue(seen["allow_new"])
        self.assertEqual(seen["max_new"], 2)      # 4 stated, 2 found
        self.assertEqual(seen["min_new_lines"], 2)

    def test_a_person_with_only_a_few_lines_is_found_when_the_user_said_they_exist(self):
        label = lambda i: "A" if i < 10 else ("B" if i < 17 else "C")      # C says 3 lines, both checks agree
        lines = {f"seg_{i}": label(i) for i in range(20)}
        r = self.app_rows()
        none = svl.apply(r, {"dialogue": dict(lines), "audio": dict(lines)}, allow_new=True, max_new=1)
        self.assertEqual(none["new_speakers"], [])      # the vote's own minimum of 5 lines
        r = self.app_rows()
        some = svl.apply(r, {"dialogue": dict(lines), "audio": dict(lines)}, allow_new=True, max_new=1, min_new_lines=2)
        self.assertEqual(len(some["new_speakers"]), 1)


class Insisted(unittest.TestCase):
    """The user was told what the checks found and kept their number: it wins, one check's word is enough for a new speaker."""
    def lines(self):
        label = lambda i: "A" if i < 10 else ("B" if i < 17 else "C")
        return {f"seg_{i}": label(i) for i in range(20)}

    def test_a_person_only_one_check_names_is_added_when_the_user_insisted(self):
        r = rows(20, lambda i: "Speaker 1" if i < 10 else "Speaker 2")
        votes = {"dialogue": {k: ("B" if v == "C" else v) for k, v in self.lines().items()}, "audio": self.lines()}
        s = svl.apply(r, votes, allow_new=True, max_new=1, min_new_lines=1)
        self.assertEqual(s["new_speakers"], [])      # not enough agreement for the normal answer
        r = rows(20, lambda i: "Speaker 1" if i < 10 else "Speaker 2")
        s = svl.apply(r, votes, allow_new=True, max_new=1, min_new_lines=1, new_support=1)
        self.assertEqual(len(s["new_speakers"]), 1)

    def test_the_service_uses_it_only_after_the_user_chose_keep(self):
        seen = []

        def fake_apply(r, votes, allow_new=True, **kw):
            seen.append(kw)
            return {"status": "skipped"}

        for choice in (None, "keep"):
            job = {"id": "j", "stated_speakers": 4, "analysis": {"audio_duration": 100}, **({"speaker_merge_choice": choice} if choice else {})}
            with mock.patch.object(svl, "collect", lambda *a, **k: {"dialogue": None, "audio": None, "usage": {}}), mock.patch.object(svl, "apply", fake_apply), \
                    mock.patch.object(ld, "_mark", lambda *a, **k: None), mock.patch.object(ld, "_ev", lambda *a, **k: None), \
                    mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_speaker_check_refund", lambda *a, **k: None):
                ld._vote_speakers(job, rows(4), Path("."), Path("v.wav"))
        self.assertEqual((seen[0]["min_new_lines"], "new_support" in seen[0]), (2, False))
        self.assertEqual((seen[1]["min_new_lines"], seen[1]["new_support"]), (1, 1))


class StatedFirst(unittest.TestCase):
    """The user said 5 people, the detector lumped them into 2 voices, both checks tell 5 people apart: the 5 people are the speakers."""
    def setUp(self):
        self.sizes = [10, 5, 6, 4, 5]
        letters = "ABCDE"
        self.lines = {}
        i = 0
        for L, n in zip(letters, self.sizes):
            for _ in range(n):
                self.lines[f"seg_{i}"] = L
                i += 1
        self.n = i

    def rows(self):
        return rows(self.n, lambda i: "Speaker 1" if i < 15 else "Speaker 2")

    def test_every_person_the_checks_tell_apart_becomes_a_speaker(self):
        r = self.rows()
        s = svl.apply(r, {"dialogue": dict(self.lines), "audio": dict(self.lines)}, allow_new=True, max_new=4, min_new_lines=1, new_support=1, fresh=True)
        self.assertEqual(s["status"], "ok")
        self.assertTrue(s["fresh"])
        got = [x["speaker"] for x in r]
        self.assertEqual(len(set(got)), 5)
        # the lines of one letter stay together, the detector's lumps are gone
        by_letter = {}
        for sid, L in self.lines.items():
            by_letter.setdefault(L, set()).add(next(x["speaker"] for x in r if x["segment_id"] == sid))
        self.assertTrue(all(len(v) == 1 for v in by_letter.values()))
        self.assertEqual(len({next(iter(v)) for v in by_letter.values()}), 5)

    def test_without_it_the_lumps_stay(self):
        r = self.rows()
        svl.apply(r, {"dialogue": dict(self.lines), "audio": dict(self.lines)}, allow_new=False)
        self.assertEqual({x["speaker"] for x in r}, {"Speaker 1", "Speaker 2"})

    def test_the_service_checks_that_both_checks_name_everyone(self):
        f = ld._checks_name_everyone
        five = dict(self.lines)
        four = {k: ("D" if v == "E" else v) for k, v in five.items()}
        self.assertTrue(f({"dialogue": five, "audio": five}, 5))
        self.assertTrue(f({"dialogue": five, "audio": None}, 5))
        self.assertFalse(f({"dialogue": five, "audio": four}, 5))
        self.assertFalse(f({"dialogue": four, "audio": five}, 5))
        self.assertFalse(f({"dialogue": None, "audio": five}, 5))
        self.assertFalse(f(None, 5))

    def test_the_service_uses_it_and_the_switch_turns_it_off(self):
        seen = []

        def fake_apply(r, votes, allow_new=True, **kw):
            seen.append(kw)
            return {"status": "skipped"}

        for switch in (True, False):
            job = {"id": "j", "stated_speakers": 5, "analysis": {"audio_duration": 100}}
            with mock.patch.object(svl, "collect", lambda *a, **k: {"dialogue": dict(self.lines), "audio": dict(self.lines), "usage": {}}), \
                    mock.patch.object(svl, "apply", fake_apply), mock.patch.object(ld, "STATED_FIRST", switch), \
                    mock.patch.object(ld, "_mark", lambda *a, **k: None), mock.patch.object(ld, "_ev", lambda *a, **k: None), \
                    mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_speaker_check_refund", lambda *a, **k: None):
                ld._vote_speakers(job, self.rows(), Path("."), Path("v.wav"))
        self.assertTrue(seen[0].get("fresh"))
        self.assertEqual(seen[0]["max_new"], 4)
        self.assertFalse(any(k.get("fresh") for k in seen[1:]))


class Wiring(unittest.TestCase):
    def test_stated_people(self):
        f = ld._stated_people
        self.assertEqual(f({"stated_speakers": 2}), 2)
        self.assertEqual(f({"stated_speakers": "3"}), 3)
        self.assertEqual(f({"stated_speakers": 1}), 1)
        for bad in ({}, {"stated_speakers": 0}, {"stated_speakers": None}, {"stated_speakers": "x"}, {"stated_speakers": ld.MAX_SPEAKERS + 1}):
            self.assertIsNone(f(bad), bad)

    def test_the_vote_is_told_the_number_and_may_not_add_speakers(self):
        seen = {}

        def fake_collect(job_id, r, vocals, wd, key, **kw):
            seen["collect"] = kw
            return {"dialogue": None, "audio": None, "usage": {}}

        def fake_apply(r, votes, allow_new=True):
            seen["allow_new"] = allow_new
            return {"status": "skipped"}

        job = {"id": "j", "stated_speakers": 2, "analysis": {"audio_duration": 100}}
        with mock.patch.object(svl, "collect", fake_collect), mock.patch.object(svl, "apply", fake_apply), \
                mock.patch.object(ld, "_mark", lambda *a, **k: None), mock.patch.object(ld, "_ev", lambda *a, **k: None), \
                mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_speaker_check_refund", lambda *a, **k: None):
            ld._vote_speakers(job, rows(4), Path("."), Path("v.wav"))
        self.assertEqual(seen["collect"]["people"], 2)
        self.assertFalse(seen["allow_new"])

    def test_detection_asks_for_exactly_the_chosen_number(self):
        src = inspect.getsource(ld._run_detection)
        self.assertLess(src.index("get_speaker_turns(str(vocals_all), HF_TOKEN, exact_n)"), src.index("min_speakers=exact_n"))   # "at least" is only the fallback
        self.assertIn("_run_detection(slot, vocals_all, k, 0)", inspect.getsource(ld._find_voices))
        self.assertIn("{'exactly' if exact else 'at least'}", inspect.getsource(ld._run_analysis))

    def test_no_count_warnings_reach_the_user(self):
        src = inspect.getsource(ld._review_speakers)
        self.assertNotIn("ADDED_SPEAKER_MESSAGE", src)
        self.assertNotIn('setdefault("warnings"', src)
        self.assertNotIn("Please check who says each line", src)


if __name__ == "__main__":
    unittest.main()
