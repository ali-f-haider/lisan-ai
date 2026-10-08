import math
import unittest

import delivery as D
from test_delivery import row


class EvidenceTests(unittest.TestCase):
    def test_speaker_neighbours_are_not_other_peoples_pace(self):
        rows = []
        for i in range(6):
            rows.append(row(i * 4, i * 30, 3, 10, speaker="A"))
            rows.extend(row(i * 4 + j, i * 30 + j * 5, 4, 6, speaker="B") for j in range(1, 4))
        self.assertGreater(D.moment_rate(rows, 12), 6)
        self.assertEqual(D.pace(rows, 12), "fast")

    def test_too_few_same_speaker_lines_fall_back_to_local_window(self):
        rows = [row(i, i * 5, 3, 10, speaker=str(i)) for i in range(4)]
        self.assertGreater(D.moment_rate(rows, 2), 6)
        self.assertEqual(D.pace(rows, 2), "fast")
        self.assertIsNone(D.moment_rate(rows[:2], 0))

    def test_short_lines_and_pause_heavy_lines_are_unknown(self):
        for candidate in (row(0, 0, 3, 4), row(0, 0, 1.4, 8), row(0, 0, 6, 10, gap=3)):
            rows = [dict(candidate, segment_id=str(i), start=i * 10) for i in range(7)]
            self.assertEqual(D.pace(rows, 3), "unknown")

    def test_quarter_second_pause_is_removed(self):
        r = row(0, 0, 4, 6)
        # An exactly 0.25 second internal gap must not enter voiced time.
        for w in r["words"][3:]:
            w["start"] += 0.25
            w["end"] += 0.25
        r["words"][2]["end"] = r["words"][3]["start"] - 0.25
        spoken = r["words"][-1]["end"] - r["words"][0]["start"] - 0.25
        self.assertAlmostEqual(D.line_rate(r), 12 / spoken)

    def test_slot_duration_without_word_times_cannot_prove_slow_speech(self):
        rows = [{"start": 0, "end": 30, "text": "Please bring a cup over here", "speaker": "A"}] * 5
        self.assertEqual(D.pace(rows, 2), "unknown")

    def test_slow_needs_both_own_and_neighbour_evidence(self):
        rows = [row(i, i * 6, 3, 10) for i in range(9)]
        rows[4] = row(4, 24, 4, 6)
        self.assertEqual(D.pace(rows, 4), "normal")

    def test_urgent_emotions_never_keep_slow_instructions(self):
        for urgent in D.URGENT_TAGS:
            for tag in D.SLOW_TAGS:
                with self.subTest(urgent=urgent, tag=tag):
                    self.assertNotIn(tag, D.ground(urgent + ", " + tag, "slow"))

    def test_unknown_removes_all_speed_and_ground_is_total(self):
        self.assertEqual(D.ground("sad, slowly, drawn out, rushed, very fast", "unknown"), "sad")
        for invalid in ([], {}, 123, float("nan")):
            self.assertEqual(D.ground(invalid, "slow"), "neutral")
        for bad in (math.nan, math.inf, -math.inf):
            r = row(0, 0, 3, 8)
            r["words"][0]["end"] = bad
            self.assertIsNone(D.line_rate(r))

    def test_caps_are_unchanged(self):
        self.assertEqual(D.CAP, {"slow": 1.08, "normal": 1.15, "fast": 1.25, "unknown": 1.15})
