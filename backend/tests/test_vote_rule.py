"""'listen_first': the listening check decides a line unless two other voters agree on one other person."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speaker_vote as V
import speaker_vote_live as svl

IDS = [f"s{i}" for i in range(12)]


def lines(app):
    return [{"segment_id": i, "speaker": app[k]} for k, i in enumerate(IDS)]


class Rule(unittest.TestCase):
    def setUp(self):
        self.dialogue = {i: ("A" if k < 6 else "B") for k, i in enumerate(IDS)}
        self.app = ["P1" if k < 6 else "P2" for k in range(12)]

    def decide(self, audio, rule, app=None, picture=None):
        voters = {"dialogue": self.dialogue, "audio": audio, **({"picture": picture} if picture else {})}
        res = V.decide(lines(app or self.app), voters, {"rule": rule, "audio_weight": 2})
        return {d["segment_id"]: d for d in res["lines"]}

    def test_listening_wins_against_one_other_voter(self):
        audio = dict(self.dialogue); audio["s0"] = "B"
        app = list(self.app)      # the detector agrees with the reading on s0 (P1 = A): two others agree -> overruled
        out = self.decide(audio, "listen_first", app)
        self.assertEqual(out["s0"]["speaker"], "P1")
        self.assertIn("listen_overruled", out["s0"]["reasons"])
        app[0] = "P2"             # now only the reading says A: the listening check wins
        out = self.decide(audio, "listen_first", app)
        self.assertEqual(out["s0"]["speaker"], "P2")

    def test_the_old_rule_is_unchanged_by_default(self):
        self.assertEqual(V.Policy().rule, "plurality")
        self.assertIsNone(V._policy({"rule": "nonsense"}))

    def test_the_app_keeps_the_weighted_majority_by_default(self):
        self.assertEqual(svl.VOTE_RULE, "plurality")


if __name__ == "__main__":
    unittest.main()
