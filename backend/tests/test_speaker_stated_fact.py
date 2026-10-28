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
            job = {"id": "j", "stated_speakers": 5, "speaker_merge_choice": "keep", "analysis": {"audio_duration": 100}}
            with mock.patch.object(svl, "collect", lambda *a, **k: {"dialogue": dict(self.lines), "audio": dict(self.lines), "usage": {}}), \
                    mock.patch.object(svl, "apply", fake_apply), mock.patch.object(ld, "STATED_FIRST", switch), \
                    mock.patch.object(ld, "_mark", lambda *a, **k: None), mock.patch.object(ld, "_ev", lambda *a, **k: None), \
                    mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_speaker_check_refund", lambda *a, **k: None):
                ld._vote_speakers(job, self.rows(), Path("."), Path("v.wav"))
        self.assertTrue(seen[0].get("fresh"))
        self.assertEqual(seen[0]["max_new"], 4)
        self.assertFalse(any(k.get("fresh") for k in seen[1:]))

    def test_before_the_user_has_answered_the_detector_is_not_overruled(self):
        seen = []

        def fake_apply(r, votes, allow_new=True, **kw):
            seen.append(kw)
            return {"status": "skipped"}

        job = {"id": "j", "stated_speakers": 5, "analysis": {"audio_duration": 100}}      # no answer yet: the question comes first
        with mock.patch.object(svl, "collect", lambda *a, **k: {"dialogue": dict(self.lines), "audio": dict(self.lines), "usage": {}}), \
                mock.patch.object(svl, "apply", fake_apply), \
                mock.patch.object(ld, "_mark", lambda *a, **k: None), mock.patch.object(ld, "_ev", lambda *a, **k: None), \
                mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_speaker_check_refund", lambda *a, **k: None):
            ld._vote_speakers(job, self.rows(), Path("."), Path("v.wav"))
        self.assertFalse(any(k.get("fresh") for k in seen))


class KeepDetected(unittest.TestCase):
    """The detector found exactly the user's number: the vote corrects lines but never empties a speaker, never adds one, and nobody is
    asked. (Real runs: the detector found 12 of 12 and the vote merged 1-3 of them away; the rebuild that followed mixed people up.)"""
    def votes(self):
        # both checks lump Speaker 3's lines (12-15) in with A and move line 3 from Speaker 1 to Speaker 2
        label = lambda i: "A" if i < 3 or 4 <= i < 6 or 12 <= i < 16 else ("B" if i == 3 or 6 <= i < 12 else "A")
        lines = {f"seg_{i}": label(i) for i in range(16)}
        return {"dialogue": dict(lines), "audio": dict(lines)}

    def rows(self):
        return rows(16, lambda i: "Speaker 1" if i < 6 else ("Speaker 2" if i < 12 else "Speaker 3"))

    def test_a_speaker_the_vote_would_empty_keeps_its_lines_marked_for_checking(self):
        r = self.rows()
        plain = svl.apply(r, self.votes(), allow_new=False)
        self.assertEqual(plain["status"], "ok")
        self.assertNotIn("Speaker 3", {x["speaker"] for x in r})          # without keep: merged away
        r = self.rows()
        s = svl.apply(r, self.votes(), allow_new=False, keep=True)
        self.assertEqual({x["speaker"] for x in r}, {"Speaker 1", "Speaker 2", "Speaker 3"})
        self.assertEqual(s["merged"], 0)
        self.assertEqual(s["restored"], 4)
        kept = [x for x in r if x["speaker"] == "Speaker 3"]
        self.assertTrue(all(x["speaker_vote"]["badge"] for x in kept))     # shown as 'check speaker'
        self.assertEqual(next(x for x in r if x["segment_id"] == "seg_3")["speaker"], "Speaker 2")      # a line between survivors is still corrected

    def run_service(self, job, rws, votes, **patches):
        seen = []

        def fake_apply(r, v, allow_new=True, **kw):
            seen.append(dict(kw, allow_new=allow_new))
            return {"status": "skipped"}

        import contextlib
        with contextlib.ExitStack() as stack:
            for target, name, value in [(svl, "collect", lambda *a, **k: dict(votes, usage={})), (svl, "apply", fake_apply),
                                        (ld, "_mark", lambda *a, **k: None), (ld, "_ev", lambda *a, **k: None), (ld, "_save", lambda j: None),
                                        (ld, "_speaker_check_refund", lambda *a, **k: None)] + [(ld, k, v) for k, v in patches.items()]:
                stack.enter_context(mock.patch.object(target, name, value))
            ld._vote_speakers(job, rws, Path("."), Path("v.wav"))
        return seen

    def test_the_service_keeps_the_detectors_speakers_even_after_a_keep_answer(self):
        for choice in (None, "keep"):
            job = {"id": "j", "stated_speakers": 3, "analysis": {"audio_duration": 100}, **({"speaker_merge_choice": choice} if choice else {})}
            seen = self.run_service(job, self.rows(), self.votes())
            self.assertEqual(len(seen), 1, choice)
            self.assertTrue(seen[0]["keep"]);self.assertFalse(seen[0]["allow_new"]);self.assertFalse(seen[0].get("fresh"))

    def test_nobody_is_asked_when_the_detector_found_the_number(self):
        job = {"id": "j", "stated_speakers": 3, "analysis": {"audio_duration": 100}}
        with mock.patch.object(svl, "collect", lambda *a, **k: dict(self.votes(), usage={})), \
                mock.patch.object(ld, "_mark", lambda *a, **k: None), mock.patch.object(ld, "_ev", lambda *a, **k: None), \
                mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_speaker_check_refund", lambda *a, **k: None), \
                mock.patch.object(svl, "reask", lambda *a, **k: self.fail("no second look is needed")):
            s = ld._vote_speakers(job, self.rows(), Path("."), Path("v.wav"))
        self.assertEqual(s["status"], "ok")
        self.assertNotIn("fewer", s)

    def test_fewer_found_or_the_switch_off_uses_the_old_way(self):
        job = {"id": "j", "stated_speakers": 4, "analysis": {"audio_duration": 100}}
        self.assertFalse(any(k.get("keep") for k in self.run_service(job, self.rows(), self.votes())))
        job = {"id": "j", "stated_speakers": 3, "analysis": {"audio_duration": 100}}
        self.assertFalse(any(k.get("keep") for k in self.run_service(job, self.rows(), self.votes(), KEEP_DETECTED=False)))


