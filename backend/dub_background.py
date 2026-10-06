"""The same residual-speech removal and local level matching for short and long dubbing."""
from pathlib import Path
import json
import numpy as np
import bg_duck
import dub_audio
import music_fill


def speech_spans(map_path, rows=()):
    spans = []
    if map_path and Path(map_path).exists():
        spans = [(float(a), float(b)) for a, b in json.loads(Path(map_path).read_text(encoding='utf-8'))]
    # Protect every original line, including gaps inside phrases and manually repaired ASR omissions.
    spans += [(float(r['start']), float(r['end'])) for r in rows if r.get('text')]
    return spans


def mute(bg, vocals, destination, spans):
    result = bg_duck.mute_speech(bg, vocals, destination, spans=spans, mode='always', keep=False, strict=True)
    if not result['muted']:
        # No speech means no residual spoken words to remove. A technical failure must not restore a leaky layer.
        if result['reason'] == 'no speech found in the original':
            return Path(bg)
        raise RuntimeError('Background speech removal failed: ' + result['reason'])
    return Path(destination)


def count_repairs(muted, spans, raw, original=None):
    pcm = None
    try:
        music_fill._to_pcm(muted, raw)
        n = Path(raw).stat().st_size // 4
        if n == 0:
            return 0
        pcm = np.memmap(raw, dtype='<i2', mode='r', shape=(n, 2))
        gaps, info = music_fill.find_gaps(music_fill._levels(pcm), spans)
        if spans and info['music_sec'] < music_fill.MIN_MUSIC_SEC and original is not None:
            check = Path(str(raw) + '.original')
            source = None
            try:
                music_fill._to_pcm(original, check)
                frames = check.stat().st_size // 4
                if frames:
                    source = np.memmap(check, dtype='<i2', mode='r', shape=(frames, 2))
                    if np.any(music_fill._levels(source) >= music_fill.REF_FLOOR_DB):
                        raise ValueError('Removing speech leaves too little clean background to restore reliably. Music-preserving export is unavailable for this file.')
            finally:
                if source is not None:
                    source._mmap.close()
                check.unlink(missing_ok=True)
        if info['found'] > len(gaps):
            raise ValueError('Some silent music sections are too long or lack clean music context for reliable repair.')
        if len(gaps) > music_fill.MAX_GAPS:
            raise ValueError('This file needs more music repairs than the per-project limit.')
        return min(len(gaps), music_fill.MAX_GAPS)
    finally:
        if pcm is not None:
            pcm._mmap.close()
        Path(raw).unlink(missing_ok=True)


def prepare(bg, vocals, dub, work, tag, spans, key='', gemini_key='', allow=None, on_filled=None, preserve_music=True,
            strict=True, progress=None, log=None):
    work = Path(work)
    muted = work / f'{tag}_muted.wav'
    repaired = work / f'{tag}_repaired.wav'
    matched = work / f'{tag}_matched.wav'
    clean = mute(bg, vocals, muted, spans)
    info = {'filled': False, 'reason': 'Music repair was not requested', 'gaps': []}
    required = count_repairs(clean, spans, work / f'{tag}_inspect.pcm', original=bg) if clean == muted and preserve_music else 0
    if required and (not key or not music_fill.ENABLED):
        raise RuntimeError('Music inpainting is unavailable. The background cannot be preserved reliably.')
    if key and clean == muted and required:
        info = music_fill.fill(clean, repaired, spans, key, gemini_key=gemini_key,
                               allow=allow, on_filled=on_filled, progress=progress, log=log)
        if info['filled']:
            clean = repaired
        incomplete = required and (not info['filled'] or any(not g['ok'] for g in info['gaps']) or
                                   int(info.get('found', 0)) > sum(bool(g['ok']) for g in info['gaps']))
        if incomplete:
            notes = '; '.join(f"{g['start']}-{g['end']} s: {g['note']}" for g in info['gaps'] if not g['ok'])
            if log:
                log('music repair incomplete: ' + info['reason'] + (' | ' + notes if notes else ''))
            if strict:
                raise RuntimeError('Music repair could not finish all silent music sections. ' + info['reason'])
            # Not strict: a hole that could not be rebuilt stays silent (it was muted), the repaired ones are kept.
            info['incomplete'] = True
    measurements = dub_audio.match_background(bg, clean, matched, vocals, dub, spans)
    return {'path': matched, 'music_fill': info, 'measurements': measurements,
            'temps': [muted, repaired, matched]}


def checkpointed_prepare(job, save, expected_repairs, *args, **kwargs):
    """Reuse a completed bed; never re-submit an uncertain paid repair after restart."""
    work, tag = Path(args[3]), args[4]
    path = work / f'{tag}_matched.wav'
    record = job.setdefault('background_checkpoint', {}).get(tag) or {}
    if record.get('ready') and path.exists():
        return {'path': path, 'music_fill': record['music_fill'],
                'measurements': record['measurements'], 'temps': [path]}
    if (record.get('started') and expected_repairs) or job.get('paid', {}).get('music_fill', 0):
        raise RuntimeError('Music repair was interrupted before its completed checkpoint. The job cannot safely repeat paid repairs; generation has stopped for refund/review.')
    job['background_checkpoint'][tag] = {'started': True}
    save(job)  # persist before any provider request
    result = prepare(*args, **kwargs)
    job['background_checkpoint'][tag] = {'started': True, 'ready': True,
        'music_fill': result['music_fill'], 'measurements': result['measurements']}
    save(job)
    return result
