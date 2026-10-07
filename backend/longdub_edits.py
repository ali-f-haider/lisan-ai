"""Repeatable, selected-line corrections. Originals are immutable; each export is a new owned job."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time
import uuid
import longdub_service as ld
import dub_review
from shortdub_billing import debit_confirmed


def active(parent):
    return any(j.get('edit_of') == parent['id'] and j.get('status') in ('payment_pending', 'confirmed', 'dubbing')
               for j in ld.list_jobs_for_uid(parent['uid']))


def rows(parent):
    # An empty draft is an intentional deletion of its lines, not a request to restore the original.
    return copy.deepcopy(parent['correction_draft'] if 'correction_draft' in parent else ld.read_segments(parent))


def _editable(parent):
    if parent.get('status') != 'done' or active(parent):
        raise ValueError('Wait for this project to finish before editing.')


def _save_rows(parent, current):
    parent['correction_draft'] = sorted(current, key=lambda r: r['start'])
    ld._save(parent)


def line_operation(parent, operation, segment_id, position=-1):
    """Use the original editor's split/translation/diacritics rules on the separate correction draft."""
    with ld._lock_for(parent['id']):
        _editable(parent)
        current = rows(parent)
        index = next((i for i, r in enumerate(current) if r['segment_id'] == segment_id), None)
        if index is None:
            raise ValueError('Line not found in this project.')
        row = current[index]
        new_id = None
        if operation == 'delete':
            del current[index]
        elif operation == 'insert':
            if len(current) >= ld.MAX_SEGMENTS:
                raise ValueError('The project has reached its line limit.')
            start = round(float(row['end']), 3)
            later = [r['start'] for r in current if r['start'] >= start and r['segment_id'] != segment_id]
            end = round(min(start + ld.NEW_LINE_SEC, min(later) if later else ld._total_secs(parent)), 3)
            if end - start < ld.MIN_LINE_SEC:
                raise ValueError('Make room after this line before inserting a new one.')
            new_id = 'edit_' + uuid.uuid4().hex
            current.insert(index + 1, dict(segment_id=new_id, start=start, end=end, text='', arabic_text='',
                speaker_id=row['speaker_id'], speaker=row.get('speaker'), gender=row.get('gender', 'male'),
                emotion='neutral', words=[], added=True, manual_time=True))
        elif operation == 'split':
            if len(current) >= ld.MAX_SEGMENTS:
                raise ValueError('The project has reached its line limit.')
            plan, error = ld._split_plan(row, position)
            if error:
                raise ValueError(error)
            new_id = 'edit_' + uuid.uuid4().hex
            first, second = copy.deepcopy(row), copy.deepcopy(row)
            first.update(text=plan['left'], words=plan['w_left'], end=plan['t1'], arabic_text='', manual_time=True)
            second.update(segment_id=new_id, text=plan['right'], words=plan['w_right'], start=plan['t2'],
                          arabic_text='', added=True, manual_time=True)
            translated = ld._translate_batch(parent['id'], [first, second], parent.get('glossary'))
            if not all(translated.get(r['segment_id'], ('',))[0] for r in (first, second)):
                raise ValueError('Translation did not finish. The original line was kept.')
            for part in (first, second):
                part['arabic_text'], emotion = translated[part['segment_id']]
                if not part.get('emotion_set'):
                    part['emotion'] = emotion
            current[index:index + 1] = [first, second]
        elif operation == 'retranslate':
            if not row.get('text', '').strip():
                raise ValueError('Enter the original text first.')
            translated = ld._translate_batch(parent['id'], [row], parent.get('glossary'))
            if not translated.get(segment_id, ('',))[0]:
                raise ValueError('Translation did not finish. Your text was kept.')
            row['arabic_text'], emotion = translated[segment_id]
            if not row.get('emotion_set'):
                row['emotion'] = emotion
        elif operation == 'tashkeel':
            import gemini_service
            text = row.get('arabic_text', '').strip()
            if not text:
                raise ValueError('Enter Arabic text first.')
            if any(ld._word_needs_tashkeel(w) for w in text.split()):
                result = gemini_service.add_tashkeel_lines(parent['id'],
                    [{'segment_id': segment_id, 'arabic_text': text}], ld.GEMINI_API_KEY)
                if result is None:
                    raise ValueError('Tashkeel did not finish. Your text was kept.')
                marked = ld.merge_tashkeel(text, result.get(segment_id, ''))
                if marked == text:
                    raise ValueError('No diacritics could be added. Your text was kept.')
                row['arabic_text'] = marked
        else:
            raise ValueError('Unknown line action.')
        _save_rows(parent, current)
        return new_id


