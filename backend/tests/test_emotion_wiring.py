"""The long dub's delivery (emotion) comes from LISTENING to the isolated voice of each line, never from the text alone:
the text pass is no longer asked for speed words, a failed listening step never leaves a speed guess, a delivery the user picked
is never touched, and every pass writes an `emotions` line to the job's record. All offline: the AI and the audio are replaced."""
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import emotion_listen as E
import gemini_service as gs
import longdub_service as ld
from config import CANONICAL_EMOTIONS

PACING = ("slowly", "drawn out", "rushed", "hesitant", "stammering")


def row(i, start, end, emotion="anxious, slowly", **kw):
    r = {"segment_id": f"seg_{i}", "start": start, "end": end, "speaker": "Speaker 1", "text": "we have two boats coming at us now",
         "emotion": emotion, "words": []}
    r.update(kw)
    return r


class PromptTests(unittest.TestCase):
    def prompt(self):
        sent = []

        def fake(key, payload, timeout=0):
            sent.append(payload["contents"][0]["parts"][0]["text"])
            text = json.dumps([{"segment_id": "a", "arabic_text": "x", "emotion": "anxious, slowly, rushed"}])
            return {"candidates": [{"content": {"parts": [{"text": text}]}}]}, None

        seg = mock.Mock(segment_id="a", start=0, end=2, text="hello there", speaker="S1")
        with mock.patch.object(gs, "call_gemini", fake), mock.patch.object(gs, "record_gemini", lambda *a, **k: None):
            res = gs.translate_segments("job", [seg], "key", paces={"a": "slow"})
        return sent[0], res

    def test_no_forced_second_tag_and_no_speed_words_in_the_choice_list(self):
        text, _ = self.prompt()
        self.assertNotIn("exactly TWO", text)
        self.assertNotIn("MUST return exactly", text)
        listing = next(l for l in text.splitlines() if l.startswith("neutral, happy"))
        for tag in PACING:
            self.assertNotIn(tag, [t.strip() for t in listing.split(",")])
        self.assertNotIn("beats the meaning of the words", text)
        self.assertIn('"emotion": "neutral"}', text)
        self.assertNotIn("neutral, conversational", text)

    def test_a_pace_the_text_pass_still_returns_is_removed_afterwards(self):
        _, res = self.prompt()
        self.assertNotIn("slowly", res["translated_segments"][0]["emotion"])      # measured_pace "slow" + anxious: the coherence rule
        self.assertNotIn("rushed", res["translated_segments"][0]["emotion"])      # pace "slow": a rushed tag has no support


class TextPassTests(unittest.TestCase):
    def test_the_text_pass_never_decides_a_speed(self):
        res = {"status": "success", "translated_segments": [
            {"segment_id": "seg_1", "arabic_text": "مرحبا", "emotion": "terrified, slowly"},
            {"segment_id": "seg_2", "arabic_text": "أهلا", "emotion": "rushed"},
            {"segment_id": "seg_3", "arabic_text": "نعم", "emotion": "sad, softly"}]}
        with mock.patch.object(gs, "translate_segments", return_value=res):
            got = ld._translate_batch("job", [row(1, 0, 2), row(2, 3, 5), row(3, 6, 8)])
        self.assertEqual(got["seg_1"][1], "terrified")
        self.assertEqual(got["seg_2"][1], "neutral")
        self.assertEqual(got["seg_3"][1], "sad, softly")


class ListenMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self.tmp.name)
        (self.wd / "vocals_mono.wav").write_bytes(b"x")
        self.events = []
        self.job = {"id": "job"}
        p = mock.patch.object(ld, "_ev", lambda job, step, status="ok", detail="", credits=None: self.events.append((step, status, detail)))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(ld, "GEMINI_API_KEY", "key")
        p.start()
        self.addCleanup(p.stop)

    def heard(self, **by_id):
        return mock.patch.object(E, "listen", return_value=by_id)

    def test_listened_emotion_replaces_the_text_guess_and_no_speed_without_agreement(self):
        rows = [row(1, 0, 3, "terrified"), row(2, 4, 7, "neutral")]
        ev = {"seg_1": {"emotion": "anxious", "heard_speed": "slow", "confidence": "high", "listened": True, "reason": ""}}
        with self.heard(**ev):
            n = ld._listen_and_merge(self.job, rows, self.wd)
        self.assertEqual(n, 1)
        self.assertEqual(rows[0]["emotion"], "anxious")           # slow was heard, but anxious is urgent: no speed tag
        self.assertEqual(rows[1]["emotion"], "neutral")           # nothing heard: the text guess without speed words
        step, status, detail = self.events[-1]
        self.assertEqual(step, "emotions")
        self.assertEqual(status, "partial")
        self.assertIn("listened 1", detail)

    def test_failed_listening_leaves_no_speed_guess(self):
        rows = [row(1, 0, 3, "calm, slowly"), row(2, 4, 7, "excited, rushed, very fast")]
        with self.heard():
            ld._listen_and_merge(self.job, rows, self.wd)
        self.assertEqual(rows[0]["emotion"], "confident")
        self.assertEqual(rows[1]["emotion"], "excited")

    def test_missing_voice_file_or_key_falls_back_the_same_way(self):
        (self.wd / "vocals_mono.wav").unlink()
        rows = [row(1, 0, 3, "fearful, drawn out")]
        with mock.patch.object(E, "listen", side_effect=AssertionError("must not listen")):
            ld._listen_and_merge(self.job, rows, self.wd)
        self.assertEqual(rows[0]["emotion"], "fearful")
        self.assertEqual(self.events[-1][0], "emotions")

    def test_a_delivery_the_user_picked_is_never_touched_or_listened_to(self):
        rows = [row(1, 0, 3, "whispering, slowly", emotion_set=True), row(2, 4, 7, "sad, slowly")]
        with self.heard():
            ld._listen_and_merge(self.job, rows, self.wd)
        self.assertEqual(rows[0]["emotion"], "whispering, slowly")
        self.assertEqual(rows[1]["emotion"], "sad")

    def test_only_selected_lines_change_after_an_edit(self):
        rows = [row(1, 0, 3, "sad, slowly"), row(2, 4, 7, "sad, slowly")]
        with mock.patch.object(E, "listen", return_value={}) as lst:
            ld._listen_and_merge(self.job, rows, self.wd, selected_ids={"seg_2"})
        self.assertEqual(lst.call_args.kwargs["selected_ids"], {"seg_2"})
        self.assertEqual(rows[0]["emotion"], "sad, slowly")
        self.assertEqual(rows[1]["emotion"], "sad")

    def test_a_crash_in_the_listener_never_breaks_the_job(self):
        rows = [row(1, 0, 3, "sad, slowly")]
        with mock.patch.object(E, "listen", side_effect=RuntimeError("boom")):
            self.assertEqual(ld._listen_and_merge(self.job, rows, self.wd), 0)

    def test_the_event_is_short_and_names_no_vendor(self):
        rows = [row(i, i * 4, i * 4 + 3, "sad") for i in range(40)]
        with self.heard():
            ld._listen_and_merge(self.job, rows, self.wd)
        detail = self.events[-1][2]
        self.assertLessEqual(len(detail), 600)
        for word in ("gemini", "google", "openai", "eleven", "inworld"):
            self.assertNotIn(word, detail.lower())


class EditPathTests(unittest.TestCase):
    def test_retranslate_listens_to_that_line_only(self):
        rows = [row(1, 0, 3, "sad"), row(2, 4, 7, "sad")]
        job = {"id": "job", "status": "editing"}
        calls = []
        with mock.patch.object(ld, "read_segments", return_value=rows), mock.patch.object(ld, "_write_segments"), \
                mock.patch.object(ld, "_translate_batch", return_value={"seg_2": ("نص", "terrified")}), \
                mock.patch.object(ld, "_listen_and_merge", lambda *a, **k: calls.append(k)):
            ok, _, _ = ld.retranslate_line(job, "seg_2")
        self.assertTrue(ok)
        self.assertEqual(calls[0]["selected_ids"], {"seg_2"})

    def test_a_retranslated_line_with_a_manual_delivery_is_not_listened_to(self):
        rows = [row(1, 0, 3, "sad", emotion_set=True)]
        job = {"id": "job", "status": "editing"}
        calls = []
        with mock.patch.object(ld, "read_segments", return_value=rows), mock.patch.object(ld, "_write_segments"), \
                mock.patch.object(ld, "_translate_batch", return_value={"seg_1": ("نص", "terrified")}), \
                mock.patch.object(ld, "_listen_and_merge", lambda *a, **k: calls.append(k)):
            ld.retranslate_line(job, "seg_1")
        self.assertEqual(calls, [])
        self.assertEqual(rows[0]["emotion"], "sad")

    def test_split_and_analysis_use_the_listening_step(self):
        self.assertIn("_listen_and_merge(job, _by_start(", inspect.getsource(ld.split_line))
        src = inspect.getsource(ld._run_analysis)
        self.assertLess(src.index("_translate_all(job, rows)"), src.index("_listen_and_merge(job, rows, wd"))
        at = src.index("_listen_and_merge(job, rows, wd")
        self.assertLess(at - src.index("_translate_all(job, rows)"), 400)          # right after the translation ...
        self.assertLess(src.index("_write_segments(job, rows)", at) - at, 200)     # ... and before the lines are saved

    def test_glossary_repair_keeps_the_listened_delivery(self):
        src = inspect.getsource(ld.glossary_apply)
        self.assertNotIn('r["emotion"] = emo', src)


if __name__ == "__main__":
    unittest.main()
