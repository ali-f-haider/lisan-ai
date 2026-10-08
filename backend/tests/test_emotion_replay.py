import json
from pathlib import Path
import unittest

from tools.emotion_replay import replay


class ReplayTests(unittest.TestCase):
    def test_invented_alternating_speakers_and_pauses(self):
        rows = json.loads((Path(__file__).parent / "fixtures/emotion_scene.json").read_text(encoding="utf-8"))
        result = replay(rows)
        fast = [r for r in result["lines"] if r["speaker"] == "A"]
        self.assertTrue(all(6 <= r["line_rate"] <= 7 for r in fast))
        self.assertTrue(all(r["pace"] == "fast" for r in fast))
        paused = [r for r in result["lines"] if r["segment_id"] in {f"line_{i:02}" for i in range(13, 24, 2)}]
        self.assertTrue(all(2 <= r["line_rate_before"] <= 4 for r in paused))
        self.assertTrue(all(r["pace"] == "unknown" and r["emotion"] == "anxious" for r in paused))
        self.assertGreater(result["slow_tags_before"], result["slow_tags_after"])
        json.dumps(result, allow_nan=False)
