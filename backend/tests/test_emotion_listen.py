import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

import emotion_listen as E


def rows(n=3):
    return [{"segment_id": f"s{i}", "start": i * 4.0 + 1, "end": i * 4.0 + 3,
             "speaker": "A", "text": "Please bring the empty container here", "emotion": "sad, slowly"} for i in range(n)]


def ids(payload):
    result = []
    for part in payload["contents"][0]["parts"]:
        if part.get("text", "").startswith("{"):
            result.append(json.loads(part["text"])["segment_id"])
    return result


def response(items):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(items)}]}}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 25, "thoughtsTokenCount": 10}}


def answer(sid, emotion="anxious", speed="normal", confidence="high"):
    return {"segment_id": sid, "emotion": emotion, "heard_speed": speed, "confidence": confidence}


class MergeTests(unittest.TestCase):
    def evidence(self, emotion="anxious", speed="normal", confidence="high"):
        return {**answer("s", emotion, speed, confidence), "listened": True}

    def test_high_and_medium_replace_text_emotion(self):
        for confidence in ("high", "medium"):
            self.assertEqual(E.merge("sad, slowly", self.evidence(confidence=confidence), "slow"), "anxious")

    def test_low_or_failed_listening_strips_every_pacing_guess(self):
        text = "sad, slowly, drawn out, rushed, very fast, hesitant, stammering"
        for heard in (None, {}, self.evidence(confidence="low"), {"emotion": "happy", "confidence": "high"}):
            self.assertEqual(E.merge(text, heard, "slow"), "sad")
        self.assertEqual(E.merge("rushed, hesitant", None, "fast"), "neutral")

    def test_speed_requires_high_confidence_and_matching_measurement(self):
        for heard, measured, expected in (("fast", "fast", "sad, rushed"),
                                         ("slow", "fast", "sad"), ("fast", "unknown", "sad"), ("normal", "normal", "sad")):
            self.assertEqual(E.merge("sad", self.evidence("sad", heard), measured), expected)
        self.assertEqual(E.merge("sad", self.evidence("sad", "slow"), "slow"), "sad")        # automatic slow tags are switched off
        with patch.object(E.delivery, "AUTO_SLOW_ALLOWED", True):
            self.assertEqual(E.merge("sad", self.evidence("sad", "slow"), "slow"), "sad, slowly")
        self.assertEqual(E.merge("sad", self.evidence("sad", "fast", "medium"), "fast"), "sad")

    def test_coherence_wins_even_over_high_confidence_slow(self):
        self.assertEqual(E.merge("sad", self.evidence("fearful", "slow"), "slow"), "fearful")

    def test_normalizer_is_used_without_its_substring_guessing(self):
        for phrase in ("not slowly", "unrushed", "quick-witted", "slowdown", "no hesitant delivery"):
            self.assertEqual(E.merge(phrase, None, "unknown"), "neutral")
        with patch.object(E.gemini_service, "normalize_emotions", wraps=E.gemini_service.normalize_emotions) as normalizer:
            self.assertEqual(E.merge("calm, slow", None, "unknown"), "confident")
            self.assertTrue(normalizer.called)

    def test_merge_is_pure_and_handles_malformed_values(self):
        heard = self.evidence()
        saved = copy.deepcopy(heard)
        E.merge("sad", heard, "slow")
        self.assertEqual(heard, saved)
        for value in (None, [], {}, 42):
            self.assertEqual(E.merge(value, value, value), "neutral")


class ListenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "voices.wav"
        with wave.open(str(self.source), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            for _ in range(40):
                wav.writeframesraw(b"\0" * 160000)    # 400 s without a whole-file allocation
        self.calls, self.records, self.cuts = [], [], []

    def tearDown(self):
        self.tmp.cleanup()

    def cut(self, source, start, duration, target):
        self.cuts.append((source, start, duration))
        target.write_bytes(b"compressed-audio")

    def call(self, key, payload, timeout):
        self.calls.append((payload, timeout))
        return response([answer(sid) for sid in ids(payload)]), None

    def run_listen(self, data=None, **kw):
        options = {"cache_dir": self.root / "cache", "call": self.call, "cut": self.cut,
                   "record": lambda *args: self.records.append(args)}
        options.update(kw)
        return E.listen("job", rows() if data is None else data, self.source, "fake-key", **options)

    def test_every_request_is_recorded_and_redo_is_free(self):
        first = self.run_listen()
        self.assertEqual(set(first), {"s0", "s1", "s2"})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.records[0][1]["usageMetadata"]["thoughtsTokenCount"], 10)
        self.assertEqual(self.run_listen(), first)
        self.assertEqual(len(self.calls), 1)

    def test_public_helpers_are_the_production_defaults(self):
        with patch.object(E.gemini_service, "call_gemini", side_effect=self.call), \
             patch.object(E.gemini_service, "record_gemini", side_effect=lambda *a: self.records.append(a)), \
             patch.object(E, "_cut", side_effect=self.cut):
            out = E.listen("job", rows(), self.source, "fake-key", cache_dir=self.root / "cache")
        self.assertEqual(len(out), 3)
        self.assertEqual(len(self.records), 1)

    def test_manual_choices_are_skipped_and_input_is_unchanged(self):
        data = rows()
        data[1].update(emotion_set=True, emotion="angry, very fast")
        original = copy.deepcopy(data)
        out = self.run_listen(data)
        self.assertEqual(set(out), {"s0", "s2"})
        self.assertEqual(data, original)
        self.assertNotIn("s1", ids(self.calls[0][0]))

    def test_selected_line_keeps_three_neighbours_and_scene_context(self):
        data = rows(9)
        out = self.run_listen(data, selected_ids={"s4"}, scene_hint="A conversation near the door")
        self.assertEqual(set(out), {"s4"})
        parts = self.calls[0][0]["contents"][0]["parts"]
        context = json.loads(parts[1]["text"])
        self.assertEqual(len(context["previous"]), 3)
        self.assertEqual(len(context["next"]), 3)
        self.assertEqual(context["speaker"], "A")
        self.assertIn("A conversation near the door", parts[0]["text"])

    def test_short_neighbours_are_kept_as_text_context(self):
        data = rows(5)
        data[1].update(end=data[1]["start"] + 0.2, text="No!")
        self.run_listen(data, selected_ids={"s2"})
        context = json.loads(self.calls[0][0]["contents"][0]["parts"][1]["text"])
        self.assertEqual(context["previous"][-1]["text"], "No!")

    def test_padding_and_maximum_do_not_cut_a_line(self):
        data = rows(3)
        data[0].update(start=0, end=0.3)
        data[1].update(start=10, end=40)
        data[2].update(start=50, end=80.1)
        out = self.run_listen(data)
        self.assertEqual(set(out), {"s0", "s1"})
        self.assertAlmostEqual(self.cuts[0][1], 0)
        self.assertAlmostEqual(self.cuts[0][2], 0.45)
        self.assertAlmostEqual(self.cuts[1][1], 10)
        self.assertAlmostEqual(self.cuts[1][2], 30)

    def test_short_invalid_or_out_of_source_lines_are_skipped(self):
        data = rows(6)
        for r, pair in zip(data, ((0, .29), (-1, 2), (1, float("nan")), (2, 1), (399, 401), (float("inf"), 3))):
            r.update(start=pair[0], end=pair[1])
        self.assertEqual(self.run_listen(data), {})
        self.assertEqual(self.calls, [])

    def test_batches_are_bounded_by_line_count_and_audio_duration(self):
        data = rows(25)
        self.assertEqual(len(self.run_listen(data)), 25)
        self.assertEqual([len(ids(p)) for p, _ in self.calls], [10, 10, 5])
        self.calls.clear()
        data = [{**r, "start": i * 31.0, "end": i * 31.0 + 29.8} for i, r in enumerate(rows(7))]
        self.assertEqual(len(self.run_listen(data)), 7)
        self.assertEqual([len(ids(p)) for p, _ in self.calls], [3, 3, 1])

    def test_silent_turn_boundary_is_preferred_without_splitting_a_line(self):
        data = rows(12)
        for r in data[6:]:
            r["speaker"] = "B"
        self.run_listen(data)
        self.assertEqual([len(ids(p)) for p, _ in self.calls], [6, 6])

    def test_overlap_group_that_exceeds_a_batch_is_not_cut(self):
        data = rows(11)
        for r in data:
            r.update(start=1, end=3)
        self.assertEqual(self.run_listen(data), {})
        self.assertEqual(self.calls, [])

    def test_partial_json_retries_only_missing_ids_once(self):
        sent = []
        def call(key, payload, timeout):
            wanted = ids(payload)
            sent.append(wanted)
            return response([answer(wanted[0])]), None
        out = self.run_listen(call=call)
        self.assertEqual(sent, [["s0", "s1", "s2"], ["s1", "s2"]])
        self.assertEqual(set(out), {"s0", "s1"})
        self.assertEqual(len(self.records), 2)
        self.run_listen(call=call)
        self.assertEqual(len(sent), 2)           # unjudged receipt also prevents a paid redo

    def test_invalid_json_retries_once_but_transport_failure_does_not(self):
        for job, data in (("invalid", {"candidates": []}), ("failed", None)):
            calls = []
            def call(key, payload, timeout):
                calls.append(payload)
                return data, "raw error with credentials"
            out = E.listen(job, rows(), self.source, "fake-key", cache_dir=self.root / "cache", cut=self.cut,
                           call=call, record=lambda *args: self.records.append(args))
            self.assertEqual(out, {})
            self.assertEqual(len(calls), 2 if data is not None else 1)
        self.assertEqual(len(self.records), 3)

    def test_exception_still_records_the_attempt_and_is_not_exposed(self):
        def call(*args, **kw):
            raise TimeoutError("secret service message")
        self.assertEqual(self.run_listen(call=call), {})
        self.assertEqual(self.records, [("job", None)])

    def test_unknown_and_duplicate_ids_do_not_steal_a_line(self):
        out = E._answers(response([answer("unknown"), answer("s0"), answer("s0"), answer("s1")]), {"s0", "s1"})
        self.assertEqual(set(out), {"s1"})
        duplicate = {"segment_id": "s0", "emotion": "sad", "confidence": "bad"}
        self.assertEqual(E._answers(response([duplicate, answer("s0")]), {"s0"}), {})

    def test_response_schema_and_pacing_are_normalized_safely(self):
        data = response([answer("a", "sad, drawn-out", None), answer("b", "very fast", None),
                         answer("c", "angry, slow", "fast"), answer("d", "unrushed", "fast"),
                         answer("e", "sad", "fast", "certain")])
        out = E._answers(data, set("abcde"))
        self.assertEqual(set(out), set("abc"))
        self.assertEqual(out["a"]["emotion"], "sad")
        self.assertEqual(out["a"]["heard_speed"], "slow")
        self.assertEqual(out["b"]["emotion"], "neutral")
        self.assertEqual(out["b"]["heard_speed"], "fast")
        self.assertIsNone(out["c"]["heard_speed"])
        self.assertTrue(all(len(r["reason"]) <= 120 for r in out.values()))

    def test_thought_text_and_raw_reason_are_not_logged(self):
        data = response([{**answer("s0"), "reason": "A raw model name and a secret"}])
        data["candidates"][0]["content"]["parts"].insert(0, {"thought": True, "text": "private reasoning"})
        out = E._answers(data, {"s0"})
        self.assertNotIn("secret", out["s0"]["reason"])
        self.assertNotIn("model", out["s0"]["reason"])

    def test_changed_text_timing_speaker_scene_or_audio_invalidates_cache(self):
        data = rows(1)
        self.run_listen(data)
        variants = ({**data[0], "text": "A different sentence"}, {**data[0], "start": 1.1}, {**data[0], "speaker": "B"})
        for variant in variants:
            self.run_listen([variant])
        self.run_listen(data, scene_hint="Another context")
        with self.source.open("r+b") as source:
            source.seek(-2, 2)
            source.write(b"\x01\x00")          # same size, different audio
        self.run_listen(data)
        self.assertEqual(len(self.calls), 6)

    def test_pending_receipt_blocks_duplicate_payment_after_interruption(self):
        self.run_listen(rows(1))
        receipt = next((self.root / "cache").rglob("*.json"))
        receipt.write_text('{"state":"pending"}', encoding="utf-8")
        self.assertEqual(self.run_listen(rows(1)), {})
        self.assertEqual(len(self.calls), 1)

    def test_parallel_invocations_share_receipts(self):
        entered, release = threading.Event(), threading.Event()
        outputs = []
        def call(key, payload, timeout):
            entered.set()
            self.assertTrue(release.wait(5))
            return response([answer(sid) for sid in ids(payload)]), None
        thread = threading.Thread(target=lambda: outputs.append(self.run_listen(rows(1), call=call)))
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.run_listen(rows(1)), {})
            self.assertEqual(self.calls, [])
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(set(outputs[0]), {"s0"})

    def test_budget_stops_new_batches_but_finishes_and_records_inflight(self):
        now = [0.0]
        def call(key, payload, timeout):
            self.calls.append((payload, timeout))
            now[0] = 241
            return response([answer(sid) for sid in ids(payload)]), None
        out = self.run_listen(rows(21), clock=lambda: now[0], call=call)
        self.assertEqual(len(out), 10)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.records), 1)
        self.assertEqual(self.calls[0][1], 45)
        self.assertEqual(len(self.run_listen(rows(21))), 21)   # later run only does the remaining batches
        self.assertEqual(len(self.calls), 3)

    def test_budget_prevents_partial_retry(self):
        now = [0.0]
        def call(key, payload, timeout):
            self.calls.append((payload, timeout))
            now[0] = 241
            return response([answer("s0")]), None
        self.assertEqual(set(self.run_listen(clock=lambda: now[0], call=call)), {"s0"})
        self.assertEqual(len(self.calls), 1)

    def test_budget_expiring_during_free_cut_does_not_create_a_paid_receipt(self):
        now = [0.0]
        def cut(*args):
            self.cut(*args)
            now[0] = 241
        self.assertEqual(self.run_listen(rows(1), cut=cut, clock=lambda: now[0]), {})
        self.assertEqual(self.calls, [])
        self.assertEqual(set(self.run_listen(rows(1))), {"s0"})

    def test_parallel_workers_remain_bounded(self):
        lock = threading.Lock()
        active, peak = [0], [0]
        barrier = threading.Barrier(2)
        def call(key, payload, timeout):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            barrier.wait(5)
            with lock:
                active[0] -= 1
            return response([answer(sid) for sid in ids(payload)]), None
        self.assertEqual(len(self.run_listen(rows(20), max_workers=2, call=call)), 20)
        self.assertEqual(peak[0], 2)

    def test_cut_failure_and_oversized_audio_never_start_paid_work(self):
        for size in (0, E._MAX_BYTES + 1):
            self.assertEqual(self.run_listen(cut=lambda s, a, d, p: p.write_bytes(b"x" * size)), {})
        self.assertEqual(self.calls, [])

    def test_input_audio_is_streamed_not_read_as_one_large_bytes_object(self):
        original = Path.read_bytes
        def read(path):
            self.assertNotEqual(path, self.source)
            return original(path)
        with patch.object(Path, "read_bytes", read):
            self.assertEqual(len(self.run_listen()), 3)

    def test_no_key_bad_audio_or_unwritable_cache_never_calls(self):
        self.assertEqual(E.listen("j", rows(), self.source, "", call=self.call), {})
        self.assertEqual(self.run_listen(cache_dir=self.source), {})
        self.source.write_bytes(b"broken audio")
        self.assertEqual(self.run_listen(), {})
        self.assertEqual(self.calls, [])

    def test_default_encoder_uses_mono_compressed_audio_at_32kbps(self):
        with patch.object(E, "run_ffmpeg") as run:
            E._cut(self.source, 1.2, 3.4, self.root / "out.mp3")
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("-ac") + 1], "1")
        self.assertEqual(args[args.index("-b:a") + 1], "32k")
        self.assertEqual(args[args.index("-ar") + 1], "16000")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "local audio tools unavailable")
    def test_real_local_encoder_makes_small_mono_mp3(self):
        target = self.root / "real.mp3"
        E._cut(self.source, 1, 6, target)
        stream = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(target)],
                                                   timeout=10))["streams"][0]
        self.assertEqual(stream["codec_name"], "mp3")
        self.assertEqual(stream["channels"], 1)
        self.assertEqual(int(stream["sample_rate"]), 16000)
        self.assertEqual(int(stream["bit_rate"]), 32000)
        self.assertLess(target.stat().st_size, 26000)


