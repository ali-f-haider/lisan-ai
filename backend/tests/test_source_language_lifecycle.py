"""Persisted-project and downstream propagation checks using invented records."""
import copy
import json
import math
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid
import lang_source_capture as capture
import image_unit
import languages
import source_jobs


class LongLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.scope=capture.source_namespace('longdub_service.py',('init_upload','public_view','redo_project','switch_source_language'))
        self.scope.update(image_unit=image_unit,Path=Path,math=math,uuid=uuid,shutil=shutil,DATA_DIR=self.root,
            ALLOWED_EXTS={'.mp4'},VIDEO_EXTS={'.mp4'},MAX_SPEAKERS=20,MAX_UPLOAD_BYTES=2**31,
            MAX_PROJECTS_PER_USER=100,MAX_ACTIVE_PER_USER=100,CHUNK_BYTES=1000,NAME_MAX=80,
            _LOCK=threading.RLock(),_JOBS={},_RUNNING=set(),_save=Mock(),_ev=Mock(),_now=lambda:123.,
            project_count=lambda uid:0,active_count=lambda uid:0,clean_project_text=lambda n,d:(n,d),
            lipsync_available=lambda:False,_lip_res=lambda r:r or '720p',job_dir=lambda jid:self.root/jid,
            _lock_for=lambda jid:threading.RLock(),_wd=lambda job:self.root/job['id'],start_worker=Mock(),
            PUBLIC_FIELDS=('id','status'),project_name=lambda job:job.get('name') or 'invented',
            has_media=lambda job:bool(job.get('media_present')),read_segments=lambda job:[{'segment_id':'a','text':'Invented sentence.','arabic_text':'جملة.'}],
            list_jobs_for_uid=lambda uid:[],_redo_name=lambda uid,n:n+' copy',_write_segments=Mock())

    def make(self,code='en'):
        with patch.object(shutil,'disk_usage',return_value=type('D',(),{'free':2**40})()):
            job,error=self.scope['init_upload']('invented-owner','invented.mp4',2500,source_language=code)
        self.assertIsNone(error)
        return job

    def test_source_persists_at_creation_and_public_view(self):
        for code in ('en','es','fr','pt','it','de','nl','pl'):
            job=self.make(code)
            self.assertEqual(job['source_language'],code)
            self.assertEqual(self.scope['public_view'](job)['source_language'],code)
            self.scope['_ev'].assert_any_call(job,'language_set','ok',code)

    def test_legacy_view_defaults_without_mutating_the_old_job(self):
        job={'id':'old','status':'editing','name':'invented'}
        before=copy.deepcopy(job)
        self.assertEqual(self.scope['public_view'](job)['source_language'],'en')
        self.assertEqual(job,before)

    def test_redo_inherits_source_and_legacy_defaults(self):
        for code in ('en','es','fr','pt','it','de','nl','pl',None):
            job=self.make(code); job['status']='done'
            if code is None: job.pop('source_language',None)
            ok,new=self.scope['redo_project'](job,'invented-owner')
            self.assertTrue(ok,new)
            self.assertEqual(new['source_language'],code or 'en')
            self.assertEqual(new['paid'],{'fee':0,'analysis':0,'dub':0})

    def test_switch_keeps_audio_and_paid_records_invalidates_only_recognition(self):
        job=self.make(); job.update(status='language_mismatch',paid={'fee':3,'analysis':20,'dub':0},
            analysis={'asr_done':[0],'sep_done':[0],'diarized':True},language_mismatch={'detected':'fr'})
        wd=self.root/job['id']; (wd/'asr').mkdir()
        (wd/'asr'/'piece.json').write_text('{}'); (wd/'voice.wav').write_bytes(b'invented')
        before=copy.deepcopy(job['paid'])
        ok,error=self.scope['switch_source_language'](job,'fr')
        self.assertTrue(ok,error)
        self.assertEqual(job['source_language'],'fr'); self.assertEqual(job['status'],'accepted')
        self.assertEqual(job['paid'],before)
        self.assertEqual(job['analysis'],{'asr_done':[],'sep_done':[0],'diarized':True})
        self.assertTrue((wd/'voice.wav').exists()); self.assertFalse((wd/'asr'/'piece.json').exists())
        self.scope['start_worker'].assert_called_once_with(job['id'])

    def test_switch_rejects_running_unknown_and_not_paused(self):
        job=self.make()
        for code in ('xx','fr'):
            ok,error=self.scope['switch_source_language'](job,code)
            self.assertFalse(ok)
        job['status']='language_mismatch'; self.scope['_RUNNING'].add(job['id'])
        self.assertFalse(self.scope['switch_source_language'](job,'fr')[0])
        self.scope['start_worker'].assert_not_called()


