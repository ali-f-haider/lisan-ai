"""Offline regression checks. Never imports main/config or contacts providers.

Run: python -m unittest discover -s tests -p test_shortdub_upgrade.py -v
Route and worker functions are extracted from the actual source; external
services are replaced by mocks so importing the live cleanup threads is avoided.
"""
import ast
import copy
import re
import sys
import tempfile
import threading
import unittest
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shortdub_paths import line_audio_path, clear_line_audio, begin_operation, finish_operation, operation_active
from shortdub_billing import studio_quote, debit_confirmed

JOB_A, JOB_B = 'a' * 32, 'b' * 32


def source_functions(filename, names, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding='utf-8-sig'))
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node = copy.deepcopy(node)
            node.decorator_list = []
            nodes.append(node)
    assert len(nodes) == len(names), (filename, names, [n.name for n in nodes])
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)] + nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT / filename), 'exec'), namespace)
    return namespace


def segment(text='مرحبا', sid='seg_0', **kwargs):
    values = dict(segment_id=sid, arabic_text=text, emotion='neutral', speaker='Speaker 1', start=0., end=1., tempo_mode='excellent')
    values.update(kwargs)
    return NS(**values)


def request(**kwargs):
    seg = segment()
    values = dict(job_id=JOB_A, segment=seg, segments=[seg], voice_id='voice', voice_engine='elevenlabs',
                  speaker_voices={'Speaker 1': 'voice'}, default_voice_id='', speaker_voice_engines={},
                  default_voice_engine='elevenlabs', accepted_credits=None, tts_provider='elevenlabs',
                  elevenlabs_api_key='test', inworld_api_key='test', gemini_api_key='test', cloned_voice_ids=[],
                  gemini_voice='Kore', tempo_mode='excellent', duration_mode='exact', total_duration=4.,
                  overlap_allowed={}, dead_space_allowed={}, offsets={}, gains={}, room={})
    values.update(kwargs)
    return NS(**values)


class Response:
    def __init__(self, content, status_code=200):
        self.content, self.status_code = content, status_code


class PathTests(unittest.TestCase):
    def tearDown(self):
        finish_operation(JOB_A)
        finish_operation(JOB_B)

    def test_identical_segment_ids_never_share_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            a = line_audio_path(directory, JOB_A, 'seg_0', 'stretched', '.wav')
            b = line_audio_path(directory, JOB_B, 'seg_0', 'stretched', '.wav')
            a.write_bytes(b'customer A')
            b.write_bytes(b'customer B')
            self.assertNotEqual(a, b)
            self.assertEqual(a.read_bytes(), b'customer A')
            clear_line_audio(directory, JOB_B)
            self.assertEqual(a.read_bytes(), b'customer A')

    def test_cleanup_preserves_other_jobs_final_outputs_and_legacy_files(self):
        with tempfile.TemporaryDirectory() as directory:
            remove = line_audio_path(directory, JOB_A, 'seg_0', 'raw', '.mp3')
            preserve = [line_audio_path(directory, JOB_B, 'seg_0', 'raw', '.mp3'),
                        line_audio_path(directory, JOB_A + 'x', 'seg_0', 'raw', '.mp3'),
                        Path(directory) / f'{JOB_A}_final_dubbed.mp3', Path(directory) / 'seg_0_raw.mp3']
            for path in [remove] + preserve:
                path.write_bytes(b'test')
            clear_line_audio(directory, JOB_A)
            self.assertFalse(remove.exists())
            self.assertTrue(all(p.exists() for p in preserve))

    def test_invalid_job_cannot_escape_or_glob_delete(self):
        for job in ('', '..', '../' + JOB_A, '*' * 32, JOB_A + '/x'):
            with self.assertRaises(ValueError):
                line_audio_path('/tmp', job, 'seg_0', 'raw', '.wav')

    def test_unicode_and_windows_reserved_segment_names_are_hashed(self):
        a = line_audio_path('/tmp', JOB_A, 'CON: مشهد', 'raw', '.mp3')
        self.assertRegex(a.name, r'^[a-z0-9_]+\.mp3$')
        self.assertNotEqual(a, line_audio_path('/tmp', JOB_A, 'con: مشهد', 'raw', '.mp3'))

    def test_same_job_has_one_operation_different_jobs_can_run(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: begin_operation(JOB_A), range(8)))
        self.assertEqual(sum(results), 1)
        self.assertTrue(begin_operation(JOB_B))
        finish_operation(JOB_A)
        self.assertTrue(begin_operation(JOB_A))


