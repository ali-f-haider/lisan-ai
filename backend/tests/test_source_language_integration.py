"""Offline integration proofs. Invented speech; no generation or paid calls."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_jobs
import languages
import lang_check
import lang_source_capture as capture
import source_jobs
import source_rows
import subs_align

CODES = tuple(l.code for l in languages.supported())


class DurableLanguageTests(unittest.TestCase):
    def test_old_missing_corrupt_unknown_are_english(self):
        with tempfile.TemporaryDirectory() as td:
            jid = str(uuid.uuid4())
            self.assertEqual(source_jobs.language(td, jid), 'en')
            p = source_jobs._path(td, jid, 'source_language')
            for contents in ('{', 'null', '[1]', '{"source_language":"xx"}'):
                p.write_text(contents)
                self.assertEqual(source_jobs.language(td, jid), 'en')

    def test_all_languages_persist_independently_of_progress(self):
        with tempfile.TemporaryDirectory() as td:
            for code in CODES:
                jid = str(uuid.uuid4())
                self.assertEqual(source_jobs.set_language(td, jid, ' '+code.upper()+' '), code)
                self.assertEqual(source_jobs.language(td, jid), code)
                self.assertEqual(json.loads(source_jobs._path(td, jid, 'source_language').read_text())['source_language'], code)
            self.assertEqual(list(Path(td).glob('*.tmp')), [])

    def test_unknown_or_unsafe_write_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            for code in ('xx', '', None):
                with self.assertRaises(ValueError):
                    source_jobs.set_language(td, str(uuid.uuid4()), code)
            with self.assertRaises(ValueError):
                source_jobs.set_language(td, '../escape', 'fr')
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_retry_cannot_read_a_file_outside_uploads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)/'uploads'; root.mkdir()
            inside = root/'audio.wav'; inside.write_bytes(b'invented')
            outside = Path(td)/'other.wav'; outside.write_bytes(b'invented')
            jid = str(uuid.uuid4())
            prepared = {'input_path': str(inside), 'audio_path': str(inside), 'background_path': str(outside)}
            source_jobs.save_retry(root, jid, prepared)
            self.assertIsNone(source_jobs.retry(root, jid))
            prepared['background_path'] = None
            source_jobs.save_retry(root, jid, prepared)
            self.assertEqual(source_jobs.retry(root, jid), prepared)
            source_jobs.clear_retry(root, jid)
            self.assertIsNone(source_jobs.retry(root, jid))


class MismatchTests(unittest.TestCase):
    def test_matching_language_never_pauses_for_any_choice(self):
        for code in CODES:
            self.assertIsNone(lang_check.mismatch([{'lang': code, 'prob': .99, 'speech': 12}], code))

    def test_dominant_supported_difference_requires_explicit_switch(self):
        result = lang_check.mismatch([{'lang': 'fr', 'prob': .99, 'speech': 12}], 'en')
        self.assertEqual((result['expected'], result['detected'], result['supported']), ('en', 'fr', True))

    def test_minority_uncertain_short_and_invalid_evidence_do_not_pause(self):
        for evidence in ([], [{'lang':'fr','prob':.99,'speech':3}], [{'lang':'fr','prob':.2,'speech':12}],
                         [{'lang':'fr','prob':.99,'speech':8}, {'lang':'en','prob':.99,'speech':30}],
                         [{'lang':'fr','prob':float('nan'),'speech':12}], [None, {'speech':'bad'}]):
            self.assertIsNone(lang_check.mismatch(evidence, 'en'))

    def test_unsupported_detected_language_is_not_silently_normalised_to_english(self):
        result = lang_check.mismatch([{'lang':'ar','prob':.99,'speech':12}], 'fr')
        self.assertEqual(result['detected'], 'ar')
        self.assertFalse(result['supported'])

    def test_public_poll_has_only_allowlisted_mismatch_metadata(self):
        job = {'id':'invented','status':'language_mismatch','source_language':'fr',
               'language_mismatch':{'detected':'es','secret':'not-public','message':'internal'}}
        public = api_jobs.job_view(job, 'r', False)
        self.assertEqual(public['source_language'], 'fr')
        self.assertEqual(public['status'], 'language_mismatch')
        self.assertEqual(public['language_mismatch'], {'detected_language':'es','switch_available':True})
        job['language_mismatch']['detected'] = 'ar'
        self.assertIsNone(api_jobs.job_view(job, 'r', False)['language_mismatch']['detected_language'])
        job['status'] = 'editing'
        self.assertIsNone(api_jobs.job_view(job, 'r', False)['language_mismatch'])


class TimedRowsTests(unittest.TestCase):
    def test_span_and_original_words_survive_sentence_cuts(self):
        for code, words in [('es',['¿Puedes','venir?','Sí.']), ('fr',["L'ami",'vient.','Merci.']),
                            ('de',['Das','ist','z.','B.','gut.','Weiter.'])]:
            row = {'segment_id':'seg_0','speaker':'A','text':' '.join(words), 'start':0.,'end':len(words),
                   'words':[{'word':w,'start':i+.1,'end':i+.8} for i,w in enumerate(words)]}
            original = copy.deepcopy(row)
            parts = source_rows.split_timed_row(row, code)
            self.assertEqual([w for p in parts for w in p['words']], original['words'])
            self.assertEqual(row, original)
            self.assertEqual(parts[0]['start'], .1)
            self.assertEqual(parts[-1]['end'], len(words)-.2)
            if code == 'de':
                self.assertEqual(parts[0]['text'], 'Das ist z. B. gut.')

    def test_duration_limit_uses_measured_word_times(self):
        row = {'words':[{'word':'hola','start':0.,'end':1.}, {'word':'otra','start':16.,'end':17.}]}
        parts = source_rows.split_timed_row(row, 'es')
        self.assertEqual([(p['start'],p['end']) for p in parts], [(0.,1.),(16.,17.)])


class PromptAndSubtitleTests(unittest.TestCase):
    def test_original_english_prompt_bytes_are_pinned(self):
        cases = json.loads((capture.ROOT/'tests/fixtures/lang/english_prompt_hashes.json').read_text(encoding='utf-8'))
        self.assertEqual(len(cases), 12)
        for case in cases:
            segs = [NS(segment_id='seg_0', start=0., end=3., speaker='A', text=case['text'])]
            prompt = capture.translation(segs, gender=case['gender'])
            self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(), case['sha256'])

    def test_foreign_subtitle_restores_accents_and_apostrophes(self):
        text = "L'homme a fermé la porte."
        rows = [{'segment_id':'a','speaker':'A','start':0.,'end':4.,'text':"L homme a ferme la porte."}]
        srt = '1\n00:00:00,000 --> 00:00:04,000\n'+text+'\n'
        changed, report = subs_align.correct_rows(rows, srt, 'invented.srt', source_language='fr')
        self.assertTrue(report['ok'], report)
        self.assertEqual(changed[0]['text'], text)
        self.assertEqual(changed[0]['segment_id'], 'a')

    def test_subtitle_in_another_language_leaves_original_rows_unchanged(self):
        rows = [{'segment_id':'a','speaker':'A','start':0.,'end':4.,'text':'La puerta está abierta para todos.'}]
        original = copy.deepcopy(rows)
        srt = '1\n00:00:00,000 --> 00:00:04,000\nThe other window is closed now.\n'
        changed, report = subs_align.correct_rows(rows, srt, 'invented.srt', source_language='es')
        self.assertFalse(report['ok'])
        self.assertEqual(changed, original)
        self.assertEqual(rows, original)

    def test_foreign_error_names_no_specific_wrong_source_language(self):
        for code in CODES[1:]:
            for reason in ('arabic','no_match'):
                message = subs_align.reason_message(reason, code)
                self.assertNotIn('English', message)
                self.assertIn('same language', message)


class ShortWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = capture.source_namespace('whisper_service.py', ('transcribe_worker',))
        self.model = Mock()
        words = [NS(word=w,start=float(i),end=i+.8) for i,w in enumerate(['Hola.','Otra','frase.'])]
        self.model.transcribe.return_value = (iter([NS(start=0.,end=3.,text='Hola. Otra frase.',words=words)]),NS(duration=3.))
        def split(seg, **kwargs):
            # Force the later timed-row branch to cut a row, to prove IDs are unique.
            return [{'start':seg.start,'end':seg.end,'text':seg.text}]
        def extract(src, dst):
            Path(dst).write_bytes(b'invented')
        def separate(src, dest):
            voice, bed = self.root/'voice.wav', self.root/'bed.wav'
            voice.write_bytes(b'invented'); bed.write_bytes(b'invented')
            return str(voice), str(bed)
        self.scope.update(Path=Path,UPLOAD_DIR=self.root,jobs_progress={},app_state=NS(),
            _transcribe_queue=Mock(),_get_model=lambda:self.model,_release_model=Mock(),
            _release_diarization_pipeline=Mock(),_trim_memory=Mock(),_fadvise_dontneed=Mock(),
            extract_audio_from_video=Mock(side_effect=extract),separate_vocals=Mock(side_effect=separate),
            get_speaker_turns=Mock(return_value=[]),threading=threading,time=time,torch=Mock(),traceback=traceback,
            friendly_error=lambda e,*a:str(e),split_segment=split,source_rows=source_rows,
            smooth_speaker_islands=lambda rows,*args:rows,merge_mid_sentence_rows=lambda rows,*args:rows,
            detect_silence_gaps=lambda p:[],run_vad_timing_checks=Mock())
        self.worker = self.scope['transcribe_worker']

    def test_actual_worker_passes_each_chosen_code_to_recognition(self):
        for code in CODES:
            jid = str(uuid.uuid4()); src = self.root/'clip.wav'; src.write_bytes(b'invented')
            self.setUpModel()
            with patch.object(lang_check,'judge_file',return_value=[{'lang':code,'prob':.99,'speech':12}]):
                self.worker(jid,str(src),'',0,source_language=code)
            result = self.scope['jobs_progress'][jid]
            self.assertEqual(result['status'], 'done', result)
            self.assertEqual(self.model.transcribe.call_args.kwargs['language'], code)
            self.assertEqual(result['source_language'], code)
            ids = [r['segment_id'] for r in result['segments']]
            self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(self.scope['_transcribe_queue'].release.call_count,6)

    def setUpModel(self):
        words = [NS(word=w,start=float(i),end=i+.8) for i,w in enumerate(['Hola.','Otra','frase.'])]
        self.model.transcribe.return_value = (iter([NS(start=0.,end=3.,text='Hola. Otra frase.',words=words)]),NS(duration=3.))

    def test_mismatch_pauses_before_decode_and_retry_reuses_separation(self):
        jid = str(uuid.uuid4()); src = self.root/'clip.mp4'; src.write_bytes(b'invented')
        evidence = [{'lang':'es','prob':.99,'speech':12}]
        with patch.object(lang_check,'judge_file',return_value=evidence):
            self.worker(jid,str(src),'',0,source_language='en')
            self.assertEqual(self.scope['jobs_progress'][jid]['status'],'language_mismatch')
            self.model.transcribe.assert_not_called()
            prepared = source_jobs.retry(self.root,jid)
            self.assertIsNotNone(prepared)
            self.worker(jid,str(src),'',0,source_language='es',prepared=prepared)
        self.assertEqual(self.scope['jobs_progress'][jid]['status'],'done')
        self.assertEqual(self.scope['extract_audio_from_video'].call_count,1)
        self.assertEqual(self.scope['separate_vocals'].call_count,1)
        self.assertEqual(self.scope['_release_model'].call_count,2)
        self.assertEqual(self.scope['_transcribe_queue'].release.call_count,2)
        self.assertIsNone(source_jobs.retry(self.root,jid))

    def test_cache_write_failure_cannot_bypass_confirmed_mismatch(self):
        jid = str(uuid.uuid4()); src = self.root/'clip.wav'; src.write_bytes(b'invented')
        with patch.object(lang_check,'judge_file',return_value=[{'lang':'es','prob':.99,'speech':12}]), \
             patch.object(source_jobs,'save_retry',side_effect=OSError('invented disk failure')):
            self.worker(jid,str(src),'',0,source_language='en')
        self.assertEqual(self.scope['jobs_progress'][jid]['status'],'error')
        self.model.transcribe.assert_not_called()
        self.scope['_release_model'].assert_called_once()
        self.scope['_transcribe_queue'].release.assert_called_once()


if __name__ == '__main__':
    unittest.main()
