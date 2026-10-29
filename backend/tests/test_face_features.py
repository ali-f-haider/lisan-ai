"""The gender of a cut-out face by counting visible features (face_features.py). Offline: no AI is called."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import face_features as FF


class Counting(unittest.TestCase):
    def test_the_side_with_more_points_wins(self):
        self.assertEqual(FF.score({"G1": "yes", "M1": "yes", "M2": "yes", "C7": "F"})["gender"], "male")
        self.assertEqual(FF.score({"G1": "yes", "F1": "yes", "C3": "F", "C2": "M"})["gender"], "female")
        self.assertEqual(FF.score({"G1": "yes", "F1": "yes", "M1": "yes"})["gender"], "unsure")

    def test_cant_see_and_neither_give_no_point_and_a_covered_face_gives_no_answer(self):
        masked = {q: "cant_see" for q, _, _ in FF.FEATURES}
        masked.update({q: "cant_see" for q, _ in FF.CHOICES}, G1="yes")
        self.assertEqual(FF.score(masked)["gender"], "unsure")                  # the masked pilot: nothing visible, nothing decided
        self.assertEqual(FF.score({"G1": "yes", "C1": "neither"})["gender"], "unsure")
        self.assertEqual(FF.score({"G1": "no", "F1": "yes", "F2": "yes"})["gender"], "cannot_judge")

    def test_every_question_belongs_to_one_side_and_no_feature_is_asked_twice(self):
        self.assertTrue(all(g in ("M", "F") for _, _, g in FF.FEATURES))
        ids = [q for q, _ in FF.QUESTIONS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(FF.GATE_REQUIRED, ("G1",))                                  # only "is a face visible?"
        for removed in ("lit well", "completely smooth", "bump on the bridge", "large compared"):              # questions the film test showed to mislead
            self.assertFalse(any(removed in t.lower() for _, t in FF.QUESTIONS), removed)

    def test_the_ai_is_never_asked_for_the_gender_itself(self):
        self.assertIn("Do not say whether the person is male or female", FF.PROMPT)
        self.assertIn("never name, recognise or identify anyone", FF.PROMPT)
        props = FF.SCHEMA["properties"]["faces"]["items"]["properties"]["answers"]["properties"]
        self.assertEqual(set(props), {q for q, _ in FF.QUESTIONS})
        self.assertNotIn("gender", props)


if __name__ == "__main__":
    unittest.main()
