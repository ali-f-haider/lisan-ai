"""The same residual-speech removal and local level matching for short and long dubbing."""
from pathlib import Path
import json
import os
import wave
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


# Keep the REAL background under the speech (bg_duck's own "keep" mode: the leftover of the voice is
# subtracted and the result is checked against the voice; a stretch that fails the check is silenced, and the repair below then fills it).
# For a changing background (a battle, traffic, a crowd) the real sound is the only thing that sounds right; a rebuilt steady bed does not.
KEEP_UNDER_SPEECH = os.environ.get('DUB_BG_KEEP', '1').strip().lower() in ('1', 'on', 'yes', 'true')
# ...and a little lower than the original while the Arabic speaks (the leftover of a voice in it is masked, the dialogue stays clear)
KEEP_SPEECH_DB = float(os.environ.get('DUB_BG_KEEP_DB', '-3'))


def mute(bg, vocals, destination, spans):
    result = bg_duck.mute_speech(bg, vocals, destination, spans=spans, mode='always', keep=KEEP_UNDER_SPEECH, strict=True)
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
        # A steady sound (engine, wind, room tone ...) in the pauses is rebuilt locally, for free, in holes of any length and
        # even when nothing is left beside them: then these holes are not a reason to give up the background.
        engine_ok = original is not None and music_fill.steady_engine(muted, original, spans)['ok']
        if spans and info['music_sec'] < music_fill.MIN_MUSIC_SEC and original is not None and not engine_ok:
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
        if info['found'] > len(gaps) and not engine_ok:
            raise ValueError('Some silent music sections are too long or lack clean music context for reliable repair.')
        if len(gaps) > music_fill.MAX_GAPS and not engine_ok:
            raise ValueError('This file needs more music repairs than the per-project limit.')
        if engine_ok:
            return 0        # every hole is rebuilt locally from the steady sound: free, so nothing is quoted or charged
        return min(len(gaps), music_fill.MAX_GAPS)
    finally:
        if pcm is not None:
            pcm._mmap.close()
        Path(raw).unlink(missing_ok=True)


PAUSE_PAD_SEC = 0.25      # the original sound is used only this far away from any original speech
PAUSE_FADE_SEC = 0.15     # and is blended in over this long
PAUSE_VOICE_REL_DB = -40.0   # a frame where the separated voices are louder than this under their peak is speech, whatever the map says
_FRAME = 441              # 10 ms at 44.1 kHz


