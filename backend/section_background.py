"""Free ambience preflight and independent music repairs inside sound sections."""
from pathlib import Path
from contextlib import contextmanager, ExitStack
import subprocess
import tempfile
import wave

import numpy as np

import music_fill
import pause_bed
import ambience_match

RATE = music_fill.RATE


def local_bed(clean, bg, original, vocals, spans, work, tag, scenes=None, log=None):
    path = Path(work) / f'{tag}_ambience.wav'
    if scenes is None:
        scenes = ambience_match.cached_scenes(work, tag, bg)
    info = pause_bed.bed_floor(clean, original, vocals, bg, spans, path, scenes=scenes, log=log)
    return (path if info['ok'] and path.exists() else Path(clean)), info, path


def bounds(sections, frames):
    """Cover each sample exactly once, including unlabelled gaps; reject unsafe values."""
    cuts = {0, frames}
    for section in sections or []:
        try:
            start, end = float(section['start']), float(section['end'])
            if not (np.isfinite(start) and np.isfinite(end) and end > start):
                raise ValueError('Invalid sound section')
            cuts.update(max(0, min(frames, round(value * RATE))) for value in (start, end))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    ordered = sorted(cuts)
    return list(zip(ordered[:-1], ordered[1:]))


def _clip(source, target, a, b):
    # Working beds are PCM WAV: seek directly instead of decoding an hour for each section.
    try:
        with wave.open(str(source), 'rb') as audio:
            if audio.getnchannels() != 2 or audio.getsampwidth() != 2 or audio.getframerate() != RATE or audio.getnframes() < b:
                raise ValueError('Conversion required')
            audio.setpos(a)
            with wave.open(str(target), 'wb') as out:
                out.setparams(audio.getparams())
                left = b - a
                while left:
                    count = min(left, 10 * RATE)
                    out.writeframes(audio.readframes(count))
                    left -= count
        return
    except (wave.Error, ValueError, EOFError):
        pass
    import ffmpeg_utils
    ffmpeg_utils.run_ffmpeg(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', f'{a / RATE:.12f}', '-i', str(source),
        '-af', f'aresample={RATE},apad=whole_len={b-a},atrim=end_sample={b-a},asetpts=PTS-STARTPTS',
        '-ar', str(RATE), '-ac', '2', '-c:a', 'pcm_s16le', str(target)])


def _spans(spans, a, b):
    import speech_removal
    return speech_removal.clipped(spans, a / RATE, b / RATE)


@contextmanager
def _pcm_reference(path, work):
    """Reuse readable references; decode other formats locally, in private scratch space.

    Completed projects store AAC/M4A. The sound-file reader cannot open that
    format on all hosts, whereas FFmpeg already supports it in our pipeline.
    Conversion stays on disk and the caller still reads bounded audio blocks.
    """
    import soundfile as sf
    try:
        info = sf.info(path)
    except (OSError, RuntimeError):
        info = None
    if info is not None and info.samplerate == RATE and info.channels == 2 and info.frames > 0:
        yield path
        return
    with tempfile.TemporaryDirectory(prefix='level_ref_', dir=work) as directory:
        decoded = Path(directory) / 'reference.wav'
        try:
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(path), '-vn',
                '-ar', str(RATE), '-ac', '2', '-c:a', 'pcm_s16le', str(decoded)],
                check=True, timeout=1800, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if sf.info(decoded).frames <= 0:
                raise ValueError('Empty audio reference')
        except (subprocess.SubprocessError, OSError, RuntimeError, ValueError):
            raise ValueError('The background audio could not be read. Please try the export again.') from None
        yield decoded


def level_reference(original, restored, target, spans, gain_db=3.0):
    """Do not level a restored bed back to the separator's zero reference.

    This is a loudness guide only. Where the original stem lost its bed,
    substitute the measured reconstruction; compensate the later speech duck
    once. Existing louder effects and clean pauses keep their original guide.
    """
    import soundfile as sf
    with ExitStack() as stack:
        original_pcm = stack.enter_context(_pcm_reference(original, Path(target).parent))
        restored_pcm = stack.enter_context(_pcm_reference(restored, Path(target).parent))
        source = stack.enter_context(sf.SoundFile(original_pcm))
        filled = stack.enter_context(sf.SoundFile(restored_pcm))
        if any(a.samplerate != RATE or a.channels != 2 for a in (source, filled)) or abs(len(source) - len(filled)) > RATE // 4:
            raise ValueError('Background level references do not match')
        with sf.SoundFile(target, 'w', samplerate=RATE, channels=2, subtype='PCM_16') as out:
            for start in range(0, len(filled), 10 * RATE):
                count = min(10 * RATE, len(filled) - start)
                x = source.read(count, dtype='float32', always_2d=True)
                y = filled.read(count, dtype='float32', always_2d=True)
                if len(x) < count:
                    x = np.pad(x, ((0, count - len(x)), (0, 0)))
                frame = 441
                frames = len(x) // frame
                old = (x[:frames * frame] ** 2).reshape(frames, frame, 2).mean(axis=(1, 2))
                new = (y[:frames * frame] ** 2).reshape(frames, frame, 2).mean(axis=(1, 2))
                missing = np.repeat((old < new / 2) & (new > 1e-12), frame)
                mask = np.zeros(len(x), bool)
                for a, b in spans:
                    lo, hi = max(0, round(a * RATE) - start), min(len(x), round(b * RATE) - start)
                    if hi > lo:
                        mask[lo:hi] = True
                mask[:len(missing)] &= missing
                mask[len(missing):] = False
                x[mask] = y[mask] * np.float32(10 ** (gain_db / 20))
                out.write(x)


