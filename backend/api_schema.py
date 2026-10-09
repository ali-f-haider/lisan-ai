"""Bounded JSON validation shared by the proposed public API contract.

No imports from the application or audio engines. Syntax checks only: owner,
revision, media duration, voices, settings and live prices need adapter checks.
"""
import math
import re

ID = {"type": "string", "minLength": 1, "maxLength": 200, "pattern": r"^[A-Za-z0-9_-]+$"}
UUID = {"type": "string", "format": "uuid", "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"}
CREDITS = {"type": "integer", "minimum": 0, "maximum": 2147483647}
VERSION = {"type": "string", "minLength": 1, "maxLength": 80, "pattern": r"^[A-Za-z0-9_.-]+$"}
SEGMENT = {
    "type": "object", "additionalProperties": False,
    "required": ["segment_id", "start", "end", "speaker", "text", "arabic_text"],
    "properties": {
        "segment_id": ID, "start": {"type": "number", "minimum": 0, "maximum": 3601},
        "end": {"type": "number", "exclusiveMinimum": 0, "maximum": 3601},
        "speaker": {"type": "string", "minLength": 1, "maxLength": 80},
        "text": {"type": "string", "maxLength": 15000},
        "arabic_text": {"type": "string", "maxLength": 15000},
        "emotion": {"type": "string", "minLength": 1, "maxLength": 80},
        "waqf": {"type": "string", "enum": ["auto", "stop", "join"], "default": "auto"},
    },
}
SPEAKER = {
    "type": "object", "additionalProperties": False, "required": ["id", "name", "voice_mode"],
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 80},
        "name": {"type": "string", "maxLength": 80},
        "voice_mode": {"type": "string", "enum": ["original", "library", "saved"]},
        "voice_id": ID,
    },
}
BODY_SCHEMAS = {
    "CreateJob": {
        "type": "object", "additionalProperties": False,
        "required": ["kind", "filename", "size_bytes", "rights_confirmed", "terms_version"],
        "properties": {
            "kind": {"type": "string", "enum": ["short", "long"]},
            "filename": {"type": "string", "minLength": 1, "maxLength": 255, "pattern": r"^[^/\\\x00-\x1f\x7f]+$"},
            "size_bytes": {"type": "integer", "minimum": 1, "maximum": 2147483648},
            "name": {"type": "string", "maxLength": 80},
            "description": {"type": "string", "maxLength": 500},
            "stated_speakers": {"type": "integer", "minimum": 1, "maximum": 8, "default": 2},
            "rights_confirmed": {"type": "boolean", "const": True},
            "terms_version": VERSION,
        },
    },
    "QuoteRequest": {
        "type": "object", "additionalProperties": False, "required": ["step"],
        "properties": {
            "step": {"type": "string", "enum": ["estimate", "analysis", "clone", "dub", "merge"]},
            "keep_music": {"type": "boolean", "default": True},
            "tracks": {"type": "boolean", "default": True},
            "speaker_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1, "maxLength": 80}},
        },
    },
    "AcceptQuote": {
        "type": "object", "additionalProperties": False,
        "required": ["quote_id", "quoted_credits", "max_credits", "terms_version"],
        "properties": {"quote_id": UUID, "quoted_credits": CREDITS, "max_credits": CREDITS,
                       "terms_version": VERSION},
    },
    "EditSegments": {
        "type": "object", "additionalProperties": False, "required": ["revision", "segments"],
        "properties": {"revision": {"type": "integer", "minimum": 0, "maximum": 2147483647},
                       "segments": {"type": "array", "maxItems": 6000, "items": SEGMENT}},
    },
    "EditSpeakers": {
        "type": "object", "additionalProperties": False, "required": ["revision", "speakers"],
        "properties": {"revision": {"type": "integer", "minimum": 0, "maximum": 2147483647},
                       "speakers": {"type": "array", "minItems": 1, "maxItems": 8, "items": SPEAKER}},
    },
}