class SummaryTests(unittest.TestCase):
    def test_zero_changes_still_has_a_complete_summary(self):
        data = rows()
        data[0]["emotion_set"] = True
        heard = {"s1": {"listened": True, "heard_speed": "normal", "reason": "Raw private text"}}
        text = E.summary(data, heard, {"s2"})
        self.assertIn("listened 1, fallback 1, skipped 1", text)
        self.assertIn("sad: 3", text)
        self.assertNotIn("Raw private text", text)

    def test_long_summary_counts_speed_and_explains_omissions(self):
        data = rows(100)
        for r in data:
            r["emotion"] = "fearful, rushed"
        heard = {r["segment_id"]: {"listened": True} for r in data}
        text = E.summary(data, heard, set())
        self.assertLessEqual(len(text), 600)
        self.assertIn("speed-tag lines 100", text)
        self.assertIn("s0 (listening and timing agree)", text)
        self.assertRegex(text, r"…and \d+ more$")
        self.assertIn("fearful: 100", text)

    def test_many_tag_types_are_truncated_at_complete_entries(self):
        data = rows(len(E.CANONICAL_EMOTIONS))
        for row, tag in zip(data, E.CANONICAL_EMOTIONS):
            row["emotion"] = tag
        text = E.summary(data, {}, {r["segment_id"] for r in data})
        self.assertLessEqual(len(text), 600)
        self.assertRegex(text, r"…and \d+ more tags")
