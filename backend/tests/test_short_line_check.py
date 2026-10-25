"""A very short line whose speaker the vote changed (or the voters did not all back) stays "check speaker". Offline, invented data.

Why: in a real run the vote moved "Good." (0.5 s) and "your face?" (0.4 s) to the wrong person, 2 voters of 3 backing each, and the vote's
"high confidence" erased the short-line mark, so the log said "0 to check". A one-word reply is where the checks fail together."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import longdub_service as ld

ROOT = Path(__file__).resolve().parents[1]
TURNS = [{"start": 0, "end": 3, "speaker": "raw_a"}, {"start": 3, "end": 12, "speaker": "raw_b"}]
MAP = {"raw_a": "Speaker 1", "raw_b": "Speaker 2"}


def row(i, a, b, speaker, text, vote):
    r = {"segment_id": f"seg_{i}", "start": a, "end": b, "speaker": speaker, "text": text, "words": [{"word": "x", "start": a, "end": b}]}
    if vote is not None:
        r["speaker_vote"] = vote
    return r


def other(i=9):
    """A long line of the other speaker: the review only marks short lines when more than one speaker has lines."""
    return row(i, 8.0, 12.0, "Speaker 1", "a long line with plenty of words in it", vote("app", 3, 3))


def vote(source, support, present, badge=False, reasons=()):
    return {"source": source, "support": support, "present": present, "badge": badge, "reasons": list(reasons)}


class ShortLines(unittest.TestCase):
    def review(self, rows, voted=True):
        events = []
        with mock.patch.object(ld, "_ev", lambda j, step, status="ok", detail="", **k: events.append((step, status, detail))):
            ld._review_speakers({"id": "j", "stated_speakers": 2}, rows, TURNS, MAP, vote={"status": "ok"} if voted else None)
        return events

    def test_a_short_line_the_vote_changed_stays_marked_even_with_a_majority(self):
        rows = [row(0, 3.0, 6.0, "Speaker 2", "a long line with plenty of words in it", vote("app", 3, 3)),
                row(1, 6.0, 6.5, "Speaker 1", "Good.", vote("vote", 2, 3))]
        events = self.review(rows)
        self.assertEqual(rows[1]["speaker_confidence"], "low")
        self.assertIn("short_reply", rows[1]["speaker_reasons"])
        self.assertEqual(rows[0]["speaker_confidence"], "high")
        self.assertIn("1 to check", events[0][2])

    def test_a_short_line_the_voters_did_not_all_back_stays_marked_even_when_unchanged(self):
        rows = [row(0, 3.0, 3.4, "Speaker 2", "Okay.", vote("app", 2, 3)), other()]
        self.review(rows)
        self.assertEqual(rows[0]["speaker_confidence"], "low")

    def test_a_short_line_every_voter_backed_and_left_as_it_was_is_cleared_as_before(self):
        rows = [row(0, 3.0, 3.4, "Speaker 2", "Okay.", vote("app", 3, 3)), other()]
        self.review(rows)
        self.assertEqual(rows[0]["speaker_confidence"], "high")

    def test_a_long_line_the_vote_changed_is_not_marked_by_this_rule(self):
        rows = [row(0, 3.0, 7.0, "Speaker 2", "a longer line that the voters moved with a clear majority", vote("vote", 3, 3)), other()]
        self.review(rows)
        self.assertEqual(rows[0]["speaker_confidence"], "high")

    def test_without_a_vote_nothing_changes(self):
        rows = [row(0, 3.0, 3.4, "Speaker 2", "Okay.", None), other()]
        self.review(rows, voted=False)
        self.assertEqual(rows[0]["speaker_confidence"], "low")
        self.assertIn("short_reply", rows[0]["speaker_reasons"])

    def test_a_line_the_user_set_by_hand_is_not_touched_by_the_vote_at_all(self):
        rows = [row(0, 3.0, 3.4, "Speaker 2", "Okay.", vote("app", 1, 1, reasons=["manual_choice"])), other()]
        self.review(rows)
        self.assertEqual(rows[0]["speaker_confidence"], "low")          # (the detector's short-line mark, as before)

    def test_the_rule_is_in_the_review_step(self):
        src = (ROOT / "longdub_service.py").read_text(encoding="utf-8").replace("\r\n", "\n")
        i = src.index("def _review_speakers(")
        self.assertIn('sv.get("source") in ("vote", "new")', src[i:i + 6000])


if __name__ == "__main__":
    unittest.main()
