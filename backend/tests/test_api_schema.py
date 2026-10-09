import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from copy import deepcopy
import unittest

from api_schema import BODY_SCHEMAS, validate_body, validate_chunk


GOOD_BODIES = {
    "CreateJob": {"kind": "short", "filename": "sample.mp4", "size_bytes": 1024,
                  "rights_confirmed": True, "terms_version": "test-version"},
    "QuoteRequest": {"step": "dub"},
    "AcceptQuote": {"quote_id": "00000000-0000-0000-0000-000000000001", "quoted_credits": 5,
                    "max_credits": 10, "terms_version": "test-version"},
    "EditSegments": {"revision": 0, "segments": [{"segment_id": "seg_0", "speaker": "Speaker 1",
                      "start": 0, "end": 1, "text": "Hello.", "arabic_text": "مرحبا."}]},
    "EditSpeakers": {"revision": 0, "speakers": [{"id": "Speaker 1", "name": "One", "voice_mode": "original"}]},
}


class ApiSchemaTests(unittest.TestCase):
    def test_every_body_has_valid_and_invalid_examples_and_is_not_mutated(self):
        self.assertEqual(set(GOOD_BODIES), set(BODY_SCHEMAS))
        for name, body in GOOD_BODIES.items():
            before = deepcopy(body)
            self.assertIsNone(validate_body(name, body), name)
            self.assertEqual(body, before)
            self.assertIsNotNone(validate_body(name, dict(body, unknown="field")), name)
            for required in BODY_SCHEMAS[name]["required"]:
                bad = dict(body)
                del bad[required]
                self.assertIn(required, validate_body(name, bad))

    def test_types_reject_bool_as_number_and_never_raise_on_garbage(self):
        for name in BODY_SCHEMAS:
            for garbage in (None, [], 1, True, "text", {"x": object()}, {1: "x"}):
                self.assertIsInstance(validate_body(name, garbage), str)
        for name in (None, [], {}, "unknown"):
            self.assertIsInstance(validate_body(name, None), str)
        for value in (True, 1.5, float("inf"), 10**1000):
            body = dict(GOOD_BODIES["CreateJob"], size_bytes=value)
            self.assertIsInstance(validate_body("CreateJob", body), str)

    def test_numeric_ranges_allowed_values_and_lengths(self):
        for changes, field in (({"size_bytes": 0}, "size_bytes"), ({"size_bytes": 2147483649}, "size_bytes"),
                               ({"kind": "unknown"}, "kind"), ({"name": "x" * 81}, "name"),
                               ({"stated_speakers": 9}, "stated_speakers"), ({"rights_confirmed": False}, "rights_confirmed")):
            self.assertIn(field, validate_body("CreateJob", dict(GOOD_BODIES["CreateJob"], **changes)))

    def test_filename_paths_and_invalid_unicode_are_rejected(self):
        for filename in ("../x.mp4", "C:\\private.mp4", ".", "..", "x\n.mp4", "\ud800.mp4"):
            self.assertIn("filename", validate_body("CreateJob", dict(GOOD_BODIES["CreateJob"], filename=filename)))

    def test_credit_cap_and_quote_id_are_validated_before_work(self):
        self.assertIn("max_credits", validate_body("AcceptQuote", dict(GOOD_BODIES["AcceptQuote"], max_credits=4)))
        self.assertIn("quote_id", validate_body("AcceptQuote", dict(GOOD_BODIES["AcceptQuote"], quote_id="other")))
        self.assertIn("quoted_credits", validate_body("AcceptQuote", dict(GOOD_BODIES["AcceptQuote"], quoted_credits=-1)))

    def test_clone_selection_is_explicit_and_unique(self):
        self.assertIn("speaker_ids", validate_body("QuoteRequest", {"step": "clone"}))
        self.assertIsNone(validate_body("QuoteRequest", {"step": "clone", "speaker_ids": ["Speaker 1"]}))
        for body in ({"step": "dub", "speaker_ids": ["Speaker 1"]},
                     {"step": "clone", "speaker_ids": ["Speaker 1", "Speaker 1"]}):
            self.assertIn("speaker_ids", validate_body("QuoteRequest", body))

    def test_line_times_ids_and_waqf_preserve_arabic(self):
        body = deepcopy(GOOD_BODIES["EditSegments"])
        row = body["segments"][0]
        for value in (float("nan"), -1, 3602, True):
            row["start"] = value
            self.assertIn("start", validate_body("EditSegments", body))
        row["start"], row["end"] = 1, 1
        self.assertIn("end", validate_body("EditSegments", body))
        row["start"] = 0
        row["waqf"] = "stop"
        before = deepcopy(body)
        self.assertIsNone(validate_body("EditSegments", body))
        self.assertEqual(body, before)
        row["waqf"] = "unknown"
        self.assertIn("waqf", validate_body("EditSegments", body))
        row["waqf"] = "auto"
        body["segments"].append(dict(row))
        self.assertIn("segment_id", validate_body("EditSegments", body))

    def test_speaker_voice_requirements_and_duplicate_ids(self):
        body = deepcopy(GOOD_BODIES["EditSpeakers"])
        body["speakers"][0]["voice_mode"] = "saved"
        self.assertIn("voice_id", validate_body("EditSpeakers", body))
        body["speakers"][0]["voice_id"] = "my_voice"
        self.assertIsNone(validate_body("EditSpeakers", body))
        body["speakers"].append(dict(body["speakers"][0]))
        self.assertIn("id", validate_body("EditSpeakers", body))

    def test_chunk_index_exact_size_last_chunk_and_garbage(self):
        self.assertIsNone(validate_chunk(0, b"abcd", 6, 4))
        self.assertIsNone(validate_chunk(1, b"ef", 6, 4))
        for args in ((0, b"abc", 6, 4), (1, b"efgh", 6, 4), (-1, b"", 6, 4),
                     (2, b"", 6, 4), (True, b"abcd", 6, 4), (0, "abcd", 6, 4),
                     (0, None, None, {}), (0, b"a", 1, 8388609)):
            self.assertIsInstance(validate_chunk(*args), str)