def listening_source(parent):
    source = restore_source(parent)
    if source:
        return source
    return parent if ld.has_media(parent) else None


def player_source(parent):
    source = listening_source(parent)
    if source:
        return source
    return parent if ld.preview_path(parent).exists() else None


def edit(parent, incoming):
    if parent.get('status') != 'done' or active(parent):
        raise ValueError('Wait for this project to finish before editing.')
    current = rows(parent)
    by_id = {r['segment_id']: r for r in current}
    speakers = {s['id'] for s in parent.get('speaker_list', [])}
    duration = ld._total_secs(parent)
    for change in incoming:
        sid = change.get('segment_id')
        if sid not in by_id:
            raise ValueError('This line is not part of the selected project.')
        row = by_id[sid]
        for field in ('text', 'arabic_text'):
            if field in change:
                if not isinstance(change[field], str) or len(change[field]) > 2000:
                    raise ValueError('Line text must be at most 2000 characters.')
                row[field] = change[field]
        if 'speaker_id' in change:
            if change['speaker_id'] not in speakers:
                raise ValueError('Choose one of this project’s original speakers.')
            row['speaker_id'] = change['speaker_id']
        if isinstance(change.get('waqf'), str):
            import arabic_waqf
            row['waqf'] = arabic_waqf.clean_mode(change['waqf'])      # how the voice ends this line: auto / stop / join
        if 'emotion' in change and ld.clean_emotion(change['emotion']) != row.get('emotion', 'neutral'):
            row['emotion'] = ld.clean_emotion(change['emotion'])
            row['emotion_set'] = True
        start, end = float(change.get('start', row['start'])), float(change.get('end', row['end']))
        if not math.isfinite(start + end) or start < 0 or end <= start or end > duration + 0.01:
            raise ValueError('Set finite start/end times inside the original file’s duration.')
        row['start'], row['end'] = start, end
        if 'manual_time' in change:
            row['manual_time'] = bool(change['manual_time'])
        if change.get('emotion_set') is True:
            row['emotion_set'] = True
    current.sort(key=lambda r: r['start'])
    parent['correction_draft'] = current
    ld._save(parent)
    return current


def background(parent):
    path = ld.OUTPUT_DIR / f"{parent['id']}{ld.TRACK_KINDS['effects']}"
    return path if path.exists() else None


def restore_source(parent):
    child = ld.load_job(parent.get('edit_restore_id', ''))
    if child and child.get('uid') == parent['uid'] and child.get('status') == 'editing' and ld.has_media(child):
        return child
    return None


def view(parent):
    current = rows(parent)
    return {'segments': current, 'batches': dub_review.batches(current, parent.get('correction_reviewed')),
            'overlaps': dub_review.overlaps(current), 'speaker_list': parent.get('speaker_list', []),
            'duration': ld._total_secs(parent), 'can_play': bool(player_source(parent)), 'has_assets': bool(background(parent)) and all(
                (parent.get('edit_assets') and (parent.get('dub') or {}).get('voices', {}).get(s['id'])) or
                (ld.job_dir(parent['id']) / 'editrefs' / (s['id'] + '.wav')).exists()
                for s in parent.get('speaker_list', [])),
            'can_restore': bool(parent.get('media_fp')), 'restore_id': parent.get('edit_restore_id'),
            'busy': active(parent), 'history': [ld.public_view(j) for j in ld.list_jobs_for_uid(parent['uid'])
                                              if j.get('edit_of') == parent['id']]}