def count(source, spans, raw, reference, sections, count_one):
    """Quote with exactly the same boundaries the renderer uses."""
    raw = Path(raw)
    import soundfile as sf
    source_info = sf.info(source)
    frames = round(source_info.frames * RATE / source_info.samplerate)
    total = 0
    try:
        for index, (a, b) in enumerate(bounds(sections, frames)):
            local_spans = _spans(spans, a, b)
            if not local_spans or b - a < 3 * RATE:
                continue
            clip, ref = Path(str(raw) + '.wav'), Path(str(raw) + '.ref.wav')
            _clip(source, clip, a, b)
            if reference:
                _clip(reference, ref, a, b)
            total += count_one(clip, local_spans, raw, original=ref if reference else None)
        if total > music_fill.MAX_GAPS:
            raise ValueError('This file needs more music repairs than the per-project limit.')
        return total
    finally:
        for suffix in ('.wav', '.ref.wav', ''):
            Path(str(raw) + suffix).unlink(missing_ok=True)


def fill(source, target, spans, key, sections=None, **kwargs):
    """No reference, description or repair context crosses a sound boundary."""
    if not sections or len(sections) == 1:
        return music_fill.fill(source, target, spans, key, **kwargs)
    raw = Path(str(target) + '.sections.pcm')
    import soundfile as sf
    source_info = sf.info(source)
    frames = round(source_info.frames * RATE / source_info.samplerate)
    clip, ref, fixed = [Path(str(target) + suffix) for suffix in ('.section.wav', '.reference.wav', '.fixed.wav')]
    part = Path(str(target) + '.part.wav')
    result = {'filled': False, 'reason': '', 'gaps': [], 'found': 0, 'filled_sec': 0.0, 'sent_sec': 0.0}
    reference = kwargs.get('reference')
    charged = kwargs.get('on_filled')
    completed, attempts, elapsed_audio = 0, 0, 0.0
    import time
    started = time.monotonic()
    try:
        with wave.open(str(part), 'wb') as out:
            out.setnchannels(2); out.setsampwidth(2); out.setframerate(RATE)
            for a, b in bounds(sections, frames):
                _clip(source, clip, a, b)
                local = dict(kwargs)
                if reference:
                    _clip(reference, ref, a, b)
                    local['reference'] = ref
                offset = a / RATE
                if charged:
                    local['on_filled'] = lambda s, e, offset=offset: charged(s + offset, e + offset)
                approval = kwargs.get('allow')
                def allow():
                    nonlocal attempts
                    if (attempts >= music_fill.MAX_GAPS or elapsed_audio >= music_fill.MAX_TOTAL_SEC
                            or time.monotonic() - started > music_fill.MAX_WALL_SEC):
                        return False
                    if approval and not approval():
                        return False
                    attempts += 1
                    return True
                local['allow'] = allow
                info = music_fill.fill(clip, fixed, _spans(spans, a, b), key, **local)
                chosen = fixed if info['filled'] and fixed.exists() else clip
                with wave.open(str(chosen), 'rb') as audio:
                    if audio.getnframes() != b - a:
                        raise ValueError('Background section length changed during repair')
                    while True:
                        block = audio.readframes(10 * RATE)
                        if not block:
                            break
                        out.writeframes(block)
                for gap in info.get('gaps', []):
                    result['gaps'].append({**gap, 'start': round(gap['start'] + offset, 3), 'end': round(gap['end'] + offset, 3)})
                for field in ('found', 'filled_sec', 'sent_sec'):
                    result[field] += info.get(field, 0)
                elapsed_audio += info.get('sent_sec', 0)
                completed += bool(info['filled'])
                fixed.unlink(missing_ok=True)
        result['filled'] = bool(completed)
        result['reason'] = f'Background repaired independently in {completed} sound section(s)'
        if result['filled']:
            part.replace(target)
        return result
    finally:
        for path in (raw, clip, ref, fixed, part):
            path.unlink(missing_ok=True)