class SwapPlace(unittest.TestCase):
    """The detector found the user's number by chance: it glued two people together (a mother and her daughter) and made a separate
    speaker of half an 'Mm-hmm' that no check backs. The person both checks agree on takes that empty place; the number stays the same."""
    def rows(self):
        # Speaker 1: mother (0-2) + daughter (3-7); Speaker 2: lines 8-13; Speaker 3: line 14 only ('Mm'); Speaker 4: lines 15-19
        return rows(20, lambda i: "Speaker 1" if i < 8 else "Speaker 2" if i < 14 else "Speaker 3" if i == 14 else "Speaker 4")

    def votes(self, line14="B", extra=None):
        lab = {f"seg_{i}": ("A" if i < 3 else "D" if i < 8 else "B" if i < 14 else line14 if i == 14 else "C") for i in range(20)}
        lab.update(extra or {})
        return {"dialogue": dict(lab), "audio": dict(lab)}

    def people(self, r):
        return {x["speaker"] for x in r}

    def test_the_person_both_checks_agree_on_takes_the_unbacked_place(self):
        r = self.rows()
        s = svl.apply(r, self.votes(), allow_new=False, keep=True)
        self.assertEqual(s["status"], "ok")
        self.assertEqual(len(self.people(r)), 4)                    # still the user's number
        self.assertNotIn("Speaker 3", self.people(r))               # the half 'Mm-hmm' speaker gave its place away
        self.assertEqual([x["speaker"] for x in r if x["segment_id"] == "seg_14"], ["Speaker 2"])     # its line went to who said it
        first = {x["speaker"] for x in r[:3]}; second = {x["speaker"] for x in r[3:8]}
        self.assertEqual(len(first), 1); self.assertEqual(len(second), 1); self.assertNotEqual(first, second)      # mother and daughter apart
        self.assertEqual([d["old"] for d in s["swapped"]], ["Speaker 3"])
        self.assertEqual(len(s["new_speakers"]), 1)
        self.assertEqual(s["merged"], 0)

    def test_a_small_speaker_a_check_backs_is_never_given_away(self):
        r = self.rows()
        s = svl.apply(r, self.votes(line14="E"), allow_new=False, keep=True)      # both checks: line 14 is somebody else
        self.assertIn("Speaker 3", self.people(r))
        self.assertEqual(len(self.people(r)), 4)
        self.assertEqual(s["swapped"], [])
        self.assertEqual({x["speaker"] for x in r[:8]}, {"Speaker 1"})           # nothing to swap with: the lines stay, marked
        self.assertTrue(any(x["speaker_vote"]["badge"] for x in r[:8]))

    def test_one_free_place_goes_to_the_person_with_more_backed_lines(self):
        # Speaker 4 now has lines 15-25; a third person N is heard in lines 23-25 (3 lines), the daughter has 5: there is one free place
        r = rows(26, lambda i: "Speaker 1" if i < 8 else "Speaker 2" if i < 14 else "Speaker 3" if i == 14 else "Speaker 4")
        lab = {f"seg_{i}": ("A" if i < 3 else "D" if i < 8 else "B" if i < 15 else "N" if i >= 23 else "C") for i in range(26)}
        s = svl.apply(r, {"dialogue": dict(lab), "audio": dict(lab)}, allow_new=False, keep=True)
        self.assertEqual(len(self.people(r)), 4)
        self.assertEqual(len({x["speaker"] for x in r[3:8]} & {x["speaker"] for x in r[:3]}), 0)      # the daughter got it
        self.assertEqual({x["speaker"] for x in r[15:26]}, {"Speaker 4"})                            # N stays on its speaker, marked
        self.assertTrue(all(x["speaker_vote"]["badge"] for x in r[23:26]))
        self.assertEqual(len(s["swapped"]), 1)

    def test_too_few_lines_for_a_new_person_changes_nothing(self):
        r = self.rows()
        lab = self.votes(extra={f"seg_{i}": "A" for i in range(3, 6)})      # the 'daughter' now has only 2 lines
        s = svl.apply(r, lab, allow_new=False, keep=True)
        self.assertEqual(s["swapped"], [])
        self.assertIn("Speaker 3", self.people(r))                  # given back as before, marked 'check speaker'
        self.assertEqual(s["restored"], 1)

    def test_when_the_detector_found_fewer_an_unbacked_speaker_also_gives_its_place(self):
        # stated 5, the detector found 4: mother+daughter on Speaker 1, the judge on Billy's Speaker 2, a one-line Speaker 3, Speaker 4.
        r = rows(28, lambda i: "Speaker 1" if i < 8 else "Speaker 2" if i < 20 else "Speaker 3" if i == 20 else "Speaker 4")
        lab = {f"seg_{i}": ("A" if i < 3 else "D" if i < 8 else "B" if i < 14 else "J" if i < 20 else "B" if i == 20 else "C") for i in range(28)}
        v = {"dialogue": dict(lab), "audio": dict(lab)}
        plain = svl.apply([dict(x) for x in r], v, allow_new=True, max_new=1, min_new_lines=2)      # before: one place, the judge stays on Billy
        self.assertEqual(plain["swapped"], [])
        s = svl.apply(r, v, allow_new=True, max_new=1, min_new_lines=2, cap=5)
        self.assertEqual(len(self.people(r)), 5)
        judge = {x["speaker"] for x in r[14:20]}; billy = {x["speaker"] for x in r[8:14]}
        self.assertEqual(len(judge), 1); self.assertNotEqual(judge, billy)                  # the judge has his own speaker
        self.assertEqual(len({x["speaker"] for x in r[:3]} | {x["speaker"] for x in r[3:8]}), 2)      # mother and daughter apart
        self.assertEqual([d["old"] for d in s["swapped"]], ["Speaker 3"])
        self.assertEqual(r[20]["speaker"], r[8]["speaker"])                                 # its line went to Billy

    def test_never_more_than_the_users_number(self):
        r = rows(28, lambda i: "Speaker 1" if i < 8 else "Speaker 2" if i < 20 else "Speaker 3" if i == 20 else "Speaker 4")
        lab = {f"seg_{i}": ("A" if i < 3 else "D" if i < 8 else "B" if i < 14 else "J" if i < 20 else "B" if i == 20 else "C") for i in range(28)}
        s = svl.apply(r, {"dialogue": dict(lab), "audio": dict(lab)}, allow_new=True, max_new=1, min_new_lines=2, cap=4)
        self.assertLessEqual(len(self.people(r)), 4)
        self.assertEqual(s["swapped"], [])

    def test_the_log_says_who_got_no_place(self):
        t = ld._vote_why({"candidates": [{"first": "seg_105", "lines": 9, "backed_lines": 8, "reason": "new_speaker_limit"}],
                          "unbacked": ["Speaker 3"], "rename": {}})
        self.assertIn("from seg_105 (9 lines, 8 backed by 2 checks: no free place)", t)
        self.assertIn("detector speakers no check backs: Speaker 3", t)
        self.assertEqual(ld._vote_why({}), "")

    def test_the_log_names_the_swap(self):
        note = ld._swap_note({"swapped": [{"old": "Speaker 8", "old_lines": 1}], "new_speakers": ["Speaker 14"], "rename": {"Speaker 14": "Speaker 13"}})
        self.assertIn("Speaker 8 (1 line(s)) -> Speaker 13", note)
        self.assertEqual(ld._swap_note({"swapped": []}), "")


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

        def fake_apply(r, votes, allow_new=True, **kw):
            seen["allow_new"] = allow_new
            seen["kw"] = kw
            return {"status": "skipped"}

        job = {"id": "j", "stated_speakers": 2, "analysis": {"audio_duration": 100}}
        with mock.patch.object(svl, "collect", fake_collect), mock.patch.object(svl, "apply", fake_apply), \
                mock.patch.object(ld, "_mark", lambda *a, **k: None), mock.patch.object(ld, "_ev", lambda *a, **k: None), \
                mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_speaker_check_refund", lambda *a, **k: None):
            ld._vote_speakers(job, rows(4), Path("."), Path("v.wav"))
        self.assertEqual(seen["collect"]["people"], 2)
        self.assertFalse(seen["allow_new"])
        self.assertTrue(seen["kw"].get("keep"))       # the detector found the 2: they are kept

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