def output_budget(parent):
    total = ld._total_secs(parent)
    budget = dub_review.output_budget(0, total, False, False, False)
    if background(parent) is None:
        budget += math.ceil(total * 25000) + 1048576
    refs = ld.job_dir(parent['id']) / 'editrefs'
    budget += sum(30 * 44100 * 2 for s in parent.get('speaker_list', []) if not (refs / (s['id'] + '.wav')).exists())
    return budget


def quote(parent, selected):
    if parent.get('status') != 'done' or active(parent):
        raise ValueError('This project is still being processed.')
    if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)):
        raise ValueError('Select at least one different line to correct.')
    current = rows(parent)
    subset = [r for r in current if r['segment_id'] in selected]
    if len(subset) != len(selected) or any(not r.get('arabic_text', '').strip() for r in subset):
        raise ValueError('Each selected line must belong to this project and contain Arabic text.')
    source = restore_source(parent)
    if background(parent) is None and source is None:
        raise ValueError('Attach the original file to recover this older project’s editing assets.')
    import inworld_service
    pricing = ld.Hooks.pricing()
    cpc = max(1, int(pricing.get('chars_per_credit', 60)))
    chars = sum(len(inworld_service.instruction_tag(r.get('emotion'))) + len(r['arabic_text'].strip()) for r in subset)
    speaker_ids = sorted({r['speaker_id'] for r in subset})
    voice_map = (parent.get('dub') or {}).get('voices') or {}
    # Old IDs were deleted even if their names remain in the checkpoint.
    need_clones = [sid for sid in speaker_ids if not parent.get('edit_assets') or not voice_map.get(sid)]
    for sid in need_clones:
        if not (ld.job_dir(parent['id']) / 'editrefs' / f'{sid}.wav').exists() and source is None:
            raise ValueError('Attach the original file again to recover the reference for this speaker.')
    music = ld.music_quote(source) if source and background(parent) is None else {'max_credits': 0, 'repairs': 0}
    voice, clones = math.ceil(chars / cpc), len(need_clones) * max(0, int(pricing.get('clone_credits', 5)))
    merge = max(1, int(pricing.get('merge_credits', 1)))
    payload = {'rows': subset, 'speaker_ids': speaker_ids, 'need_clones': need_clones, 'chars': chars,
               'voice': voice, 'clones': clones, 'merge': merge, 'cpc': cpc,
               'music': music, 'due': voice + clones + merge, 'max_total': voice + clones + merge + music['max_credits']}
    payload['token'] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return payload