def original_in_pauses(base, original, vocals, spans, out_path, log=None):
    """Writes `out_path` = `base` (a separated background, silenced or repaired under the speech) with the ORIGINAL recording
    laid in wherever nobody speaks. The separator rewrites the background even where there is nothing to remove (a metallic
    "machine" noise, and part of a quiet room tone is taken away); the original sound of a pause is the real thing.
    Only used when the original is the same recording (same length, and voices + background add up to it); otherwise nothing is
    written. Returns {"ok", "reason", "share"}; never raises."""
    info = {'ok': False, 'reason': '', 'share': 0.0}
    out_path = Path(out_path)
    raws = [out_path.with_name(out_path.name + f'.blend{i}.pcm') for i in range(3)]
    pcms = []
    part = out_path.with_name(out_path.name + '.part.wav')
    try:
        if not original or not Path(original).exists() or not spans or not Path(base).exists():
            info['reason'] = 'no original recording to use'
            return info
        sources = [base, original, vocals]
        if vocals is None or not Path(vocals).exists():
            info['reason'] = 'no separated voices to check the original against'
            return info
        for src, raw in zip(sources, raws):
            music_fill._to_pcm(src, raw)
            n_ = Path(raw).stat().st_size // 4
            pcms.append(np.memmap(raw, dtype='<i2', mode='r', shape=(n_, 2)) if n_ else None)
        if any(p is None for p in pcms):
            info['reason'] = 'an audio file is empty'
            return info
        b, o, v = pcms
        n = len(b)
        if abs(len(o) - n) > music_fill.RATE // 4 or abs(len(v) - n) > music_fill.RATE // 4:
            info['reason'] = 'the original recording has another length than the separated background'
            return info
        n = min(n, len(o), len(v))
        # same recording? voices + background must add up to the original (any gain or small shift breaks it)
        mid, half = n // 2, min(n, 40 * music_fill.RATE) // 2
        s0, s1 = max(0, mid - half), min(n, mid + half)
        om = o[s0:s1].astype(np.float32).mean(axis=1)
        sm = b[s0:s1].astype(np.float32).mean(axis=1) + v[s0:s1].astype(np.float32).mean(axis=1)
        if om.std() < 1e-3 or sm.std() < 1e-3 or float(np.corrcoef(om, sm)[0, 1]) < 0.8:
            info['reason'] = 'the original recording does not match the separated sound'
            return info
        # frames (10 ms) that are speech: the speech map plus a margin, and anything the separated voices say
        nf = n // _FRAME
        speech = np.zeros(nf + 1, dtype=bool)
        for a, c in spans:
            speech[max(0, int((a - PAUSE_PAD_SEC) * 100)):min(nf + 1, int((c + PAUSE_PAD_SEC) * 100) + 1)] = True
        vl = np.empty(nf)
        for i in range(0, nf, 6000):
            j = min(nf, i + 6000)
            blk = v[i * _FRAME:j * _FRAME].astype(np.float32) / 32768.0
            vl[i:j] = 10.0 * np.log10((blk ** 2).reshape(j - i, _FRAME, 2).mean(axis=(1, 2)) + 1e-12)
        loud = vl > (float(vl.max()) + PAUSE_VOICE_REL_DB)
        k = int(PAUSE_PAD_SEC * 100)
        loud = np.convolve(loud.astype(np.float32), np.ones(2 * k + 1), mode='same') > 0
        speech[:nf] |= loud
        w = (~speech).astype(np.float32)
        kf = max(1, int(PAUSE_FADE_SEC * 100))
        w = np.minimum(w, np.convolve(w, np.ones(kf) / kf, mode='same'))     # fades never reach into the speech margin
        info['share'] = round(float(w.mean()), 3)
        if info['share'] < 0.02:
            info['reason'] = 'no pauses to use'
            return info
        centers = (np.arange(w.size) + 0.5) * _FRAME
        with wave.open(str(part), 'wb') as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(music_fill.RATE)
            step = 30 * music_fill.RATE
            for a in range(0, n, step):
                c = min(n, a + step)
                wt = np.interp(np.arange(a, c), centers, w)[:, None].astype(np.float32)
                mixed = b[a:c].astype(np.float32) * (1.0 - wt) + o[a:c].astype(np.float32) * wt
                out.writeframes(np.clip(np.rint(mixed), -32768, 32767).astype('<i2').tobytes())
        part.replace(out_path)
        info['ok'] = True
        info['reason'] = f"the original sound is used in the pauses ({info['share'] * 100:.0f}% of the time)"
        return info
    except Exception as ex:
        info['reason'] = f'not used ({str(ex)[:160]})'
        if log:
            log('original in pauses: ' + info['reason'])
        return info
    finally:
        for p in pcms:
            if p is not None:
                try:
                    p._mmap.close()
                except Exception:
                    pass
        for p in raws + [part]:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


def short_holes(muted, spans, raw):
    """Number of holes too short for the AI model that the free local rebuild (a steady engine, wind, room tone) can still close."""
    pcm = None
    try:
        if not music_fill.LOCAL_FILL:
            return 0
        music_fill._to_pcm(muted, raw)
        n = Path(raw).stat().st_size // 4
        if n == 0:
            return 0
        pcm = np.memmap(raw, dtype='<i2', mode='r', shape=(n, 2))
        gaps, info = music_fill.find_gaps(music_fill._levels(pcm), spans, min_len=music_fill.LOCAL_MIN_GAP_SEC)
        return len(info.get('short_gaps', []))
    finally:
        if pcm is not None:
            pcm._mmap.close()
        Path(raw).unlink(missing_ok=True)