class DownstreamLanguageTests(unittest.TestCase):
    def test_short_scene_uses_saved_source_without_changing_legacy_caller(self):
        import config, scene_context, gemini_service
        with tempfile.TemporaryDirectory() as td:
            jid=str(uuid.uuid4()); source_jobs.set_language(td,jid,'fr')
            rows=[{'start':0.,'end':10.,'text':"Une personne ouvre la porte et entre dans la maison."}]
            with patch.object(config,'UPLOAD_DIR',Path(td)),patch.object(scene_context,'ENABLED',True), \
                 patch.object(gemini_service,'call_gemini',return_value=(None,'invented failure')) as call:
                scene_context.scenes_for_job(jid,td,rows,'offline-key')
            prompt=call.call_args.args[1]['contents'][0]['parts'][0]['text']
            self.assertEqual(prompt,scene_context._prompt(10.,False,'fr'))
            self.assertNotEqual(prompt,scene_context._prompt(10.,False,'en'))

    def test_clone_retries_keep_the_chosen_sample_language(self):
        import inworld_service
        scope=capture.source_namespace('longdub_service.py',('_clone_with_retry',))
        scope.update(INWORLD_API_KEY='offline',time=Mock())
        for code in ('es','fr','pt','it','de','nl','pl'):
            with patch.object(inworld_service,'clone_voice_from_file',side_effect=ValueError('language refused')) as call, \
                 patch.object(inworld_service,'_http_error_detail',return_value='language refused'):
                result,error=scope['_clone_with_retry']('invented',Path('invented.wav'),code)
            self.assertIsNone(result)
            self.assertEqual([c.kwargs['language_code'] for c in call.call_args_list],[code,code,code])

    def test_clone_event_reports_the_actual_original_language(self):
        import ast, inworld_service
        node=copy.deepcopy(next(n for n in ast.walk(capture.tree('longdub_service.py'))
            if isinstance(n,ast.FunctionDef) and n.name=='_make_voice'))
        node.decorator_list=[]
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for code in ('en','es','fr','pt','it','de','nl','pl'):
                wav=root/'invented.wav'; wav.write_bytes(b'local test reference')
                job={'id':'invented','source_language':code}
                event=Mock(); clone=Mock(return_value=('invented-voice',None))
                scope={'languages':languages,'inworld_service':inworld_service,'shutil':shutil,
                    'job':job,'rows_all':[],'d':root,'dub':{'voices':{},'fallback':{}},
                    'names':{'speaker':'Invented speaker'},'job_dir':lambda jid:root,
                    '_clone_sample':lambda *args:(wav,5.),'_clone_with_retry':clone,
                    '_save':Mock(),'_ev':event}
                exec(compile(ast.Module([node],type_ignores=[]),'offline','exec'),scope)
                scope['_make_voice']('speaker',1.)
                expected=inworld_service.CLONE_SAMPLE_LANGUAGE if code=='en' else code
                self.assertIn("voice labelled '"+expected+"'",event.call_args.args[3])
                self.assertEqual(clone.call_args.kwargs,{} if code=='en' else {'source_language':code})

    def test_subtitle_export_remains_original_text_for_each_language(self):
        import ast, subs_export
        from fastapi.responses import Response, JSONResponse
        node=copy.deepcopy(next(n for n in capture.tree('main.py').body if isinstance(n,ast.FunctionDef) and n.name=='longdub_subtitles'))
        node.decorator_list=[]
        scope={'Request':object,'Path':Path,'languages':languages,'subs_export':subs_export,
               'Response':Response,'JSONResponse':JSONResponse}
        exec(compile(ast.Module([node],type_ignores=[]),'offline','exec'),scope)
        for code,text in [('en','A clear day.'),('es','Un día claro.'),('fr',"L’été arrive."),
                          ('pt','Um dia claro.'),('it','Un giorno chiaro.'),('de','Ein schöner Tag.')]:
            job={'status':'done','filename':'invented.mp4','source_language':code}
            scope['_ld_job']=lambda request,jid:('invented-owner',job,None)
            scope['longdub_service']=type('LD',(),{'read_segments':staticmethod(lambda job:[
                {'start':0.,'end':3.,'text':text,'arabic_text':'صباح صاف.'}])})()
            result=scope['longdub_subtitles']('invented',None,code,'srt')
            self.assertEqual(result.status_code,200)
            self.assertIn(text,result.body.decode('utf-8-sig'))
            self.assertIn('invented_'+code+'.srt',result.headers['content-disposition'])
            self.assertNotIn('صباح',result.body.decode('utf-8-sig'))
            if code!='en':
                self.assertEqual(scope['longdub_subtitles']('invented',None,'en','srt').status_code,400)



if __name__=='__main__':
    unittest.main()