class PricingTests(unittest.TestCase):
    def test_quote_includes_analysis_previously_missing_from_badge(self):
        # $0.0104 analysis at the configured 100 credits/cent = 104 credits.
        q = studio_quote({'elevenlabs': 600}, {'gemini_out': 4160}, {'charsPerCredit': 60, 'geminiCreditsPerCent': 100})
        self.assertEqual((q['voice_credits'], q['analysis_credits'], q['credits']), (10, 104, 114))

    def test_engine_rates_round_separately(self):
        q = studio_quote({'elevenlabs': 61, 'inworld': 101}, {}, {'charsPerCredit': 60, 'inworldCharsPerCredit': 100})
        self.assertEqual(q['credits'], 4)

    def test_repeat_generation_does_not_rebill_analysis_or_regeneration(self):
        bucket = {'gemini_in': 10000, 'gemini_out': 300, 'audio_sec': 5, 'eleven_chars': 99000}
        first = studio_quote({'elevenlabs': 60}, bucket, {})
        bucket['shortdub_ai_settled'] = first['ai_snapshot']
        bucket['eleven_chars'] += 7000  # old generation/regeneration counters
        second = studio_quote({'elevenlabs': 60}, bucket, {})
        self.assertEqual(second['credits'], 1)
        bucket['gemini_out'] += 8000
        self.assertEqual(studio_quote({'elevenlabs': 60}, bucket, {})['analysis_credits'], 2)

    def test_no_phantom_analysis_fee(self):
        self.assertEqual(studio_quote({'elevenlabs': 60}, {}, {})['analysis_credits'], 0)

    def test_only_actual_debit_results_are_confirmed(self):
        for value in (None, False, -1, {}, [], '0'):
            self.assertFalse(debit_confirmed(value))
        for value in (True, 0, 50):
            self.assertTrue(debit_confirmed(value))

    def test_spend_history_records_confirmed_debits_only(self):
        tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8-sig'))
        wrapper = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'deduct_credits' and isinstance(n.args.vararg, ast.arg)][0]
        namespace = {'_od': Mock(), '_record_spend': Mock(), 'debit_confirmed': debit_confirmed}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), 'main.py', 'exec'), namespace)
        for result in (None, False):
            namespace['_od'].return_value = result
            namespace['deduct_credits']('user', 10, 'generate', JOB_A)
        namespace['_record_spend'].assert_not_called()
        namespace['_od'].return_value = 0
        namespace['deduct_credits']('user', 10, 'generate', JOB_A)
        namespace['_record_spend'].assert_called_once_with('user', 'generate', 10, JOB_A, None)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.buckets = {}
        self.progress, self.charges = {}, {}
        self.cfg = {'charsPerCredit': 60, 'inworldCharsPerCredit': 100, 'minReserve': 20}
        n = dict(JSONResponse=Response, _job_guard=Mock(return_value=None), _paid_uid=Mock(return_value=('user', None)),
                 _get_pricing_config=lambda: self.cfg, _voice_engines_for_ids=Mock(return_value={}),
                 usage_bucket=lambda job: self.buckets.setdefault(job, {}), get_credits=Mock(return_value=1000),
                 deduct_credits=Mock(return_value=900), _rate_limited=Mock(return_value=False),
                 HEAVY_RATE_MAX=1, HEAVY_RATE_WINDOW_SEC=1, LIGHT_RATE_MAX=1, LIGHT_RATE_WINDOW_SEC=1,
                 _storage_block=Mock(return_value=None), ELEVENLABS_API_KEY='test', INWORLD_API_KEY='test', GEMINI_API_KEY='test',
                 begin_operation=begin_operation, finish_operation=finish_operation, operation_active=operation_active,
                 _abandoned_jobs=set(), jobs_progress=self.progress, _job_charges=self.charges,
                 studio_quote=studio_quote, debit_confirmed=debit_confirmed, _public_progress=lambda x: x,
                 _watch_and_deduct=Mock(), threading=NS(Thread=Mock(return_value=NS(start=Mock()))),
                 eleven_service=NS(_emotion_tags=lambda emotion: '[neutral]', regenerate_line=Mock(return_value={'status': 'success'}),
                                   generate_worker=Mock(), restretch_line=Mock(return_value={'status': 'success'}), remix_with_offsets=Mock()),
                 inworld_service=NS(instruction_tag=lambda emotion: '' if emotion == 'neutral' else '[speak softly] '))
        names = ['_resolve_short_voices', '_short_quote', '_quote_response', 'generate_quote', 'regenerate_quote',
                 '_check_short_payment', '_run_short_generate', '_short_edit', 'generate', 'regenerate_line',
                 'restretch_line', 'remix_audio', 'generate_progress']
        self.n = source_functions('main.py', names, n)

    def tearDown(self):
        finish_operation(JOB_A)

    def call_regenerate(self, **kwargs):
        req = request(**kwargs)
        if req.accepted_credits is None:
            req.accepted_credits = self.n['_short_quote'](req, True)['credits']
        return self.n['regenerate_line'](req, object())

    def test_quote_has_real_prompt_tags_and_no_usage_side_effects(self):
        req = request(segments=[segment('x' * 51)])  # [neutral] + space = 10 chars
        q = self.n['generate_quote'](req, object())
        self.assertEqual(q['credits'], 2)
        self.assertNotIn('ai_snapshot', q)
        self.n['deduct_credits'].assert_not_called()
        self.n['eleven_service'].generate_worker.assert_not_called()

    def test_client_cannot_select_cheaper_voice_engine(self):
        req = request(segments=[segment('x' * 61)], speaker_voice_engines={'Speaker 1': 'inworld'})
        self.assertEqual(self.n['_short_quote'](req)['credits'], 2)
        self.n['_voice_engines_for_ids'].return_value = {'voice': 'inworld'}
        self.assertEqual(self.n['_short_quote'](req)['credits'], 1)

    def test_regeneration_authentication_precedes_provider(self):
        self.n['_paid_uid'].return_value = (None, Response({'error': 'Sign in'}, 401))
        self.assertEqual(self.call_regenerate().status_code, 401)
        self.n['eleven_service'].regenerate_line.assert_not_called()

    def test_quote_and_regeneration_reject_foreign_job(self):
        self.n['_job_guard'].return_value = Response({'error': 'not found'}, 404)
        self.assertEqual(self.n['generate_quote'](request(), object()).status_code, 404)
        self.assertEqual(self.call_regenerate().status_code, 404)
        self.n['deduct_credits'].assert_not_called()

    def test_balance_outage_insufficient_funds_and_missing_price_start_no_tts(self):
        for balance, accepted, status in ((None, 1, 503), (0, 1, 402), (100, -1, 409)):
            self.n['get_credits'].return_value = balance
            self.assertEqual(self.call_regenerate(accepted_credits=accepted).status_code, status)
        self.n['eleven_service'].regenerate_line.assert_not_called()
        self.n['deduct_credits'].assert_not_called()

    def test_regeneration_charges_success_once_at_configured_rate(self):
        req = request(segment=segment('x' * 51))
        req.accepted_credits = 2
        result = self.n['regenerate_line'](req, object())
        self.assertEqual(result['credits_charged'], 2)
        self.assertEqual(self.charges[JOB_A]['credits_charged'], 2)
        self.n['deduct_credits'].assert_called_once_with('user', 2, 'regenerate', JOB_A)
        self.assertFalse(operation_active(JOB_A))

    def test_provider_error_does_not_charge_and_unlocks_job(self):
        self.n['eleven_service'].regenerate_line.return_value = {'error': 'provider failed'}
        self.assertIn('error', self.call_regenerate())
        self.n['deduct_credits'].assert_not_called()
        self.assertFalse(operation_active(JOB_A))

    def test_debit_failure_is_not_reported_as_paid_success(self):
        self.n['deduct_credits'].return_value = None
        self.assertEqual(self.call_regenerate().status_code, 503)

    def test_busy_job_preserves_original_progress_and_starts_no_thread(self):
        begin_operation(JOB_A)
        self.progress[f'generate_{JOB_A}'] = {'status': 'processing', 'percent': 55}
        req = request(accepted_credits=1)
        self.assertEqual(self.n['generate'](req, object()).status_code, 409)
        self.assertEqual(self.progress[f'generate_{JOB_A}']['percent'], 55)
        self.n['threading'].Thread.assert_not_called()

    def test_generation_missing_confirmation_and_balance_outage_start_no_thread(self):
        self.assertEqual(self.n['generate'](request(), object()).status_code, 409)
        self.n['get_credits'].return_value = None
        self.assertEqual(self.n['generate'](request(accepted_credits=1), object()).status_code, 503)
        self.n['threading'].Thread.assert_not_called()

    def test_fixed_generation_charges_quote_not_cumulative_bucket(self):
        req = request()
        self.buckets[JOB_A] = {'gemini_out': 4000, 'eleven_chars': 99000}
        quote = self.n['_short_quote'](req)
        def worker(_):
            self.progress[f'generate_{JOB_A}'] = {'status': 'done', 'result': {'final_duration': 4}}
        self.n['eleven_service'].generate_worker.side_effect = worker
        begin_operation(JOB_A)
        self.n['_run_short_generate'](req, 'user', quote)
        self.n['deduct_credits'].assert_called_once_with('user', quote['credits'], 'generate', JOB_A, 4)
        self.assertEqual(self.n['_short_quote'](req)['analysis_credits'], 0)
        self.assertFalse(operation_active(JOB_A))

    def test_failed_generation_debit_keeps_analysis_unsettled(self):
        self.n['deduct_credits'].return_value = None
        self.n['eleven_service'].generate_worker.side_effect = lambda _: self.progress.update({f'generate_{JOB_A}': {'status': 'done'}})
        self.buckets[JOB_A] = {'gemini_out': 4000}
        self.n['_run_short_generate'](request(), 'user', self.n['_short_quote'](request()))
        self.assertEqual(self.progress[f'generate_{JOB_A}']['status'], 'error')
        self.assertNotIn('shortdub_ai_settled', self.buckets[JOB_A])
        self.assertNotIn(JOB_A, self.charges)

    def test_progress_waits_until_billing_finishes(self):
        self.progress[f'generate_{JOB_A}'] = {'status': 'done'}
        begin_operation(JOB_A)
        self.assertEqual(self.n['generate_progress'](object(), JOB_A)['status'], 'processing')
        finish_operation(JOB_A)
        self.assertEqual(self.n['generate_progress'](object(), JOB_A)['status'], 'done')

    def test_free_edits_remain_free_and_use_nonempty_guard(self):
        self.n['restretch_line'](request(), object())
        self.n['_job_guard'].assert_called_with(unittest.mock.ANY, JOB_A, allow_empty=False)
        self.n['deduct_credits'].assert_not_called()

    def test_duplicate_segment_ids_cannot_overwrite_line_audio(self):
        with self.assertRaises(ValueError):
            self.n['_short_quote'](request(segments=[segment(), segment()]))


class AudioWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.progress, self.buckets, self.commands = {}, {}, []
        def ffmpeg(command):
            self.commands.append(command)
            Path(command[-1]).write_bytes(b'mocked ffmpeg audio')
        n = dict(OUTPUT_DIR=self.output, Path=Path, jobs_progress=self.progress,
                 usage_bucket=lambda job: self.buckets.setdefault(job, {'eleven_chars': 0, 'inworld_chars': 0}),
                 USER_GAINS={}, ROOM_SETTINGS={}, ROOM_LAST={}, eleven_client=NS(text_to_speech=NS(convert=Mock(return_value=b'provider audio'))),
                 TTS_MODEL_ID='test', line_audio_path=line_audio_path, clear_line_audio=clear_line_audio,
                 run_ffmpeg=ffmpeg, get_media_duration=lambda path: 2., resolve_job_speech=lambda _: None,
                 job_speech_spans=lambda _: [], measure_loudness_db=lambda _: None, _apply_room=lambda *a: {},
                 _measure_line_loudness=lambda *a: None, _mix_filter_part=lambda *a: 'mock_filter',
                 friendly_error=str, UserError=ValueError, inworld_service=NS(instruction_tag=lambda _: '', synthesize=Mock(return_value=b'inworld audio')))
        self.n = source_functions('eleven_service.py', ['_emotion_tags', 'generate_worker', 'rebuild_final_mix', 'regenerate_line', 'restretch_line', 'remix_with_offsets'], n)

    def test_two_concurrent_jobs_keep_files_flags_and_mix_inputs_separate(self):
        barrier = threading.Barrier(2)
        self.n['eleven_client'].text_to_speech.convert.side_effect = lambda **kw: (barrier.wait(timeout=5), kw['voice_id'].encode())[1]
        requests = [request(job_id=JOB_A, speaker_voices={'Speaker 1': 'A'}, dead_space_allowed={'seg_0': True}),
                    request(job_id=JOB_B, speaker_voices={'Speaker 1': 'B'}, dead_space_allowed={})]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(self.n['generate_worker'], requests))
        a, b = (self.progress[f'generate_{job}'] for job in (JOB_A, JOB_B))
        self.assertEqual(a['status'], 'done', a)
        self.assertEqual(b['status'], 'done', b)
        self.assertEqual(a['result']['duration_cuts'], 0)
        self.assertEqual(b['result']['duration_cuts'], 1)
        for job, audio in ((JOB_A, b'A'), (JOB_B, b'B')):
            self.assertEqual(line_audio_path(self.output, job, 'seg_0', 'raw', '.mp3').read_bytes(), audio)
        final_commands = [c for c in self.commands if c[-1].endswith('_final_dubbed.mp3')]
        for command in final_commands:
            other = JOB_B if JOB_A in command[-1] else JOB_A
            self.assertFalse(any(other in arg for arg in command))

    def test_rebuild_and_preview_paths_never_fall_back_to_legacy_audio(self):
        (self.output / 'seg_0_stretched.wav').write_bytes(b'wrong user')
        with self.assertRaisesRegex(Exception, 'No generated line'):
            self.n['rebuild_final_mix']([segment()], 4, job_id=JOB_A)
        self.assertFalse(self.commands)

    def test_regeneration_and_restretch_reuse_only_current_job_files(self):
        self.n['generate_worker'](request(job_id=JOB_B))
        other = line_audio_path(self.output, JOB_B, 'seg_0', 'raw', '.mp3')
        original = other.read_bytes()
        result = self.n['regenerate_line'](request(elevenlabs_api_key='test'))
        self.assertEqual(result['status'], 'success', result)
        result = self.n['restretch_line'](request())
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(other.read_bytes(), original)

    def test_inworld_generation_uses_scoped_audio_and_correct_counter(self):
        req = request(speaker_voice_engines={'Speaker 1': 'inworld'})
        self.n['generate_worker'](req)
        self.assertEqual(self.progress[f'generate_{JOB_A}']['status'], 'done')
        self.assertEqual(line_audio_path(self.output, JOB_A, 'seg_0', 'raw', '.mp3').read_bytes(), b'inworld audio')
        self.assertEqual(self.buckets[JOB_A]['inworld_chars'], len(req.segment.arabic_text))
        self.assertEqual(self.buckets[JOB_A]['eleven_chars'], 0)


