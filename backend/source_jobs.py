"""Small durable short-project language records; callers enforce ownership.

Files share the upload retention policy. This module never charges credits or
starts generation. UUID-only paths and atomic replacement avoid unsafe names.
"""
import json
import os
from pathlib import Path
import threading
import uuid

import languages

_LOCK = threading.RLock()


def validate(value):
    if not languages.is_supported(value):
        raise ValueError("Choose a supported original language.")
    return languages.get(value).code


def _path(root, job_id, kind):
    value = str(uuid.UUID(str(job_id)))
    return Path(root) / f"{value}_{kind}.json"


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def language(root, job_id):
    if not job_id:
        return 'en'
    try:
        data = json.loads(_path(root, job_id, 'source_language').read_text(encoding='utf-8'))
        return languages.get(data.get('source_language')).code
    except (OSError, ValueError, TypeError, AttributeError):
        return 'en'


def set_language(root, job_id, value):
    code = validate(value)
    with _LOCK:
        _save(_path(root, job_id, 'source_language'), {'version': 1, 'source_language': code})
    return code


def save_retry(root, job_id, prepared):
    with _LOCK:
        _save(_path(root, job_id, 'language_retry'), prepared)


def retry(root, job_id):
    """Return prepared paths only if they are existing files under uploads."""
    try:
        data = json.loads(_path(root, job_id, 'language_retry').read_text(encoding='utf-8'))
        base = Path(root).resolve()
        for key in ('input_path', 'audio_path', 'background_path'):
            if data.get(key):
                p = Path(data[key]).resolve()
                if not p.is_relative_to(base) or not p.is_file():
                    return None
        if not data.get('input_path') or not data.get('audio_path'):
            return None
        return data
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def clear_retry(root, job_id):
    _path(root, job_id, 'language_retry').unlink(missing_ok=True)