def start(parent, selected, token):
    with ld._lock_for(parent['id']):
        plan = quote(parent, selected)
        if token != plan['token']:
            raise ValueError('The selected lines or price changed. Review the price again.')
        balance = ld.Hooks.get_credits(parent['uid'])
        if balance is None or balance < plan['max_total']:
            raise ValueError('Your verified balance must cover the confirmed maximum price.')
        # The same price check made twice (a retry after a lost reply, a second click) names the same job, so it
        # can be paid only once. An earlier try that failed is not reused: a new attempt gets a new job.
        try:
            jid = str(uuid.uuid5(uuid.UUID(parent['id']), token))
        except ValueError:
            jid = str(uuid.uuid4())
        earlier = ld.load_job(jid)
        if earlier is not None:
            if earlier.get('status') != 'failed' and earlier.get('uid') == parent['uid']:
                return earlier
            jid = str(uuid.uuid4())
        job = {'id': jid, 'uid': parent['uid'], 'edit_of': parent['id'], 'status': 'payment_pending',
               'stage': 'queued', 'percent': 0, 'message': 'Waiting to generate the selected lines…',
               'filename': parent.get('filename', 'audio'), 'name': ld.project_name(parent) + ' — corrections',
               'ext': '.m4a', 'has_video': False, 'duration': ld._total_secs(parent), 'size': 0,
               'analysis': {'audio_duration': ld._total_secs(parent)}, 'created': time.time(),
               'paid': {'dub': 0}, 'correction_plan': plan,
               'reserved_output_bytes': output_budget(parent)}
        ld.job_dir(jid).mkdir(parents=True, exist_ok=True)
        ld._save(job)
        if not debit_confirmed(ld._charge(job, 'dub', parent['uid'], plan['due'], 'long_dub_correction')):
            job['status'] = 'failed'
            job['error'] = 'The correction payment could not be confirmed. Nothing was submitted.'
            ld._save(job)
            raise ValueError(job['error'])
        job['paid']['dub'] = plan['due']
        job['status'] = 'confirmed'
        try:
            ld._save(job)
            parent['edit_voice_last_used'] = ld._now()
            ld._save(parent)
            ld.start_worker(jid)
        except Exception:
            refunded = ld._refund(job, parent['uid'], plan['due'], 'dub', 'queue_failed')
            job['status'] = 'failed'
            job['error'] = ('The correction could not be queued. Its confirmed payment was refunded.' if refunded else
                            'The correction could not be queued. Its payment is being returned and will appear in your credit history.')
            try:
                ld._save(job)
            except Exception:
                pass
            raise
        return job


