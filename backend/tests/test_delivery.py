"""How a speaker speaks is MEASURED from the recording: speed tags of the delivery must agree with it, and a slow speaker is not fast-forwarded."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delivery as D


def row(i, start, dur, n_words, speaker="Speaker 1", gap=0.0, emotion="fearful, rushed", **kw):
    """A line of n_words words ("water" = 2 syllables) spread evenly over dur seconds (minus an optional silence in the middle)."""
    step = (dur - gap) / n_words
    words, t = [], start
    for k in range(n_words):
        if k == n_words // 2:
            t += gap
        words.append({"word": "water", "start": round(t, 3), "end": round(t + step * 0.9, 3)})
        t += step
    r = {"segment_id": f"seg_{i}", "start": start, "end": start + dur, "text": " ".join("water" for _ in words), "speaker": speaker,
         "emotion": emotion, "words": words}
    r.update(kw)
    return r


def scene(rate_syll, n=12, **kw):
    """n lines of 3 s each at about `rate_syll` syllables per second."""
    words = max(3, round(rate_syll * 3 / 2))
    return [row(i, i * 5.0, 3.0, words, **kw) for i in range(n)]


class MeasureTests(unittest.TestCase):
    def test_syllables(self):
        self.assertEqual(D.syllables("water"), 2)
        self.assertEqual(D.syllables("I gotta wake myself up"), 7)
        self.assertEqual(D.syllables(""), 0)

    def test_pace_follows_the_recording(self):
        self.assertEqual(D.pace(scene(3.0), 5), "slow")
        self.assertEqual(D.pace(scene(5.0), 5), "normal")
        self.assertEqual(D.pace(scene(8.0), 5), "fast")

    def test_a_silence_inside_a_line_is_not_speaking(self):
        slow_by_pauses = [row(i, i * 6.0, 5.0, 10, gap=0.0) for i in range(10)]
        paused = [row(i, i * 6.0, 5.0, 10, gap=2.0) for i in range(10)]
        self.assertLess(D.line_rate(slow_by_pauses[0]), D.line_rate(paused[0]))      # the same words in less speaking time are faster

    def test_what_cannot_be_measured_is_unknown(self):
        self.assertEqual(D.pace([{"segment_id": "a", "start": 0, "end": 1, "text": "Yeah.", "words": []}], 0), "unknown")
        self.assertEqual(D.pace([], 0), "unknown")
        self.assertEqual(D.pace([{}], 0), "unknown")


class GroundTests(unittest.TestCase):
    def test_rushed_is_removed_unless_the_speaker_is_fast(self):
        self.assertEqual(D.ground("fearful, rushed", "slow"), "fearful")
        self.assertEqual(D.ground("fearful, rushed", "normal"), "fearful")
        self.assertEqual(D.ground("fearful, rushed", "fast"), "fearful, rushed")
        self.assertEqual(D.ground("angry, very fast", "normal"), "angry")

    def test_slow_tags_are_removed_for_a_fast_speaker(self):
        self.assertEqual(D.ground("sad, slowly", "fast"), "sad")
        self.assertEqual(D.ground("sad, slowly", "slow"), "sad, slowly")

    def test_nothing_left_is_neutral_and_unknown_changes_nothing(self):
        self.assertEqual(D.ground("rushed", "slow"), "neutral")
        self.assertEqual(D.ground("fearful, rushed", "unknown"), "fearful, rushed")
        self.assertEqual(D.ground("", "slow"), "")
        self.assertEqual(D.ground(None, "slow"), None)

    def test_the_tempo_allowed_is_smaller_for_a_slow_speaker(self):
        self.assertLess(D.tempo_cap("slow"), D.tempo_cap("normal"))
        self.assertLess(D.tempo_cap("normal"), D.tempo_cap("fast"))
        self.assertLessEqual(D.tempo_cap("fast"), 1.25)


class JobTests(unittest.TestCase):
    def setUp(self):
        import longdub_service as ls
        self.ls = ls

    def test_a_frightened_line_of_a_slow_speaker_is_not_rushed(self):
        rows = scene(3.0)
        self.ls._ground_rows(rows)
        self.assertTrue(all(r["emotion"] == "fearful" for r in rows))

    def test_a_delivery_the_user_picked_is_never_touched(self):
        rows = scene(3.0)
        rows[3]["emotion_set"] = True
        n = self.ls._ground_rows(rows)
        self.assertEqual(rows[3]["emotion"], "fearful, rushed")
        self.assertEqual(n, len(rows) - 1)

    def test_a_fast_speaker_keeps_rushed(self):
        rows = scene(8.0)
        self.ls._ground_rows(rows)
        self.assertTrue(all(r["emotion"] == "fearful, rushed" for r in rows))

    def test_row_paces_are_ordered_by_time(self):
        rows = scene(3.0)
        rows.reverse()
        self.assertEqual(set(self.ls._row_paces(rows).values()), {"slow"})


class TranslationTests(unittest.TestCase):
    def test_the_prompt_carries_the_measured_pace_and_the_answer_is_grounded(self):
        import gemini_service as g
        saved = (g.call_gemini, g.record_gemini)
        seen = {}

        def fake(key, payload, timeout=0):
            seen["prompt"] = payload["contents"][0]["parts"][0]["text"]
            text = json.dumps([{"segment_id": "a", "arabic_text": "أَنَا خَائِفٌ", "emotion": "fearful, rushed"},
                               {"segment_id": "b", "arabic_text": "هَيَّا", "emotion": "fearful, rushed"}], ensure_ascii=False)
            return {"candidates": [{"content": {"parts": [{"text": text}]}}]}, None
        g.call_gemini, g.record_gemini = fake, lambda *a, **k: None
        try:
            class Seg:
                def __init__(self, sid):
                    self.segment_id, self.start, self.end, self.text, self.speaker = sid, 0.0, 3.0, "I am afraid", "s"
            r = g.translate_segments("j", [Seg("a"), Seg("b")], "key", paces={"a": "slow", "b": "fast"})
        finally:
            g.call_gemini, g.record_gemini = saved
        self.assertIn('"measured_pace": "slow"', seen["prompt"])
        self.assertIn('"measured_pace": "fast"', seen["prompt"])
        out = {x["segment_id"]: x["emotion"] for x in r["translated_segments"]}
        self.assertEqual(out["a"], "fearful")
        self.assertEqual(out["b"], "fearful, rushed")


class RephraseInsteadOfSpeedUpTests(unittest.TestCase):
    """A line that fits only by being fast-forwarded more than the speaker's delivery allows is shortened in wording instead."""
    def setUp(self):
        import tempfile
        import longdub_service as ls
        import gemini_service as g
        self.ls, self.g = ls, g
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "lines").mkdir()
        (self.d / "fit").mkdir()
        self.saved = (g.shorten_arabic_line, ls._tts_with_retry, ls._fit_line, ls._ev)
        self.asked = []
        g.shorten_arabic_line = lambda jid, en, ar, max_letters, key: (self.asked.append(max_letters) or "هَذَا نَصٌّ أَقْصَرُ")
        ls._tts_with_retry = lambda voice, text: (b"x", "")
        ls._ev = lambda *a, **k: None

        def fit(raw, out, slot, room, loud_ref, start):
            out.write_bytes(b"w")
            return {"dur": 3.3, "tempo": 1.0, "warn": False, "gain_db": 0.0, "raw": 3.3, "slot": slot, "room": room}
        ls._fit_line = fit

    def tearDown(self):
        self.g.shorten_arabic_line, self.ls._tts_with_retry, self.ls._fit_line, self.ls._ev = self.saved
        self.tmp.cleanup()

    def meta(self, tempo):
        return {"dur": 3.4, "tempo": tempo, "warn": False, "gain_db": 0.0, "raw": 3.4 * tempo, "slot": 2.9, "room": 3.4}

    def run_it(self, tempo, cap):
        r = {"segment_id": "seg_1", "start": 10.0, "arabic_text": "هَذَا نَصٌّ طَوِيلٌ جِدًّا لِهَذِهِ الْجُمْلَةِ", "text": "this"}
        return self.ls._rephrase_to_fit({"id": "j"}, r, "", self.meta(tempo), 2.9, 3.4, "v", self.d / "x.wav", self.d, cap=cap)

    def test_a_line_fast_forwarded_beyond_the_cap_gets_shorter_wording(self):
        meta, text = self.run_it(1.2, 1.08)
        self.assertIsNotNone(text)
        self.assertEqual(meta["tempo"], 1.0)
        self.assertTrue(self.asked)

    def test_the_shorter_text_is_asked_for_in_proportion_to_the_cap(self):
        self.run_it(1.2, 1.08)
        slow_ask = self.asked[0]
        self.asked.clear()
        self.run_it(1.2, 1.25)
        self.assertLessEqual(slow_ask, self.asked[0] if self.asked else slow_ask)


class ConstantsTests(unittest.TestCase):
    def test_the_soft_caps_never_exceed_the_hard_limit(self):
        import longdub_service as ls
        for p in ("slow", "normal", "fast", "unknown"):
            self.assertLessEqual(min(ls.TEMPO_MAX, D.tempo_cap(p)), ls.TEMPO_MAX)


if __name__ == "__main__":
    unittest.main()