def prepare(bg, vocals, dub, work, tag, spans, key='', gemini_key='', allow=None, on_filled=None, preserve_music=True,
            strict=True, progress=None, log=None, original=None):
    work = Path(work)
    muted = work / f'{tag}_muted.wav'
    repaired = work / f'{tag}_repaired.wav'
    matched = work / f'{tag}_matched.wav'
    clean = mute(bg, vocals, muted, spans)
    info = {'filled': False, 'reason': 'Music repair was not requested', 'gaps': []}
    try:
        required = count_repairs(clean, spans, work / f'{tag}_inspect.pcm', original=bg) if clean == muted and preserve_music else 0
        if required and (not key or not music_fill.ENABLED):
            raise RuntimeError('Music inpainting is unavailable. The background cannot be preserved reliably.')
    except (ValueError, RuntimeError) as ex:
        if strict:
            raise
        # Not strict: the music cannot be rebuilt, so the background stays silent while people speak (nothing is charged).
        if log:
            log('music not rebuilt: ' + str(ex))
        required = 0
        info['reason'] = 'The original music could not be rebuilt, so the background is silent while people speak: ' + str(ex)
        info['unavailable'] = True
    # The original recording (when it is the same one) is the real sound in the pauses: it is used there instead of the separated
    # background, and the sound of a steady engine / room tone is learned from it too.
    reference = bg
    bg_ref = bg
    blend = {'ok': False}
    if original is not None and spans:
        blend = original_in_pauses(bg, original, vocals, spans, work / f'{tag}_bgref.wav', log=log)
        if blend['ok']:
            bg_ref = work / f'{tag}_bgref.wav'
            reference = original
        elif log:
            log('original sound not used in the pauses: ' + blend['reason'])
    short = 0
    if key and clean == muted and preserve_music and spans:
        try:
            short = short_holes(clean, spans, work / f'{tag}_short.pcm')
            if not short and music_fill.steady_engine(clean, bg, spans)['ok']:
                short = 1       # nothing for the model, but a steady engine / wind in the pauses can still be laid under the speech
        except Exception as ex:      # only a free extra: never a reason to stop
            if log:
                log('short holes not checked: ' + str(ex))
    if key and clean == muted and (required or short):
        # reference=bg: the sound of a steady engine / wind is learned from the whole track (its pauses), not only from what is left
        # nothing quoted (required == 0): only the free local rebuild may run, the model is never called, so nothing unquoted is charged
        info = music_fill.fill(clean, repaired, spans, key, gemini_key=gemini_key,
                               allow=allow if required else (lambda: False), on_filled=on_filled, progress=progress, log=log,
                               reference=reference, reference_pad=0.4 if reference is not bg else 0.15)
        if info['filled']:
            clean = repaired
        # a short hole the local rebuild could not close is optional (the model is not asked for it): it never makes the repair incomplete
        wanted = [g for g in info['gaps'] if not g.get('optional')]
        incomplete = required and (not info['filled'] or any(not g['ok'] for g in wanted) or
                                   int(info.get('found', 0)) > sum(bool(g['ok']) for g in wanted))
        if incomplete:
            notes = '; '.join(f"{g['start']}-{g['end']} s: {g['note']}" for g in wanted if not g['ok'])
            if log:
                log('music repair incomplete: ' + info['reason'] + (' | ' + notes if notes else ''))
            if strict:
                raise RuntimeError('Music repair could not finish all silent music sections. ' + info['reason'])
            # Not strict: a hole that could not be rebuilt stays silent (it was muted), the repaired ones are kept.
            info['incomplete'] = True
    temps = [muted, repaired, matched]
    if blend['ok']:
        temps.append(bg_ref)
        final = work / f'{tag}_final_bg.wav'
        done = original_in_pauses(clean, original, vocals, spans, final, log=log)
        if done['ok']:
            clean = final
            info['original_in_pauses'] = done['reason']
            temps.append(final)
    # Levels: the final level matching restores the original loudness, so "lower" has to be told to it: what was rebuilt locally
    # (an engine, wind, a room tone) is laid LOCAL_FILL_DB under it, the real background kept under the speech KEEP_SPEECH_DB.
    lower = []
    if KEEP_UNDER_SPEECH and KEEP_SPEECH_DB:
        lower += [(a, b, KEEP_SPEECH_DB) for a, b in spans]
    lower += [(g['start'], g['end'], music_fill.LOCAL_FILL_DB) for g in (info.get('gaps') or []) if g.get('ok') and g.get('local')]
    measurements = dub_audio.match_background(bg_ref, clean, matched, vocals, dub, spans, lower=lower)
    return {'path': matched, 'music_fill': info, 'measurements': measurements, 'temps': temps}


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
