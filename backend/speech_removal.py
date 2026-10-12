"""Explicit source-speech regions, independent of dubbed line placement."""
from pathlib import Path
import hashlib
import json
import math
import os
import uuid

MAX_REGIONS = 256
FILE_NAME = 'speech_removals.json'


def validate(ranges, duration=None):
    if not isinstance(ranges, list) or len(ranges) > MAX_REGIONS:
        raise ValueError('Choose at most 256 speech sections.')
    if duration is not None and (not math.isfinite(float(duration)) or float(duration) <= 0):
        raise ValueError('The original file duration is unavailable. Attach the original file.')
    output = []
    for pair in ranges:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(isinstance(x, bool) for x in pair):
            raise ValueError('Choose valid start and end times for each speech section.')
        try:
            a, b = (float(x) for x in pair)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('Choose valid start and end times for each speech section.') from None
        if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a:
            raise ValueError('The end must be after the start, inside the original file.')
        if duration is not None:
            if b > float(duration) + 0.01:
                raise ValueError('Choose times inside the original file.')
            b = min(b, float(duration))
        a, b = round(a, 3), round(b, 3)
        if b <= a:
            raise ValueError('Choose a speech section at least one millisecond long.')
        if [a, b] not in output:
            output.append([a, b])
    return sorted(output)


def revision(ranges):
    return hashlib.sha256(json.dumps(validate(ranges), separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path):
    path = Path(path)
    if not path.exists():
        return []
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get('version') != 1:
            raise ValueError('Invalid saved speech-removal record')
        return validate(record.get('ranges'))
    except (OSError, TypeError, ValueError):
        raise ValueError('Saved speech sections could not be read. Reopen the project and try again.') from None


def write(path, ranges, duration, expected):
    """Caller holds the job lock; a stale editor cannot replace another save."""
    path = Path(path)
    current = read(path)
    if expected != revision(current):
        raise ValueError('Saved speech sections changed. Reopen the player and try again.')
    values = validate(ranges, duration)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps({'version': 1, 'ranges': values}, allow_nan=False), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {'ranges': values, 'revision': revision(values)}


class SpeechSpans(list):
    """Normal source-speech spans plus explicitly confirmed, mandatory regions.

    List compatibility keeps existing timing consumers and JSON maps unchanged.
    Only cleanup/reference-selection code needs the explicit `forced` regions.
    """
    def __init__(self, values=(), forced=(), reference=None):
        super().__init__(values)
        self.forced = list(forced)
        self.reference = list(reference) if reference is not None else None


def forced(spans):
    return getattr(spans, 'forced', ())


def clipped(spans, start, end):
    def cut(values):
        return [(max(0.0, a - start), min(end - start, b - start))
                for a, b in values if a < end and b > start]
    reference = getattr(spans, 'reference', None)
    return SpeechSpans(cut(spans), cut(forced(spans)), cut(reference) if reference is not None else None)


def signature(spans):
    values = {'spans': list(spans), 'forced': list(forced(spans)), 'reference': getattr(spans, 'reference', None)}
    return hashlib.sha256(json.dumps(values, sort_keys=True, allow_nan=False).encode()).hexdigest()


def asset_signature(path):
    if path is None:
        return ''
    path = Path(path)
    stat = path.stat()
    value = [path.name, stat.st_size, stat.st_mtime_ns]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def splice_background(base, cleaned, ranges, destination):
    """Sample-accurate local replacement; bounded buffers even for hour-long beds."""
    import subprocess
    import numpy as np
    rate, channels, block = 44100, 2, 44100
    frame_bytes = channels * 4
    destination = Path(destination)
    temporary = destination.with_name(destination.name + '.part.wav')
    decoders, encoder = [], None
    try:
        for path in (base, cleaned):
            decoders.append(subprocess.Popen(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path), '-vn',
                '-ac', str(channels), '-ar', str(rate), '-f', 'f32le', '-'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL))
        encoder = subprocess.Popen(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'f32le', '-ar', str(rate),
            '-ac', str(channels), '-i', '-', '-c:a', 'pcm_s16le', str(temporary)], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        position = 0
        while True:
            original = decoders[0].stdout.read(block * frame_bytes)
            if not original:
                break
            if len(original) % frame_bytes:
                raise RuntimeError('The background audio could not be read.')
            replacement = decoders[1].stdout.read(len(original))
            tolerance = round(rate * 0.05) * frame_bytes
            missing = len(original) - len(replacement)
            if missing % frame_bytes or missing > tolerance:
                raise RuntimeError('The rebuilt background has a different duration.')
            if missing:
                replacement += bytes(missing)  # frame/codec rounding at EOF; selected speech remains suppressed
            old = np.frombuffer(original, dtype='<f4').reshape(-1, channels)
            new = np.frombuffer(replacement, dtype='<f4').reshape(-1, channels)
            times = (np.arange(len(old), dtype=np.float64) + position) / rate
            weight = np.zeros(len(old), dtype=np.float32)
            # Same speech margins as cleanup; fades sit outside the protected interval.
            for a, b in ranges:
                a, b = max(0.0, a - 0.10), b + 0.30
                local = np.minimum((times - (a - 0.08)) / 0.08, ((b + 0.08) - times) / 0.08)
                weight = np.maximum(weight, np.clip(local, 0.0, 1.0))
            mixed = old * (1.0 - weight[:, None]) + new * weight[:, None]
            encoder.stdin.write(mixed.astype('<f4').tobytes())
            position += len(old)
        if len(decoders[1].stdout.read(round(rate * 0.05) * frame_bytes + frame_bytes)) > round(rate * 0.05) * frame_bytes:
            raise RuntimeError('The rebuilt background has a different duration.')
        encoder.stdin.close()
        for decoder in decoders:
            decoder.stdout.close()
            decoder.wait(timeout=30)
        encoder.wait(timeout=30)
        if not position or any(p.returncode for p in decoders) or encoder.returncode:
            raise RuntimeError('The cleaned background could not be saved.')
        os.replace(temporary, destination)
        return destination
    finally:
        for process in decoders + ([encoder] if encoder else []):
            if process.poll() is None:
                process.kill()
            process.wait()
        temporary.unlink(missing_ok=True)