def run(job):
    """Checkpoint each generated line; never generate an unselected voice line."""
    import inworld_service
    import ffmpeg_utils as ff
    import dub_background
    parent = ld.load_job(job['edit_of'])
    work = ld._wd(job)
    fit = work / 'fit'; fit.mkdir(exist_ok=True)
    try:
        if not parent or parent['uid'] != job['uid']:
            raise ValueError('The original project is no longer available.')
        job['status'] = 'dubbing'; ld._save(job)
        plan, total = job['correction_plan'], ld._total_secs(parent)
        checkpoint = job.setdefault('correction_lines', {})
        source = restore_source(parent)
        refs = ld.job_dir(parent['id']) / 'editrefs'; refs.mkdir(exist_ok=True)
        voice_map = parent.setdefault('dub', {}).setdefault('voices', {})
        if not parent.get('edit_assets'):
            voice_map.clear()  # pre-upgrade IDs were already deleted at the provider
        for sid in plan['need_clones']:
            ref = refs / f'{sid}.wav'
            if not ref.exists():
                wav, secs = ld._clone_sample(source, sid, ld.read_segments(source), work)
                if wav is None:
                    raise ValueError('The original file has too little clear speech for this speaker.')
                shutil.copyfile(wav, ref)
            if sid not in job.setdefault('correction_voices', {}):
                vid, err = ld._clone_with_retry(f'lisan-edit-{parent["id"][:8]}-{sid}', ref)
                if not vid:
                    raise ValueError('Could not recover the original speaker’s voice: ' + str(err))
                job['correction_voices'][sid] = vid
                voice_map[sid] = vid
                parent.setdefault('voices_pending_delete', []).append(vid)
                parent['edit_assets'] = True
                parent['edit_voice_last_used'] = time.time()
                ld._save(parent); ld._save(job)
        for i, row in enumerate(plan['rows']):
            sid = row['segment_id']
            if sid in checkpoint and (fit / f'{sid}.wav').exists():
                continue
            ld._mark(job, 'speak', 5 + int(i * 65 / len(plan['rows'])), f'Correcting selected line {i + 1} of {len(plan["rows"])}…')
            voice = job.get('correction_voices', {}).get(row['speaker_id']) or voice_map.get(row['speaker_id'])
            _later = sorted(r['start'] for r in rows(parent) if r['segment_id'] != sid and r['start'] >= row['end'] - 0.05)
            gap = (_later[0] - row['end']) if _later else 99.0       # silence after the line: a long one is a real stop
            audio, err = ld._tts_with_retry(voice, inworld_service.instruction_tag(row.get('emotion')) + row['arabic_text'].strip(), row.get('waqf'), gap)
            if audio is None:
                # A deleted provider voice cannot be recovered by reusing its old ID. Next quote includes a clone.
                if '404' in str(err):
                    voice_map.pop(row['speaker_id'], None); ld._save(parent)
                raise ValueError('A selected line could not be generated: ' + str(err))
            raw = work / f'{sid}.mp3'; raw.write_bytes(audio)
            slot = row['end'] - row['start']
            ref = ld._wd(source) / 'vocals_mono.wav' if source else refs / f'{row["speaker_id"]}.wav'
            meta = ld._fit_line(raw, fit / f'{sid}.wav', slot, slot, ref, row['start'] if source else 0)
            # the original line is measured the same way as the corrected line (the speech level, see ld._speech_levels), so the two compare
            original = None
            if source:
                original, _ = ld._speech_levels(ref, row['start'], slot)
                if original is not None:
                    parent.setdefault('original_voice_levels', {})[sid] = original
                    ld._save(parent)
            if original is None:
                original = parent.get('original_voice_levels', {}).get(sid)
            if original is None:
                envelope = parent.get('original_voice_envelope') or []
                samples = envelope[max(0, int(row['start'] * 2)):min(len(envelope), math.ceil(row['end'] * 2))]
                if samples:
                    rms = math.sqrt(sum(v*v for v in samples) / len(samples))
                    original = 20 * math.log10(max(rms, 1e-8))
            measured, peak = ld._speech_levels(fit / f'{sid}.wav')
            if original is not None and measured is not None and peak is not None:
                # the speaker's usual level (kept from the first dubbing) keeps a corrected line at the same level as its neighbours
                anchor = (parent.get('voice_anchor') or {}).get(str(row['speaker_id']))
                meta['gain_db'] = ld._voice_gain(original, measured, peak, anchor)
            meta.update(seg=sid, start=row['start'], allowed=slot, trim=meta['dur'] > slot)
            checkpoint[sid] = meta; ld._save(job)
            raw.unlink(missing_ok=True)
        pieces = []
        # Silence is the base of every chunk; only selected voice clips are placed on it.
        for i, start in enumerate(range(0, math.ceil(total), 45)):
            end = min(total, start + 45)
            items = []
            for original in checkpoint.values():
                a, b = original['start'], original['start'] + original['allowed']
                if a < end and b > start:
                    # A crossing line is rendered once into the first chunk, then split sample-exactly below.
                    items.append(original)
            path = work / f'chunk{i}.wav'
            _mix_correction_chunk(items, start, end, fit, path)
            pieces.append(path)
        voices = work / 'voices.wav'; ld._concat_wavs(pieces, voices)
        effects = background(parent)
        if effects is None:
            if source is None:
                raise ValueError('The original background is unavailable. Attach the original file.')
            sw = ld._wd(source); all_rows = ld.read_segments(source)
            fee = int(ld.Hooks.pricing().get('music_fill_credits', 10))
            def allow():
                bal = ld.Hooks.get_credits(job['uid'])
                return bal is not None and bal >= fee and job['paid'].get('music_fill', 0) + fee <= plan['music']['max_credits']
            def charge(a, b):
                if not debit_confirmed(ld._charge(job, f'music_fill:{a:.3f}:{b:.3f}', job['uid'], fee, 'long_dub_music_fill')):
                    raise ValueError('Music repair payment could not be verified')
                ld._sync_paid(job); ld._save(job)
            rebuilt = dub_background.checkpointed_prepare(job, ld._save, plan['music'].get('repairs', 0), sw / 'background.wav', sw / 'vocals_mono.wav', voices, work,
                'restore_bg', dub_background.speech_spans(sw / 'speech_spans.json', all_rows),
                key=ld.FAL_API_KEY, gemini_key=ld.GEMINI_API_KEY, allow=allow, on_filled=charge)
            effects = ld.OUTPUT_DIR / f"{parent['id']}{ld.TRACK_KINDS['effects']}"
            restored_effects = work / 'recovered_effects.m4a'
            ff.run_ffmpeg(['ffmpeg', '-y', '-i', str(rebuilt['path']), '-t', str(total), '-c:a', 'aac', '-b:a', '192k', str(restored_effects)])
            os.replace(restored_effects, effects)
            ld._save(parent)
        final = ld.OUTPUT_DIR / f"{job['id']}_final_corrections.m4a"
        tmp = work / 'corrections.m4a'
        ff.run_ffmpeg(['ffmpeg', '-y', '-i', str(voices), '-i', str(effects), '-filter_complex',
                      '[0:a][1:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97:level=disabled[out]',
                      '-map', '[out]', '-t', str(total), '-c:a', 'aac', '-b:a', '192k', str(tmp)])
        pure = ld.OUTPUT_DIR / f"{job['id']}{ld.TRACK_KINDS['voices']}"
        ff.run_ffmpeg(['ffmpeg', '-y', '-i', str(voices), '-t', str(total), '-c:a', 'aac', '-b:a', '192k', str(pure)])
        os.replace(tmp, final)
        job['result'] = {'kind': 'audio', 'file': final.name, 'size': final.stat().st_size, 'duration': total,
                         'finished': time.time(), 'lines': len(plan['rows']), 'selected': [r['segment_id'] for r in plan['rows']],
                         'tracks': {'voices': {'size': pure.stat().st_size}}}
        parent['edit_voice_last_used'] = time.time(); ld._save(parent)
        job['status'] = 'done'; ld._mark(job, 'done', 100, 'Your correction tracks are ready.'); ld._save(job)
        try:
            ld.Hooks.send_email(job['uid'], 'Your Lisan AI corrections are ready', 'Download the correction tracks at https://lisanai.org/dub-long-edit')
        except Exception:
            pass  # a mail outage must not refund and mark a finished export as failed
    except Exception as ex:
        ld._fail(job, str(ex), 'dub')
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _mix_correction_chunk(items, start, end, fit, output):
    """Trim an already timed line at the chunk boundary instead of restarting its audio."""
    import ffmpeg_utils as ff
    inputs = ['-f', 'lavfi', '-t', str(end - start), '-i', f'anullsrc=r={ld.SAMPLE_RATE}:cl=stereo']
    filters, labels = [], ['[0:a]']
    for i, item in enumerate(items, 1):
        inputs += ['-i', str(fit / f'{item["seg"]}.wav')]
        offset = max(0, start - item['start'])
        duration = min(end, item['start'] + item['allowed']) - max(start, item['start'])
        delay = max(0, round((item['start'] - start) * 1000))
        gain = 10 ** (item.get('gain_db', 0) / 20)
        filters.append(f'[{i}:a]atrim=start={offset}:duration={duration},asetpts=PTS-STARTPTS,volume={gain},adelay={delay}|{delay}[a{i}]')
        labels.append(f'[a{i}]')
    filters.append(''.join(labels) + f'amix=inputs={len(labels)}:duration=first:normalize=0,atrim=duration={end-start}[out]')
    ff.run_ffmpeg(['ffmpeg', '-y'] + inputs + ['-filter_complex', ';'.join(filters), '-map', '[out]', '-c:a', 'pcm_s16le', str(output)])


def finish(parent):
    with ld._lock_for(parent['id']):
        if active(parent):
            raise ValueError('Wait for the correction export to finish.')
        ld._delete_pending_voices(parent)
        parent['edit_assets'] = False
        parent.setdefault('dub', {})['voices'] = {}
        ld._save(parent)