def _check(schema, value, field, budget, depth=0):
    budget[0] -= 1
    if budget[0] < 0 or depth > 20:
        return "body is too complex."
    kind = schema["type"]
    expected = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    good_type = type(value) in (int, float) if kind == "number" else type(value) is expected[kind]
    if not good_type:
        return f"{field} must be {kind}."
    if kind in ("number", "integer"):
        if not math.isfinite(value):
            return f"{field} must be a finite number."
        if "minimum" in schema and value < schema["minimum"]:
            return f"{field} must be at least {schema['minimum']}."
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            return f"{field} must be greater than {schema['exclusiveMinimum']}."
        if "maximum" in schema and value > schema["maximum"]:
            return f"{field} must be at most {schema['maximum']}."
    if "const" in schema and value != schema["const"]:
        return f"{field} must be true."
    if "enum" in schema and value not in schema["enum"]:
        return f"{field} must be one of: {', '.join(schema['enum'])}."
    if kind == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 100000):
            return f"{field} has an invalid length."
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            return f"{field} must contain valid Unicode."
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            return f"{field} has an invalid format."
    if kind == "object":
        props = schema["properties"]
        for required in schema.get("required", []):
            if required not in value:
                return f"{field}.{required} is required."
        if any(type(k) is not str or k not in props for k in value):
            return f"{field} has an unknown field."
        for key in props:
            if key in value:
                problem = _check(props[key], value[key], f"{field}.{key}", budget, depth + 1)
                if problem:
                    return problem
    if kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 6000):
            return f"{field} has an invalid number of items."
        if schema.get("uniqueItems") and len(value) != len({str(x) for x in value}):
            return f"{field} must not contain duplicates."
        for i, item in enumerate(value):
            problem = _check(schema["items"], item, f"{field}[{i}]", budget, depth + 1)
            if problem:
                return problem
    return None


def validate_body(schema_name, body):
    """First plain field problem or None. Never raises on arbitrary input."""
    try:
        if type(schema_name) is not str or schema_name not in BODY_SCHEMAS:
            return "body has an unknown request type."
        problem = _check(BODY_SCHEMAS[schema_name], body, "body", [150000])
        if problem:
            return problem
        if schema_name == "AcceptQuote" and body["max_credits"] < body["quoted_credits"]:
            return "body.max_credits must cover quoted_credits."
        if schema_name == "QuoteRequest":
            if body["step"] == "clone" and not body.get("speaker_ids"):
                return "body.speaker_ids is required for clone."
            if body["step"] != "clone" and "speaker_ids" in body:
                return "body.speaker_ids is only allowed for clone."
        if schema_name == "CreateJob" and body["filename"] in (".", ".."):
            return "body.filename has an invalid format."
        if schema_name == "EditSegments":
            seen = set()
            for i, row in enumerate(body["segments"]):
                if row["end"] <= row["start"]:
                    return f"body.segments[{i}].end must be after start."
                if row["segment_id"] in seen:
                    return f"body.segments[{i}].segment_id must be unique."
                seen.add(row["segment_id"])
        if schema_name == "EditSpeakers":
            seen = set()
            for i, row in enumerate(body["speakers"]):
                if row["id"] in seen:
                    return f"body.speakers[{i}].id must be unique."
                seen.add(row["id"])
                if row["voice_mode"] != "original" and not row.get("voice_id"):
                    return f"body.speakers[{i}].voice_id is required."
        return None
    except Exception:
        return "body is not a valid request."


def validate_chunk(index, data, total_bytes, chunk_bytes):
    """Validate an ALREADY BOUNDED raw chunk; streaming limits belong to adapter."""
    try:
        if type(total_bytes) is not int or total_bytes <= 0 or total_bytes > 2147483648:
            return "upload.size_bytes is invalid."
        if type(chunk_bytes) is not int or chunk_bytes <= 0 or chunk_bytes > 8388608:
            return "upload.chunk_bytes is invalid."
        total_chunks = (total_bytes + chunk_bytes - 1) // chunk_bytes
        if type(index) is not int or not 0 <= index < total_chunks:
            return "index is outside this upload."
        if type(data) is not bytes:
            return "body must be binary audio or video bytes."
        expected = min(chunk_bytes, total_bytes - index * chunk_bytes)
        if len(data) != expected:
            return "body must contain exactly the bytes expected for this chunk."
        return None
    except Exception:
        return "body is not a valid upload chunk."
