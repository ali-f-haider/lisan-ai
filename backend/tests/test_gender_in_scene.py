"""Genders heard in the whole scene (gender_context.hear_in_context, 1.84.4): on Air Force One the hijacker (shouting) and another
man were guessed 'female (pitch, unsure)'. The strongest model hears each speaker's lines in the scene; only an unsure guess is
replaced, the words and the person's own choice never are, and two sure opinions that disagree only mark the speaker."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gender_context as gc

ROWS = [{"segment_id": "seg_0", "start": 120.0, "end": 122.0, "text": "Shut up, goddammit!", "speaker": "Speaker 1"},
        {"segment_id": "seg_1", "start": 125.0, "end": 127.0, "text": "He's alive!", "speaker": "Speaker 5"},
        {"segment_id": "seg_2", "start": 130.0, "end": 131.0, "text": "This is Marshall.", "speaker": "Speaker 3"}]


def answer(obj):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}],
            "usageMetadata": {"promptTokenCount": 17000, "candidatesTokenCount": 200, "thoughtsTokenCount": 0}}


class InScene(unittest.TestCase):
    def test_one_request_for_all_asked_speakers_with_their_lines(self):
        seen = []
        heard, usage = gc.hear_in_context("j", "QUFB", ROWS, ["Speaker 1", "Speaker 5"], "k",
                                          ask=lambda p: (seen.append(p) or (answer({"speakers": [
                                              {"name": "Speaker 1", "gender": "male", "sure": True},
                                              {"name": "Speaker 5", "gender": "female", "sure": False},
                                              {"name": "Speaker 9", "gender": "male", "sure": True}]}), None)))
        self.assertEqual(len(seen), 1)
        text = seen[0]["contents"][0]["parts"][1]["text"]
        self.assertIn("Shut up, goddammit!", text)
        self.assertNotIn("This is Marshall.", text)                    # a speaker not asked about is not listed
        self.assertEqual(heard, {"Speaker 1": {"gender": "male", "child": False, "sure": True},
                                 "Speaker 5": {"gender": "female", "child": False, "sure": False}})
        self.assertEqual((usage["calls"], usage["tokens_in"]), (1, 17000))

    def test_only_an_unsure_guess_is_replaced(self):
        found = {"Speaker 1": {"gender": "female", "source": "pitch", "uncertain": True},
                 "Speaker 2": {"gender": "male", "source": "text", "uncertain": False},
                 "Speaker 3": {"gender": "male", "source": "heard", "uncertain": False},
                 "Speaker 4": {"gender": "female", "source": "chosen", "uncertain": False}}
        heard = {"Speaker 1": {"gender": "male", "sure": True}, "Speaker 2": {"gender": "female", "sure": True},
                 "Speaker 3": {"gender": "female", "sure": True}, "Speaker 4": {"gender": "male", "sure": True}}
        changed = gc.apply_context(found, heard)
        self.assertEqual(found["Speaker 1"], {"gender": "male", "source": "heard", "uncertain": False})
        self.assertEqual(found["Speaker 2"]["gender"], "male")           # the words win
        self.assertEqual(found["Speaker 4"]["gender"], "female")         # the person's choice wins
        self.assertEqual((found["Speaker 3"]["gender"], found["Speaker 3"]["uncertain"]), ("male", True))   # two sure opinions: marked
        self.assertEqual(sorted(changed), ["Speaker 1", "Speaker 3"])

    def test_an_unsure_answer_does_not_overrule_a_guess(self):
        found = {"Speaker 5": {"gender": "female", "source": "pitch", "uncertain": True}}
        gc.apply_context(found, {"Speaker 5": {"gender": "male", "sure": False}})
        self.assertEqual(found["Speaker 5"]["gender"], "female")

    def test_a_bad_answer_changes_nothing(self):
        heard, _ = gc.hear_in_context("j", "QUFB", ROWS, ["Speaker 1"], "k",
                                      ask=lambda p: ({"candidates": [{"content": {"parts": [{"text": "no idea"}]}}]}, None))
        self.assertEqual(heard, {})


if __name__ == "__main__":
    unittest.main()
