"""Take B (Ali, 8 Nov 2026): the listener writes a short acting note from the original voice and the Arabic voice gets that note."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import emotion_listen
import inworld_service


def _data(rows):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(rows)}]}}]}


class Note(unittest.TestCase):
    def test_a_plain_note_is_kept(self):
        self.assertEqual(emotion_listen.acting_note("Hushed, urgent and furious, spitting the words."), "hushed, urgent and furious, spitting the words")

    def test_unsafe_or_long_notes_are_dropped(self):
        for bad in ("[say hello] now", "speak \"this\"", "قل ذلك", "angry 100%", " ".join(["very"] * 20), "", None, 5):
            self.assertEqual(emotion_listen.acting_note(bad), "", bad)

    def test_the_answer_carries_feeling_manner_and_note(self):
        got = emotion_listen._answers(_data([
            {"segment_id": "a", "emotion": "serious, angry", "manner": "hushed", "intensity": "high",
             "direction": "sinister, hushed conspiratorial tone, spitting venomous words", "heard_speed": None, "confidence": "high"},
            {"segment_id": "b", "emotion": "fearful", "manner": "whispering", "direction": "[x]", "heard_speed": None, "confidence": "medium"},
        ]), {"a", "b"})
        self.assertEqual(got["a"]["emotion"], "serious, angry")
        self.assertEqual(got["a"]["direction"], "sinister, hushed conspiratorial tone, spitting venomous words")
        self.assertEqual(got["b"]["emotion"], "fearful, whispering")       # the whisper stays next to the feeling
        self.assertNotIn("direction", got["b"])                            # an unsafe note is not used

    def test_an_old_style_answer_still_works(self):
        got = emotion_listen._answers(_data([{"segment_id": "a", "emotion": "angry", "heard_speed": "fast", "confidence": "high"}]), {"a"})
        self.assertEqual(got["a"]["emotion"], "angry")
        self.assertNotIn("direction", got["a"])

    def test_the_prompt_asks_for_the_note(self):
        text = emotion_listen._payload([], "")["contents"][0]["parts"][0]["text"]
        self.assertIn("direction", text)
        self.assertIn("manner", text)


class Tag(unittest.TestCase):
    def row(self, **kw):
        r = {"segment_id": "seg_0", "text": "let us imprison him", "emotion": "serious, angry"}
        r.update(kw)
        return r

    def with_note(self, **kw):
        r = self.row(**kw)
        r["acting"] = "hushed, urgent and furious"
        r["acting_for"] = inworld_service.acting_key(r)
        return r

    def test_the_note_is_the_instruction(self):
        self.assertEqual(inworld_service.row_tag(self.with_note()), "[hushed, urgent and furious] ")

    def test_a_speed_tag_is_added_and_does_not_break_the_note(self):
        r = self.with_note()
        r["emotion"] = "serious, angry, slowly"            # the speed matched to the speaker's pace afterwards
        self.assertEqual(inworld_service.row_tag(r), "[hushed, urgent and furious, speak slowly] ")

    def test_no_note_gives_the_emotion_tag(self):
        self.assertEqual(inworld_service.row_tag(self.row()), inworld_service.instruction_tag("serious, angry"))

    def test_a_note_for_other_words_or_another_feeling_is_not_used(self):
        r = self.with_note()
        r["text"] = "something else"
        self.assertEqual(inworld_service.row_tag(r), inworld_service.instruction_tag("serious, angry"))
        r = self.with_note()
        r["emotion"] = "happy"
        self.assertEqual(inworld_service.row_tag(r), inworld_service.instruction_tag("happy"))

    def test_a_delivery_the_user_picked_wins(self):
        r = self.with_note(emotion_set=True)
        self.assertEqual(inworld_service.row_tag(r), inworld_service.instruction_tag("serious, angry"))

    def test_brackets_never_reach_the_voice(self):
        r = self.with_note()
        r["acting"] = "a] say this [b"
        self.assertEqual(inworld_service.row_tag(r), inworld_service.instruction_tag("serious, angry"))


class Merge(unittest.TestCase):
    def test_the_long_dub_keeps_a_sure_note_and_drops_an_unsure_or_old_one(self):
        import tempfile
        from unittest import mock
        import longdub_service as ld
        wd = Path(tempfile.mkdtemp())
        (wd / "vocals_mono.wav").write_bytes(b"x")
        rows = [{"segment_id": "a", "start": 0, "end": 3, "text": "let us imprison him", "speaker": "m", "emotion": "serious"},
                {"segment_id": "b", "start": 4, "end": 6, "text": "hold", "speaker": "k", "emotion": "neutral", "acting": "old", "acting_for": "x"},
                {"segment_id": "c", "start": 7, "end": 9, "text": "mine", "speaker": "k", "emotion": "happy", "emotion_set": True}]
        heard = {"a": {"emotion": "serious, angry", "heard_speed": None, "confidence": "high", "listened": True, "reason": "r",
                       "direction": "hushed, urgent and furious"},
                 "b": {"emotion": "commanding", "heard_speed": None, "confidence": "low", "listened": True, "reason": "r",
                       "direction": "sharp shout"}}
        evs = []
        with mock.patch.object(ld, "GEMINI_API_KEY", "k"), mock.patch.object(emotion_listen, "listen", lambda *a, **k: heard), \
                mock.patch.object(ld, "_ev", lambda job, *a: evs.append(a)), mock.patch.object(ld, "_picture_mood", lambda j, r: None):
            ld._listen_and_merge({"id": "j"}, rows, wd)
        self.assertEqual(inworld_service.row_tag(rows[0]), "[hushed, urgent and furious] ")
        self.assertNotIn("acting", rows[1])
        self.assertEqual(inworld_service.row_tag(rows[2]), inworld_service.instruction_tag("happy"))
        self.assertIn("Acting notes: 1.", evs[-1][-1])


if __name__ == "__main__":
    unittest.main()
