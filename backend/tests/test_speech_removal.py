"""Explicit cleanup, independent placement, ownership, zero-cost work and bounded audio."""
import ast
import contextlib
import copy
import json
import math
import os
import tempfile
import threading
import time
import uuid
import sys
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
import speech_removal as sr
import dub_background as db
import bg_duck
import pause_bed
import section_background


def functions(filename, names, namespace):
    nodes = [copy.deepcopy(n) for n in ast.parse((ROOT / filename).read_text(encoding='utf-8-sig')).body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert len(nodes) == len(names)
    for node in nodes:
        node.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)] + nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT / filename), 'exec'), namespace)
    return namespace


class RegionTests(unittest.TestCase):
    def test_validation_keeps_individually_undoable_overlaps(self):
        self.assertEqual(sr.validate([[2, 4], [1, 3], [2, 4]], 10), [[1.0, 3.0], [2.0, 4.0]])

    def test_invalid_or_nonfinite_regions_never_enter_a_record(self):
        for value in [None, {}, [[True, 3]], [[-1, 3]], [[2, 2]], [[1, 11]], [[1, math.inf]],
                      [[math.nan, 2]], [['bad', 2]], [[1]], [[0, .0001]], [[0, 1]] * 257]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                sr.validate(value, 10)

    def test_old_projects_default_to_no_explicit_regions(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(sr.read(Path(folder) / sr.FILE_NAME), [])

    def test_save_undo_and_stale_tab_do_not_depend_on_line_times(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / sr.FILE_NAME
            before = sr.revision([])
            result = sr.write(path, [[1, 2]], 10, before)
            with self.assertRaises(ValueError):
                sr.write(path, [[3, 4]], 10, before)
            self.assertEqual(sr.read(path), [[1.0, 2.0]])
            sr.write(path, [], 10, result['revision'])
            self.assertEqual(sr.read(path), [])
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_damaged_saved_record_blocks_instead_of_dropping_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / sr.FILE_NAME
            path.write_text('{broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                sr.read(path)

    def test_sound_sections_keep_forced_and_reference_regions(self):
        spans = sr.SpeechSpans([(8, 12)], [(9, 11)], [(7, 13)])
        cut = section_background._spans(spans, 10 * section_background.RATE, 12 * section_background.RATE)
        self.assertEqual(cut, [(0.0, 2.0)])
        self.assertEqual(cut.forced, [(0.0, 1.0)])
        self.assertEqual(cut.reference, [(0.0, 2.0)])


class SourcePlacementTests(unittest.TestCase):
    def test_alignment_changes_keep_original_cleanup_bounds(self):
        row = {'segment_id': 's', 'start': 2.0, 'end': 3.0, 'text': 'hello'}
        env = functions('longdub_service.py', ['set_line_time'], {
            'math': math, '_lock_for': lambda _: threading.RLock(),
            '_total_secs': lambda _: 10, 'read_segments': lambda _: [row],
            '_time_error': lambda *a: None, '_clear_speaker_review': lambda _: None,
            '_by_start': lambda rows: rows, '_write_segments': lambda *a: None, '_ev': lambda *a: None,
        })
        job = {'id': 'job', 'status': 'editing'}
        self.assertTrue(env['set_line_time'](job, 's', 6, 7, True)[0])
        self.assertTrue(env['set_line_time'](job, 's', 8, 9, True)[0])
        self.assertEqual((row['source_start'], row['source_end']), (2, 3))
        with tempfile.TemporaryDirectory() as folder:
            spans = db.speech_spans(Path(folder) / 'speech_spans.json', [row])
        self.assertEqual(spans, [(2.0, 3.0)])
        self.assertFalse(sr.forced(spans))

    def test_added_or_old_manually_timed_lines_are_not_assumed_to_be_source_speech(self):
        rows = [{'start': 4, 'end': 5, 'text': 'added', 'added': True},
                {'start': 6, 'end': 7, 'text': 'old aligned line', 'manual_time': True}]
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(db.speech_spans(Path(folder) / 'speech_spans.json', rows), [])

    def test_arabic_only_inserted_line_can_have_an_explicit_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            map_path = Path(folder) / 'speech_spans.json'
            sr.write(map_path.with_name(sr.FILE_NAME), [[4, 5]], 10, sr.revision([]))
            spans = db.speech_spans(map_path, [{'start': 7, 'end': 8, 'text': '', 'arabic_text': 'نعم'}])
            self.assertEqual(spans, [(4.0, 5.0)])
            self.assertEqual(spans.forced, [[4.0, 5.0]])

    def test_completed_checkpoint_cannot_hide_a_new_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); (folder / 'bg_matched.wav').write_bytes(b'old')
            old, new = sr.SpeechSpans([(1, 2)]), sr.SpeechSpans([(1, 2), (4, 5)], [(4, 5)])
            job = {'background_checkpoint': {'bg': {'started': True, 'ready': True,
                'speech_signature': sr.signature(old), 'music_fill': {}, 'measurements': {}}}, 'paid': {'music_fill': 10}}
            with patch.object(db, 'prepare') as prepare, self.assertRaises(RuntimeError):
                db.checkpointed_prepare(job, lambda _: None, 1, None, None, None, folder, 'bg', new)
            prepare.assert_not_called()


class OwnershipTests(unittest.TestCase):
    def test_short_unknown_or_foreign_owner_cannot_read_or_edit(self):
        for owner, uid in [(None, 'me'), ('other', 'me'), ('me', None)]:
            lookup = Mock(return_value=owner)
            env = functions('main.py', ['_short_removal_location'], {
                '_job_guard': lambda *a, **k: None, '_current_uid': lambda _: uid,
                '_job_owner': {}, '_uid_for_job': lookup, 'JSONResponse': JSONResponse,
                'job_background_audio': Mock(side_effect=AssertionError('foreign audio accessed')),
            })
            self.assertEqual(env['_short_removal_location']('job', object())[2].status_code, 404)

    def test_long_save_rechecks_ownership_and_blocks_a_running_export(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for latest, active, expected in [({'uid': 'other', 'status': 'done'}, False, 404),
                                             ({'uid': 'me', 'status': 'done'}, True, 409)]:
                ld = NS(_lock_for=lambda _: threading.RLock(), load_job=lambda _: latest,
                        _wd=lambda _: folder, _total_secs=lambda _: 10, _ev=Mock())
                env = functions('main.py', ['long_save_speech_removals'], {
                    '_ld_job': lambda *a: ('me', {'uid': 'me'}, None), 'longdub_service': ld,
                    'longdub_edits': NS(active=lambda _: active), 'JSONResponse': JSONResponse,
                    'customer_message': str,
                })
                result = env['long_save_speech_removals']('job', NS(ranges=[[1, 2]], revision=sr.revision([])), object())
                self.assertEqual(result.status_code, expected)
                self.assertFalse((folder / sr.FILE_NAME).exists())
                ld._ev.assert_not_called()

    def test_owned_save_has_no_credit_or_generation_dependency(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); job = {'uid': 'me', 'status': 'editing'}
            ld = NS(_lock_for=lambda _: threading.RLock(), load_job=lambda _: job,
                    _wd=lambda _: folder, _total_secs=lambda _: 10, _ev=Mock())
            env = functions('main.py', ['long_save_speech_removals'], {
                '_ld_job': lambda *a: ('me', job, None), 'longdub_service': ld,
                'longdub_edits': NS(active=lambda _: False), 'JSONResponse': JSONResponse,
                'customer_message': str,
            })
            result = env['long_save_speech_removals']('job', NS(ranges=[[1, 2]], revision=sr.revision([])), object())
            self.assertEqual(result['ranges'], [[1.0, 2.0]])
            self.assertEqual(ld._ev.call_args.args[1], 'speech_removal_saved')


class MandatoryAudioTests(unittest.TestCase):
    def test_original_pause_restore_obeys_removal_even_when_voice_detection_misses_it(self):
        spans = sr.SpeechSpans([], [(2, 3)])
        n = 5 * pause_bed.RATE; nf = n // pause_bed.FRAME
        def levels(source, count):
            return np.full(count, -120.0 if source == 'voices' else -25.0)
        with patch.object(pause_bed, '_mono_db', side_effect=levels):
            mask = pause_bed.speech_frames('background', 'original', 'voices', spans, n)
        self.assertTrue(mask[200:300].all())
        self.assertFalse(mask[:100].any())

    def test_references_exclude_other_known_dialogue_when_a_vocal_stem_is_missing(self):
        spans = sr.SpeechSpans([(2, 3)], [(2, 3)], [(0.5, 1.5), (2, 3)])
        n = 5 * pause_bed.RATE
        with patch.object(pause_bed, '_mono_db', side_effect=lambda _, count: np.full(count, -25.0)):
            mask = pause_bed.speech_frames('same-bed', 'same-bed', None, spans, n)
        self.assertTrue(mask[60:140].all())
        self.assertTrue(mask[200:300].all())
        self.assertFalse(mask[400:].any())

    def test_hour_long_mask_is_frame_sized_and_not_hour_long_pcm(self):
        n = 3600 * pause_bed.RATE
        with patch.object(pause_bed, '_mono_db', side_effect=lambda _, count: np.full(count, -25.0)):
            mask = pause_bed.speech_frames(None, None, None, sr.SpeechSpans([(120, 121)], [(120, 121)]), n)
        self.assertEqual(mask.dtype, np.bool_)
        self.assertEqual(mask.nbytes, n // pause_bed.FRAME)
        self.assertTrue(mask[12000:12100].all())

    def test_keep_music_cannot_keep_a_confirmed_missed_word(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); rate = bg_duck.RATE
            times = np.arange(6 * rate) / rate
            x = .03 * np.sin(2 * np.pi * 200 * times)
            word = (times >= 2) & (times < 2.3)
            x[word] += .08 * np.sin(2 * np.pi * 1700 * times[word])
            bg, vocal, output = folder / 'bg.wav', folder / 'voice.wav', folder / 'clean.wav'
            sf.write(bg, np.column_stack([x, x]), rate, subtype='PCM_16')
            sf.write(vocal, np.zeros(len(x)), rate, subtype='PCM_16')
            result = bg_duck.mute_speech(bg, vocal, output, sr.SpeechSpans([(4, 5)], [(2, 2.3)]), mode='always', keep=True, strict=True)
            self.assertTrue(result['muted'], result)
            clean, _ = sf.read(output, dtype='float32')
            self.assertEqual(float(np.max(np.abs(clean[int(2.03 * rate):int(2.27 * rate)]))), 0.0)
            self.assertGreater(float(np.max(np.abs(clean[:rate]))), .02)

    def test_less_than_two_seconds_and_a_brief_region_are_still_cleaned(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); rate = bg_duck.RATE
            times = np.arange(int(.8 * rate)) / rate
            x = np.column_stack([.08 * np.sin(2 * np.pi * 500 * times)] * 2)
            bg, output = folder / 'bg.wav', folder / 'out.wav'; sf.write(bg, x, rate, subtype='PCM_16')
            result = bg_duck.mute_speech(bg, None, output, sr.SpeechSpans([], [(0.2, 0.22)]), mode='always', keep=False, strict=True)
            self.assertTrue(result['muted'], result)
            clean, _ = sf.read(output)
            self.assertEqual(float(np.max(np.abs(clean[int(.2 * rate):int(.22 * rate)]))), 0)

    def test_splice_keeps_other_sections_and_original_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); rate = 44100
            times = np.arange(4 * rate) / rate
            original = .05 * np.sin(2 * np.pi * 1700 * times)
            replacement = .02 * np.sin(2 * np.pi * 200 * times)
            base, cleaned, output = folder / 'base.wav', folder / 'clean.wav', folder / 'out.wav'
            sf.write(base, np.column_stack([original] * 2), rate, subtype='PCM_16')
            sf.write(cleaned, np.column_stack([replacement] * 2), rate, subtype='PCM_16')
            sr.splice_background(base, cleaned, [[1, 2]], output)
            result, _ = sf.read(output, dtype='int16'); old, _ = sf.read(base, dtype='int16'); new, _ = sf.read(cleaned, dtype='int16')
            self.assertEqual(result.shape, old.shape)
            np.testing.assert_array_equal(result[:int(.7 * rate)], old[:int(.7 * rate)])
            np.testing.assert_array_equal(result[3 * rate:], old[3 * rate:])
            np.testing.assert_array_equal(result[rate:2 * rate], new[rate:2 * rate])

    def test_full_local_prepare_cannot_restore_the_removed_source_word(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); rate = 44100; n = 8 * rate
            times = np.arange(n) / rate; rng = np.random.default_rng(37)
            x = .01 * rng.standard_normal((n, 2))
            mask = (times >= 3) & (times < 3.8)
            carrier = np.sin(2 * np.pi * 1700 * times[mask])
            x[mask] += .1 * carrier[:, None]
            base, dub = folder / 'base.wav', folder / 'dub.wav'
            sf.write(base, x, rate, subtype='PCM_16'); sf.write(dub, np.zeros((n, 2)), rate, subtype='PCM_16')
            result = db.prepare(base, None, dub, folder, 'test', sr.SpeechSpans([(3, 3.8)], [(3, 3.8)]),
                                original=base, key='', strict=False, scenes=[])
            clean, _ = sf.read(result['path'], dtype='float32')
            self.assertEqual(len(clean), n)
            amplitude = 2 * float(np.mean(clean[mask, 0] * carrier))
            self.assertLess(abs(amplitude), .001, 'pause restoration or matching reintroduced the selected word')


class ExportTests(unittest.TestCase):
    def quote_environment(self, folder, parent, base):
        row = {'segment_id': 'line', 'speaker_id': 'speaker', 'start': 1, 'end': 2, 'arabic_text': 'نعم'}
        ld = NS(_wd=lambda _: folder, _job_pricing=lambda _: {'merge_credits': 3, 'chars_per_credit': 60},
                FAL_API_KEY='', read_segments=lambda _: [row], job_dir=lambda _: folder)
        fake_voice = NS(instruction_tag=lambda _: '')
        env = functions('longdub_edits.py', ['quote'], {
            'ld': ld, 'active': lambda _: False, 'rows': lambda _: [row],
            'restore_source': lambda _: None, 'background': lambda _: base, 'removal_background': lambda *a: None,
            'speech_removal': sr, 'math': math, 'json': json, 'hashlib': __import__('hashlib'),
        })
        return env['quote'], fake_voice

    def test_background_only_quote_costs_zero_and_changed_regions_invalidate_it(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); base = folder / 'base.m4a'; base.write_bytes(b'bed')
            parent = {'id': 'parent', 'status': 'done', 'edit_assets': True, 'dub': {'voices': {'speaker': 'v'}}}
            result = sr.write(folder / sr.FILE_NAME, [[1, 2]], 10, sr.revision([]))
            quote, voice = self.quote_environment(folder, parent, base)
            with patch.dict(sys.modules, {'inworld_service': voice}), patch.object(db, 'removal_quote', return_value={'max_credits': 0, 'repairs': 0, 'kept': True}):
                plan = quote(parent, [])
                self.assertEqual([plan[k] for k in ('voice', 'clones', 'merge', 'due', 'max_total')], [0] * 5)
                self.assertEqual(plan['need_clones'], [])
                sr.write(folder / sr.FILE_NAME, [[3, 4]], 10, result['revision'])
                self.assertNotEqual(plan['token'], quote(parent, [])['token'])
                self.assertEqual(quote(parent, ['line'])['merge'], 0, 'local cleanup is free even alongside paid speech')

    def test_undo_does_not_charge_a_local_export(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); base = folder / 'base.m4a'; base.write_bytes(b'bed')
            sr.write(folder / sr.FILE_NAME, [], 10, sr.revision([]))
            parent = {'status': 'done', 'edit_assets': True, 'dub': {'voices': {'speaker': 'v'}}}
            quote, voice = self.quote_environment(folder, parent, base)
            with patch.dict(sys.modules, {'inworld_service': voice}):
                self.assertEqual(quote(parent, [])['max_total'], 0)

    def test_undo_of_a_recovered_old_background_needs_its_original_before_charging(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); base = folder / 'recovered.m4a'; base.write_bytes(b'cleaned with old selection')
            sr.write(folder / sr.FILE_NAME, [], 10, sr.revision([]))
            parent = {'status': 'done', 'background_speech_removals': [[1, 2]], 'edit_assets': True,
                      'dub': {'voices': {'speaker': 'v'}}}
            quote, voice = self.quote_environment(folder, parent, base)
            with self.assertRaisesRegex(ValueError, 'Attach the original file'):
                quote(parent, [])
            source = {'id': 'original'}
            quote.__globals__['restore_source'] = lambda _: source
            ld = quote.__globals__['ld']; ld.music_quote = Mock(return_value={'max_credits': 0, 'repairs': 0})
            with patch.dict(sys.modules, {'inworld_service': voice}):
                plan = quote(parent, [])
            self.assertEqual(plan['background_base'], '')
            ld.music_quote.assert_called_once_with(source, removals=[])

    def test_a_background_recovered_with_the_same_regions_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); base = folder / 'recovered.m4a'; base.write_bytes(b'cleaned once')
            env = functions('longdub_edits.py', ['removal_background'], {'background': lambda _: base})
            self.assertEqual(env['removal_background']({'background_speech_removals': [[1, 2]]}, [[1, 2]]), base)

    def test_cleanup_cache_requires_owned_completed_job_and_unchanged_original(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); base = folder / 'base.m4a'; base.write_bytes(b'original')
            clean = folder / 'child_effects.m4a'; clean.write_bytes(b'clean')
            parent = {'id': 'parent', 'uid': 'owner', 'speech_removal_cache': {
                'ranges': [[1, 2]], 'base': sr.asset_signature(base), 'job_id': 'child'}}
            child = {'id': 'child', 'uid': 'owner', 'edit_of': 'parent', 'status': 'done'}
            env = functions('longdub_edits.py', ['removal_background'], {
                'speech_removal': sr, 'background': lambda _: base,
                'ld': NS(load_job=lambda _: child, OUTPUT_DIR=folder, TRACK_KINDS={'effects': '_effects.m4a'})})
            lookup = env['removal_background']
            self.assertEqual(lookup(parent, [[1, 2]]), clean)
            for changes in ({'uid': 'other'}, {'edit_of': 'other'}, {'status': 'dubbing'}):
                with patch.dict(child, changes):
                    self.assertIsNone(lookup(parent, [[1, 2]]))
            self.assertIsNone(lookup(parent, [[2, 3]]))
            base.write_bytes(b'new original')
            self.assertIsNone(lookup(parent, [[1, 2]]))

    def test_free_export_start_neither_debits_nor_extends_voice_retention(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); parent = {'id': str(uuid.uuid4()), 'uid': 'owner', 'filename': 'test'}
            plan = {'token': 'price', 'rows': [], 'max_total': 0, 'due': 0}
            ld = NS(_lock_for=lambda _: threading.RLock(), Hooks=NS(get_credits=lambda _: None),
                load_job=lambda _: None, _total_secs=lambda _: 4, project_name=lambda _: 'Project',
                job_dir=lambda jid: folder / jid, _save=Mock(), _charge=Mock(), start_worker=Mock(), _refund=Mock())
            env = functions('longdub_edits.py', ['start'], {'ld': ld, 'quote': lambda *a: plan, 'uuid': uuid,
                'time': time, 'languages': NS(get=lambda _: NS(code='en')), 'output_budget': lambda _: 1000,
                'debit_confirmed': lambda _: False})
            job = env['start'](parent, [], 'price')
            self.assertEqual(job['status'], 'confirmed')
            ld._charge.assert_not_called(); ld._refund.assert_not_called()
            ld.start_worker.assert_called_once()
            self.assertNotIn('edit_voice_last_used', parent)
            with self.assertRaisesRegex(ValueError, 'price changed'):
                env['start'](parent, [], 'stale')

    def test_free_background_export_never_clones_or_speaks(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); output = folder / 'out'; output.mkdir()
            base = output / 'parent_effects.m4a'; base.write_bytes(b'original')
            job = {'id': 'child', 'uid': 'owner', 'edit_of': 'parent', 'paid': {'dub': 0}, 'correction_plan': {
                'rows': [], 'need_clones': [], 'speech_removals': [[1, 2]], 'background_base': sr.asset_signature(base),
                'music': {'max_credits': 0, 'repairs': 0, 'kept': False}}}
            parent = {'id': 'parent', 'uid': 'owner', 'dub': {'voices': {'speaker': 'retained'}}}
            def wd(item):
                p = folder / item['id']; p.mkdir(exist_ok=True); return p
            def concat(_, path): path.write_bytes(b'voice silence')
            def render(args): Path(args[-1]).write_bytes(b'audio')
            def prepare(*args, **kwargs):
                path = args[6] / 'clean.wav'; path.write_bytes(b'clean')
                return {'path': path, 'music_fill': {'filled': False}}
            def splice(base, clean, ranges, target): target.write_bytes(b'spliced'); return target
            ld = NS(load_job=lambda _: parent, _wd=wd, _save=Mock(), _mark=Mock(), _total_secs=lambda _: 4,
                job_dir=lambda _: wd(parent), _concat_wavs=concat, OUTPUT_DIR=output,
                TRACK_KINDS={'effects': '_effects.m4a', 'voices': '_voices.m4a'}, _job_pricing=lambda _: {}, read_segments=lambda _: [],
                _clone_with_retry=Mock(side_effect=AssertionError('clone called')),
                _tts_with_retry=Mock(side_effect=AssertionError('speech called')), _charge=Mock(),
                _fail=Mock(), FAL_API_KEY='', Hooks=NS(send_email=Mock()))
            env = functions('longdub_edits.py', ['run'], {'ld': ld, 'restore_source': lambda _: None,
                'background': lambda _: base, 'removal_background': lambda *a: None, 'speech_removal': sr,
                'math': math, 'Path': Path, 'os': os, 'shutil': __import__('shutil'), 'time': time,
                '_mix_correction_chunk': lambda *a: a[-1].write_bytes(b'silence'), 'debit_confirmed': lambda _: False})
            with patch.dict(sys.modules, {'inworld_service': NS(), 'ffmpeg_utils': NS(run_ffmpeg=render)}), \
                 patch.object(db, 'checkpointed_prepare', side_effect=prepare), patch.object(sr, 'splice_background', side_effect=splice):
                env['run'](job)
            self.assertEqual(ld._fail.call_args, None, str(ld._fail.call_args))
            self.assertEqual(job['status'], 'done')
            self.assertTrue(job['result']['background_only'])
            self.assertEqual(job['result']['duration'], 4)
            self.assertEqual(parent['dub']['voices'], {'speaker': 'retained'})
            self.assertNotIn('edit_voice_last_used', parent)
            ld._clone_with_retry.assert_not_called(); ld._tts_with_retry.assert_not_called(); ld._charge.assert_not_called()
            self.assertEqual(base.read_bytes(), b'original', 'undo must still have its immutable original bed')

    def test_short_cleanup_quote_is_free_and_stale_revision_stops_before_export(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); (folder / 'job_final_dubbed.mp3').write_bytes(b'voice')
            base = folder / 'background.wav'; base.write_bytes(b'bed')
            sr.write(folder / sr.FILE_NAME, [[1, 2]], 10, sr.revision([]))
            env = functions('main.py', ['_short_merge_price', 'merge_video'], {
                '_get_pricing_config': lambda: {'mergeCredits': 5}, 'OUTPUT_DIR': folder,
                'find_job_video': lambda _: folder / 'video.mp4', 'job_background_audio': lambda _: base,
                '_job_guard': lambda *a, **k: None, '_paid_uid': lambda _: ('owner', None),
                'begin_operation': lambda _: True, 'finish_operation': Mock(), 'JSONResponse': JSONResponse,
                '_merge_video_run': Mock(side_effect=AssertionError('stale export started')),
            })
            req = NS(job_id='job', keep_music=False, accepted_credits=0, speech_removal_revision='old')
            self.assertEqual(env['_short_merge_price'](req)['max_total'], 0)
            self.assertEqual(env['merge_video'](req, object()).status_code, 409)
            env['_merge_video_run'].assert_not_called()
            env['finish_operation'].assert_called_once()


class FrontendTests(unittest.TestCase):
    def test_shared_ui_save_undo_and_project_restore_contracts(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is not installed')
        result = subprocess.run([node, '--test', str(Path(__file__).with_name('test_speech_removal_ui.js'))],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