class HttpRouteTests(unittest.TestCase):
    """Real FastAPI request parsing/routing, using the same offline mocks."""
    def setUp(self):
        try:
            from fastapi import FastAPI, Request
            from fastapi.responses import JSONResponse, FileResponse
            from fastapi.testclient import TestClient
            from pydantic import BaseModel, field_validator
            from models import Segment
        except ImportError:
            self.skipTest('FastAPI/httpx dependencies required for HTTP integration checks')
        RouteTests.setUp(self)
        n = self.n
        n.update(__name__=__name__, Request=Request, JSONResponse=JSONResponse, FileResponse=FileResponse,
                 BaseModel=BaseModel, field_validator=field_validator, Segment=Segment,
                 Dict=Dict, List=List, Optional=Optional, _JOB_ID_RE=re.compile(r'[A-Za-z0-9_-]{20,100}'))
        source_functions('main.py', ['_bad_segment_id', 'segment_audio'], n)
        tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8-sig'))
        classes = [x for x in tree.body if isinstance(x, ast.ClassDef) and x.name in ('_JobIdModel', 'GenerateRequest', 'RegenerateLineRequest')]
        exec(compile(ast.Module(body=classes, type_ignores=[]), 'main.py', 'exec'), n)
        app = FastAPI()
        for url, name in (('/api/generate/quote', 'generate_quote'), ('/api/regenerate_line/quote', 'regenerate_quote'),
                          ('/api/generate', 'generate'), ('/api/regenerate_line', 'regenerate_line')):
            app.add_api_route(url, n[name], methods=['POST'])
        app.add_api_route('/api/segment_audio/{job_id}/{segment_id}', n['segment_audio'], methods=['GET'])
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.payload = dict(job_id=JOB_A, segments=[dict(segment_id='seg_0', start=0, end=1, arabic_text='مرحبا')], speaker_voices={'Speaker 1': 'voice'})

    def tearDown(self):
        finish_operation(JOB_A)

    def test_http_quote_parses_arabic_and_generation_requires_confirmed_price(self):
        response = self.client.post('/api/generate/quote', json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        price = response.json()['credits']
        self.assertEqual(self.client.post('/api/generate', json=self.payload).status_code, 409)
        self.payload['accepted_credits'] = price
        response = self.client.post('/api/generate', json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'started')

    def test_http_bad_job_and_duplicate_lines_are_rejected(self):
        self.payload['job_id'] = '../wrong'
        self.assertEqual(self.client.post('/api/generate/quote', json=self.payload).status_code, 422)
        self.payload['job_id'] = JOB_A
        self.payload['segments'] *= 2
        self.assertEqual(self.client.post('/api/generate/quote', json=self.payload).status_code, 400)

    def test_http_regeneration_charge_is_in_response(self):
        payload = dict(job_id=JOB_A, segment=self.payload['segments'][0], voice_id='voice')
        quote = self.client.post('/api/regenerate_line/quote', json=payload)
        self.assertEqual(quote.status_code, 200, quote.text)
        payload['accepted_credits'] = quote.json()['credits']
        response = self.client.post('/api/regenerate_line', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['credits_charged'], 1)
        self.n['deduct_credits'].assert_called_once()

    def test_http_preview_returns_only_the_requested_job_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            self.n.update(OUTPUT_DIR=Path(directory), line_audio_path=line_audio_path)
            for job, data in ((JOB_A, b'audio A'), (JOB_B, b'audio B')):
                line_audio_path(directory, job, 'seg_0', 'stretched', '.wav').write_bytes(data)
            (Path(directory) / 'seg_0_stretched.wav').write_bytes(b'legacy private audio')
            response = self.client.get(f'/api/segment_audio/{JOB_A}/seg_0')
            self.assertEqual(response.content, b'audio A')
            self.assertEqual(response.headers['cache-control'], 'no-store')
            clear_line_audio(directory, JOB_A)
            self.assertEqual(self.client.get(f'/api/segment_audio/{JOB_A}/seg_0').status_code, 404)
            self.assertEqual(self.client.get(f'/api/segment_audio/{JOB_B}/seg_0').content, b'audio B')


if __name__ == '__main__':
    unittest.main()
