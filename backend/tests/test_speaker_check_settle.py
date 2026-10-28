"""The speaker check is charged as a reserve (4 + 20 per minute) and settled to what it really used: its tokens at the December
price of the strongest model, 1 credit per cent, plus the flat part. The rest is given back once."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import longdub_service as ld
import speaker_vote_live as svl


class Settle(unittest.TestCase):
    def run_it(self, job, usage):
        backs, evs = [], []
        with mock.patch.object(ld, "_refund", lambda j, uid, n, slot, key: backs.append((n, slot, key)) or True), \
                mock.patch.object(ld, "_save", lambda j: None), mock.patch.object(ld, "_ev", lambda *a: evs.append(a)), \
                mock.patch.object(ld, "_job_pricing", lambda j: {"speaker_check_flat": 4, "speaker_check_per_min": 20.0}):
            got = ld._speaker_check_settle(job, usage)
        return got, backs, evs

    def test_the_unused_part_of_the_reserve_is_given_back_once(self):
        job = {"id": "j", "uid": "u", "speaker_check_paid": 152, "analysis": {}}
        usage = {"calls": 2, "tokens_in": 25000, "tokens_out": 4000, "tokens_thinking": 40000}   # $0.10 + $0.88 at $4 / $20
        got, backs, _ = self.run_it(job, usage)
        self.assertEqual(backs, [(152 - (4 + 98), "analysis", "speaker_check_unused_part")])
        self.assertEqual(job["speaker_check_paid"], 102)
        again, backs2, _ = self.run_it(job, usage)
        self.assertEqual((again, backs2), (0, []))

    def test_a_check_that_used_more_than_the_reserve_costs_nothing_extra(self):
        job = {"id": "j", "uid": "u", "speaker_check_paid": 40, "analysis": {}}
        got, backs, evs = self.run_it(job, {"calls": 2, "tokens_in": 30000, "tokens_out": 5000, "tokens_thinking": 90000})
        self.assertEqual((got, backs), (0, []))
        self.assertIn("nothing to give back", evs[-1][3])

    def test_saved_answers_reuse_the_first_runs_usage(self):
        job = {"id": "j", "uid": "u", "speaker_check_paid": 152,
               "analysis": {"speaker_check_usage": {"calls": 2, "tokens_in": 25000, "tokens_out": 4000, "tokens_thinking": 40000}}}
        got, backs, _ = self.run_it(job, {"calls": 0})
        self.assertEqual(backs[0][0], 50)

    def test_both_checks_think_hard(self):
        self.assertEqual((svl.THINKING, svl.LISTEN_THINKING), ("high", "high"))


if __name__ == "__main__":
    unittest.main()
