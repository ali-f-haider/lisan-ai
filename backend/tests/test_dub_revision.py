"""Offline state/billing checks and real audio checks. Never imports main or contacts providers."""
import ast, copy, json, math, os, re, shutil, sys, tempfile, threading, unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock, patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dub_review, dub_audio
import numpy as np

def extract(file, names, env):
    from user_errors import customer_message, customer_payload
    env.setdefault('customer_message', customer_message)
    env.setdefault('customer_payload', customer_payload)
    tree = ast.parse((ROOT/file).read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    for n in nodes: n.decorator_list=[]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),file,'exec'),env)
    return env

class ReviewTests(unittest.TestCase):
    def test_contained_overlaps_and_touching_edges(self):
        r=[dict(segment_id='a',start=1,end=10),dict(segment_id='b',start=2,end=3),dict(segment_id='c',start=4,end=5),dict(segment_id='d',start=10,end=11)]
        self.assertEqual(dub_review.overlaps(r),{'a':['b','c'],'b':['a'],'c':['a']})
    def test_review_invalidation(self):
        r=[dict(segment_id=str(i),start=i,end=i+.5,text='original') for i in range(23)]
        marks={str(b['page']):b['hash'] for b in dub_review.batches(r)}
        r[12]['text']='changed'
        self.assertEqual([b['reviewed'] for b in dub_review.batches(r,marks)],[True,False,True])
        r.insert(1,dict(segment_id='new',start=1,end=1.1))
        self.assertFalse(any(b['reviewed'] for b in dub_review.batches(r,marks)))
    def test_hour_budget_includes_all_tracks_and_references(self):
        self.assertGreater(dub_review.output_budget(1400000000,3600,True,True),1400000000+3*3600*24000)
    def test_text_emotion_is_not_verified(self):
        self.assertEqual(dub_review.emotion_review({'emotion':'angry'}),'listen_and_review')
        self.assertEqual(dub_review.emotion_review({'emotion_set':True}),'confirmed')

class AccountTests(unittest.TestCase):
    def test_storage_history_reads_past_the_first_thousand_spends(self):
        import io,urllib.request
        pages=[[dict(job_id='new'+str(i),created_at='2026') for i in range(1000)],[dict(job_id='older-file',created_at='2025')]]
        env=dict(SUPABASE_URL='https://fixture.invalid',SUPABASE_SERVICE_KEY='fake',json=json)
        extract('main.py',['_user_job_ids'],env)
        with patch.object(urllib.request,'urlopen',side_effect=[io.BytesIO(json.dumps(page).encode()) for page in pages]) as fetch:
            result=env['_user_job_ids']('user')
        self.assertIn('older-file',result);self.assertEqual(fetch.call_count,2)
        self.assertIn('offset=1000',fetch.call_args_list[1].args[0].full_url)
    def test_unconfirmed_refund_does_not_write_a_refund_receipt(self):
        def rpc(function,args,strict=False):
            if strict:raise RuntimeError('mock database outage')
            return None
        record=Mock();env=dict(_sb_rpc=rpc,_record_spend=record)
        extract('main.py',['_ld_refund'],env)
        self.assertIsNone(env['_ld_refund']('uid',10,'owned'))   # not confirmed: the caller keeps the pending charge
        record.assert_not_called()
    def test_failed_merge_refunds_repair_and_unlocks(self):
        refund,unlock=Mock(),Mock()
        price={'max_total':11,'music_charged':10}
        env=dict(_merge_say=Mock(),MergeRequest=NS,Request=NS,_job_guard=Mock(return_value=None),_paid_uid=Mock(return_value=('uid',None)),begin_operation=Mock(return_value=True),finish_operation=unlock,_short_merge_price=Mock(return_value=price),_merge_video_run=Mock(side_effect=RuntimeError('mock final debit failure')),_ld_refund=refund,JSONResponse=lambda data,status_code:NS(status_code=status_code))
        extract('main.py',['merge_video'],env)
        r=env['merge_video'](NS(job_id='owned',accepted_credits=11),NS())
        self.assertEqual(r.status_code,503);refund.assert_called_once_with('uid',10,'owned');unlock.assert_called_once_with('owned')
    def test_failed_output_publish_refunds_successful_merge_debit(self):
        from shortdub_billing import debit_confirmed
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder);video=output/'input.mp4';video.write_bytes(b'fixture')
            (output/'owned_final_dubbed.mp3').write_bytes(b'fixture')
            (output/'owned_final_dubbed_video.mp4').write_bytes(b'previous successful export')
            price={'max_total':1,'music_charged':0};refund,unlock,debit=Mock(),Mock(),Mock(return_value=99)
            mux=lambda video,audio,final:final.write_bytes(b'pending export')
            env=dict(_merge_say=Mock(),MergeRequest=NS,Request=NS,_rate_limited=Mock(return_value=False),HEAVY_RATE_MAX=1,HEAVY_RATE_WINDOW_SEC=1,
                _job_guard=Mock(return_value=None),_paid_uid=Mock(return_value=('uid',None)),get_credits=lambda uid:100,
                _get_pricing_config=lambda:{'mergeCredits':1},find_job_video=lambda jid:video,_storage_block=Mock(return_value=None),
                OUTPUT_DIR=output,job_background_audio=Mock(return_value=None),voice_clean=NS(ENABLED=False),
                ffmpeg_utils=NS(mux_audio_into_video=mux),_wm_needed=lambda uid:False,debit_confirmed=debit_confirmed,deduct_credits=debit,
                os=NS(replace=Mock(side_effect=OSError('fixture file lock'))),begin_operation=Mock(return_value=True),finish_operation=unlock,
                _short_merge_price=Mock(return_value=price),_ld_refund=refund,JSONResponse=lambda data,status_code:NS(status_code=status_code))
            extract('main.py',['merge_video','_merge_video_run'],env)
            result=env['merge_video'](NS(job_id='owned',accepted_credits=1,enhance_background=False),NS())
            self.assertEqual(result.status_code,503);debit.assert_called_once_with('uid',1,'merge','owned')
            refund.assert_called_once_with('uid',1,'owned');unlock.assert_called_once_with('owned')
            self.assertEqual((output/'owned_final_dubbed_video.mp4').read_bytes(),b'previous successful export')
    def test_account_cleanup_failure_stops_before_auth_or_subscription_changes(self):
        import threading
        for failure,status in ((ValueError('active owned job'),409),(OSError('fixture locked reference'),503)):
            cleanup=Mock(side_effect=failure);delete_auth=Mock()
            env=dict(Request=NS,Response=NS,_current_uid=lambda request:'uid',SUPABASE_URL='https://fixture.invalid',SUPABASE_SERVICE_KEY='fake',
                longdub_service=NS(account_lock=lambda uid:threading.RLock(),delete_account_jobs=cleanup),_delete_account_run=delete_auth,
                JSONResponse=lambda data,status_code:NS(status_code=status_code))
            extract('main.py',['delete_account'],env)
            result=env['delete_account'](NS(),NS())
            self.assertEqual(result.status_code,status);cleanup.assert_called_once_with('uid');delete_auth.assert_not_called()
    def test_known_account_balance_outage_does_not_show_guest(self):
        env=dict(_current_uid=Mock(return_value='uid'),_user_info_cache={'uid':{'name':'Ali'}},_email_for_uid=Mock(return_value=None),get_credits=Mock(return_value=None),_read_subscription_profile=Mock(return_value={}),SUPABASE_SERVICE_KEY='',LIPSYNC_ENABLED=False,_wm_needed=Mock(return_value=False),Request=object,JSONResponse=lambda data,status_code=200:NS(data=data,status_code=status_code))
        extract('main.py',['user_info'],env)
        r=env['user_info'](NS(cookies={'session':'cookie'}))
        self.assertEqual(r['name'],'Ali');self.assertFalse(r['is_guest']);self.assertIsNone(r['credits'])
    def test_storage_outage_refuses_paid_work(self):
        env=dict(_storage_summary=Mock(return_value=None),JSONResponse=lambda data,status_code=200:NS(data=data,status_code=status_code))
        extract('main.py',['_storage_block'],env)
        self.assertEqual(env['_storage_block']('uid',100).status_code,503)
    def test_other_active_job_reservation_counts(self):
        env=dict(_storage_summary=Mock(return_value={'used_bytes':100,'quota_bytes':1000,'subscribed':True,'plan_name':'Studio'}),longdub_service=NS(list_jobs_for_uid=lambda uid:[{'id':'other','status':'dubbing','reserved_output_bytes':850}]),Path=Path,_fmt_storage=str,JSONResponse=lambda data,status_code=200:NS(data=data,status_code=status_code))
        extract('main.py',['_storage_block'],env)
        self.assertEqual(env['_storage_block']('uid',100).status_code,409)
        self.assertIsNone(env['_storage_block']('uid',100,reservation_id='other'))
    def test_parallel_capacity_checks_cannot_reserve_the_same_space(self):
        import threading, concurrent.futures
        projects=[dict(id=str(i),uid='uid',status='uploading',duration=10,size=0) for i in range(3)]
        gate=threading.RLock()
        service=NS(account_lock=lambda uid:gate,list_jobs_for_uid=lambda uid:projects,_total_secs=lambda job:job['duration'],_save=lambda job:None)
        env=dict(longdub_service=service,_storage_summary=lambda uid:dict(used_bytes=0,quota_bytes=1000,subscribed=True,plan_name='Studio'),JSONResponse=lambda data,status_code:NS(body=json.dumps(data).encode(),status_code=status_code),json=json,disk_guard=NS(WORK_LONG_GB=.01,check=lambda *a:(True,'')),_GB=1024**3,_is_final_output=None,_fmt_storage=str,Path=Path)
        extract('main.py',['_storage_block','_ld_capacity','_ld_reserve_capacity'],env)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda job:env['_ld_capacity'](job,600),projects))
        self.assertEqual(sum(r[0] for r in results),1)

# All data and provider keys are isolated, including modules imported by the service.
TEMP=tempfile.TemporaryDirectory(); DATA=Path(TEMP.name)
config=ModuleType('config');config.DATA_DIR=DATA;config.OUTPUT_DIR=DATA/'outputs';config.UPLOAD_DIR=DATA/'uploads'
config.OUTPUT_DIR.mkdir();config.UPLOAD_DIR.mkdir()
config.__getattr__=lambda name:''
for n in ast.parse((ROOT/'config.py').read_text(encoding='utf-8')).body:
    if isinstance(n,ast.Assign):
        for target in n.targets:
            if isinstance(target,ast.Name) and target.id in ('CANONICAL_EMOTIONS','EMOTION_SYNONYMS','GEMINI_MODELS'):
                try:setattr(config,target.id,ast.literal_eval(n.value))
                except ValueError:pass
sys.modules['config']=config;os.environ['RESOURCE_METER']='0'
import longdub_service as ld, longdub_edits as edits, ffmpeg_utils as ff, bg_duck, fal_usage, emotion_review

class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.parent=dict(id='11111111-1111-4111-8111-111111111111',uid='user',status='done',duration=96,analysis={'audio_duration':96},created=1,filename='test.mp4',name='test',paid={},edit_assets=True,dub={'voices':{'s1':'same-provider-voice'}},speaker_list=[{'id':'s1','name':'Speaker 1'}])
        self.rows=[dict(segment_id=s,start=a,end=b,text='original',arabic_text='مرحبا',speaker_id='s1',emotion='neutral') for s,a,b in [('one',1,3),('crossing',43,48),('never-generate',65,70)]]
        ld.job_dir(self.parent['id']).mkdir(exist_ok=True);ld._write_segments(self.parent,self.rows);ld._save(self.parent)
        self.effects=ld.OUTPUT_DIR/f"{self.parent['id']}_final_effects.m4a";self.effects.write_bytes(b'test')
        ld.configure(pricing=lambda:{'chars_per_credit':60,'clone_credits':5,'merge_credits':1},get_credits=lambda uid:10000,charge=lambda *a,**k:99,refund=lambda *a,**k:True)
    def tearDown(self):
        ld._JOBS.clear();shutil.rmtree(ld.LONG_DIR,ignore_errors=True);ld.LONG_DIR.mkdir(exist_ok=True)
        for p in ld.OUTPUT_DIR.iterdir():
            if p.is_file():p.unlink()
    def test_price_only_selected_lines_no_reanalysis_or_existing_clone_charge(self):
        q=edits.quote(self.parent,['one'])
        self.assertEqual([r['segment_id'] for r in q['rows']],['one']);self.assertEqual(q['clones'],0);self.assertEqual(q['music']['max_credits'],0);self.assertEqual(q['due'],q['voice']+1)
    def test_draft_cannot_change_original_transcript(self):
        edits.edit(self.parent,[dict(segment_id='one',arabic_text='جديد',start=2,end=4)])
        self.assertEqual(ld.read_segments(self.parent),self.rows);self.assertEqual(edits.rows(self.parent)[0]['arabic_text'],'جديد')
    def test_recovery_does_not_reuse_a_full_redo_project(self):
        ok,redo=ld.redo_project(self.parent,'user')
        self.assertTrue(ok)
        ok,recovery=ld.redo_project(self.parent,'user',for_edits=True)
        self.assertTrue(ok);self.assertNotEqual(redo['id'],recovery['id'])
        self.assertEqual(recovery['restore_for_edits'],self.parent['id'])
        ok,again=ld.redo_project(self.parent,'user',for_edits=True)
        self.assertEqual(again['id'],recovery['id'])
    def test_missing_speaker_sample_keeps_recovery_accessible(self):
        self.parent['edit_assets']=False;self.parent['dub']['voices']={}
        self.assertFalse(edits.view(self.parent)['has_assets'])
    def test_nan_out_of_range_and_other_speaker_refused(self):
        for change in ({'end':97},{'start':float('nan')},{'speaker_id':'other-user-speaker'}):
            with self.assertRaises(ValueError):edits.edit(self.parent,[dict(segment_id='one',**change)])
    def test_quote_token_rejects_edit_before_debit(self):
        q=edits.quote(self.parent,['one']);edits.edit(self.parent,[dict(segment_id='one',arabic_text='نص جديد أطول')]);charge=Mock();ld.configure(charge=charge)
        with self.assertRaises(ValueError):edits.start(self.parent,['one'],q['token'])
        charge.assert_not_called()
    def test_unknown_balance_and_debit_do_not_submit(self):
        q=edits.quote(self.parent,['one'])
        for balance,debit in ((None,100),(100,None),(100,False)):
            ld.configure(get_credits=lambda uid,b=balance:b,charge=lambda *a,d=debit,**k:d)
            with patch.object(ld,'start_worker') as worker:
                with self.assertRaises(ValueError):edits.start(self.parent,['one'],q['token'])
                worker.assert_not_called()
    def test_correction_folder_failure_precedes_payment(self):
        q=edits.quote(self.parent,['one']);charge=Mock(return_value=100);ld.configure(charge=charge)
        with patch.object(Path,'mkdir',side_effect=OSError('mock disk failure')),patch.object(ld,'start_worker') as worker:
            with self.assertRaises(OSError):edits.start(self.parent,['one'],q['token'])
        charge.assert_not_called();worker.assert_not_called()
    def test_active_correction_protects_expired_clones_and_parent_assets(self):
        self.parent.update(voices_pending_delete=['same-provider-voice'],edit_voice_last_used=0,updated=0)
        ld._JOBS[self.parent['id']]=self.parent
        child=dict(id='22222222-2222-4222-8222-222222222222',uid='user',edit_of=self.parent['id'],created=1,updated=36*86400)
        ld.job_dir(child['id']).mkdir();ld._JOBS[child['id']]=child
        for status in ('payment_pending','confirmed','dubbing'):
            child['status']=status
            with self.subTest(status=status),patch.object(ld,'_now',return_value=36*86400),patch.object(ld,'_delete_pending_voices') as delete:
                ld.sweep_voices();self.assertEqual(ld.sweep_stale(),0)
                with self.assertRaises(ValueError):edits.finish(self.parent)
                delete.assert_not_called()
                self.assertTrue(ld.job_dir(self.parent['id']).exists())
                self.assertEqual(self.parent['dub']['voices']['s1'],'same-provider-voice')
    def test_expired_unused_clone_is_still_released(self):
        self.parent.update(voices_pending_delete=['same-provider-voice'],edit_voice_last_used=0,updated=0)
        ld._JOBS[self.parent['id']]=self.parent
        with patch.object(ld,'_now',return_value=8*86400),patch.object(ld,'_delete_pending_voices') as delete:
            ld.sweep_voices();delete.assert_called_once_with(self.parent)
        self.assertEqual(self.parent['dub']['voices'],{})
    def test_confirmed_correction_refreshes_voice_retention(self):
        self.parent['edit_voice_last_used']=0
        quote=edits.quote(self.parent,['one'])
        with patch.object(ld,'_now',return_value=123),patch.object(ld,'start_worker'):
            edits.start(self.parent,['one'],quote['token'])
        self.assertEqual(self.parent['edit_voice_last_used'],123)
    def test_restored_original_cannot_be_parked_during_correction(self):
        restored=dict(id='33333333-3333-4333-8333-333333333333',uid='user',status='editing',created=1,updated=0,restore_for_edits=self.parent['id'])
        child=dict(id='22222222-2222-4222-8222-222222222222',uid='user',status='dubbing',edit_of=self.parent['id'],created=1,updated=36*86400)
        for job in (self.parent,restored,child):
            ld.job_dir(job['id']).mkdir(exist_ok=True);ld._JOBS[job['id']]=job
        with patch.object(ld,'_now',return_value=36*86400),patch.object(ld,'has_media',return_value=True),patch.object(ld,'park_job') as park:
            ld.sweep_stale();park.assert_not_called()
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg/FFprobe required')
    def test_real_audio_full_duration_gaps_chunk_crossing_and_background(self):
        tone=DATA/'sample.mp3'
        ff.run_ffmpeg(['ffmpeg','-y','-f','lavfi','-i','sine=frequency=440:duration=5','-c:a','libmp3lame',str(tone)])
        ff.run_ffmpeg(['ffmpeg','-y','-f','lavfi','-i','sine=frequency=110:duration=96','-af','volume=0.1','-c:a','aac',str(self.effects)])
        refs=ld.job_dir(self.parent['id'])/'editrefs';refs.mkdir(exist_ok=True)
        ff.run_ffmpeg(['ffmpeg','-y','-i',str(tone),str(refs/'s1.wav')])
        q=edits.quote(self.parent,['one','crossing'])
        with patch.object(ld,'start_worker'):job=edits.start(self.parent,['one','crossing'],q['token'])
        with patch.object(ld,'_tts_with_retry',return_value=(tone.read_bytes(),None)) as speak:edits.run(job)
        self.assertEqual(job['status'],'done',job.get('error'));self.assertEqual(speak.call_count,2)
        # the finished correction must stay on record: the page lists it and offers its downloads from this file
        self.assertTrue((ld.job_dir(job['id'])/'job.json').exists(),'the finished correction job record was deleted with its scratch files')
        self.assertTrue(all(c.args[0]=='same-provider-voice' for c in speak.call_args_list))
        pure=ld.OUTPUT_DIR/f"{job['id']}_final_voices.m4a"
        self.assertAlmostEqual(ff.get_media_duration(pure),96,places=2)
        pcm=dub_audio._decode(pure,DATA/'pure.pcm');power=dub_audio.levels(pcm);pcm._mmap.close()
        self.assertGreater(power[2:6].max(),.001);self.assertGreater(power[88:96].min(),.001)
        self.assertLess(power[130:140].max(),.00005);self.assertLess(power[20:60].max(),.00005)
        pcm=dub_audio._decode(ld.result_file(job),DATA/'mixed.pcm');power=dub_audio.levels(pcm);pcm._mmap.close()
        self.assertGreater(power[130:140].min(),.001)

class AudioTests(unittest.TestCase):
    def test_interrupted_paid_music_cannot_repeat_provider_calls(self):
        import dub_background
        job=dict(paid={'music_fill':10},background_checkpoint={'bed':{'started':True}})
        with patch.object(dub_background,'prepare') as generate:
            with self.assertRaisesRegex(RuntimeError,'interrupted'):
                dub_background.checkpointed_prepare(job,Mock(),1,'bg','voice','dub',DATA,'bed',[])
            generate.assert_not_called()
    def test_completed_background_checkpoint_is_reused(self):
        import dub_background
        p=DATA/'checkpoint_matched.wav';p.write_bytes(b'completed fixture')
        job=dict(paid={'music_fill':10},background_checkpoint={'checkpoint':dict(started=True,ready=True,music_fill={'filled':True},measurements={})})
        with patch.object(dub_background,'prepare') as generate:
            result=dub_background.checkpointed_prepare(job,Mock(),1,'bg','voice','dub',DATA,'checkpoint',[])
        self.assertEqual(result['path'],p);generate.assert_not_called()
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_missing_music_context_refuses_false_success(self):
        import music_fill, dub_background
        original, muted = DATA/'context-original.wav', DATA/'context-muted.wav'
        music_fill._write_wav(original, np.full((12*44100,2),2000,dtype='<i2'))
        music_fill._write_wav(muted, np.zeros((12*44100,2),dtype='<i2'))
        with self.assertRaisesRegex(ValueError,'too little clean background'):
            dub_background.count_repairs(muted,[(0,12)],DATA/'context.pcm',original=original)
        music_fill._write_wav(original, np.zeros((12*44100,2),dtype='<i2'))
        self.assertEqual(dub_background.count_repairs(muted,[(0,12)],DATA/'context.pcm',original=original),0)
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_music_payment_failure_cannot_deliver_and_closes_pcm(self):
        import music_fill
        samples=np.zeros((30*44100,2),dtype='<i2')
        samples[:10*44100]=2000; samples[15*44100:]=2000
        source, out=DATA/'paid-music.wav', DATA/'paid-repaired.wav'
        music_fill._write_wav(source,samples)
        with patch.object(music_fill,'LOCAL_FILL',False),patch.object(music_fill,'_fill_one',return_value=(True,'mock success')):
            result=music_fill.fill(source,out,[(10,15)],'fake',prompt='instrumental',on_filled=Mock(side_effect=RuntimeError('mock debit failure')))
        self.assertFalse(result['filled']);self.assertFalse(out.exists())
        self.assertFalse(Path(str(out)+'.mf.pcm').exists())
    def test_local_levels_do_not_raise_entire_background_from_one_pause(self):
        gain,_=dub_audio.gain_plan(np.array([.1,.01,.2,.02]),np.array([.05,.01,.05,.02]))
        np.testing.assert_allclose(gain,[2,1,4,1])
    def test_loud_clean_repair_is_attenuated_to_measured_context(self):
        import music_fill
        rate=music_fill.RATE;t=np.arange(rate*24)/rate
        quiet=(1600*np.sin(2*np.pi*220*t)).astype('<i2')
        pcm=np.repeat(quiet[:,None],2,axis=1);pcm[8*rate:12*rate]=0
        generated=np.repeat((25000*np.sin(2*np.pi*220*t)).astype('<i2')[:,None],2,axis=1)
        context=music_fill._seg_db(quiet)
        with patch.object(music_fill,'_write_wav'),patch.object(music_fill,'_decode_audio',return_value=generated):
            ok,note=music_fill._fill_one(pcm,8,12,context,'fake','instrumental',lambda *a:b'fake',None)
        self.assertTrue(ok,note)
        self.assertAlmostEqual(music_fill._seg_db(pcm[9*rate:11*rate]),context-music_fill.TARGET_BELOW_CTX_DB,places=1)
        np.testing.assert_array_equal(pcm[:7*rate,0],quiet[:7*rate])
    def test_too_quiet_repair_cannot_be_amplified_past_limit(self):
        import music_fill
        rate=music_fill.RATE;t=np.arange(rate*24)/rate
        quiet=(1600*np.sin(2*np.pi*220*t)).astype('<i2')
        pcm=np.repeat(quiet[:,None],2,axis=1);pcm[8*rate:12*rate]=0
        before=pcm.copy();generated=np.repeat((20*np.sin(2*np.pi*220*t)).astype('<i2')[:,None],2,axis=1)
        with patch.object(music_fill,'_write_wav'),patch.object(music_fill,'_decode_audio',return_value=generated):
            ok,note=music_fill._fill_one(pcm,8,12,music_fill._seg_db(quiet),'fake','instrumental',lambda *a:b'fake',None)
        self.assertFalse(ok);np.testing.assert_array_equal(pcm,before)
    def test_clipped_provider_audio_is_refused_before_lowering_volume(self):
        import music_fill
        rate=music_fill.RATE;t=np.arange(rate*24)/rate
        quiet=(1600*np.sin(2*np.pi*220*t)).astype('<i2')
        pcm=np.repeat(quiet[:,None],2,axis=1);pcm[8*rate:12*rate]=0
        before=pcm.copy()
        generated=np.repeat(np.clip(100000*np.sin(2*np.pi*220*t),-32768,32767).astype('<i2')[:,None],2,axis=1)
        with patch.object(music_fill,'_write_wav'),patch.object(music_fill,'_decode_audio',return_value=generated):
            ok,note=music_fill._fill_one(pcm,8,12,music_fill._seg_db(quiet),'fake','instrumental',lambda *a:b'fake',None)
        self.assertFalse(ok);self.assertIn('severely clipped',note);np.testing.assert_array_equal(pcm,before)
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_strict_mute_zero_inside_original_speech(self):
        bg,voice,out=(DATA/n for n in ('bg.wav','v.wav','muted.wav'))
        for p in (bg,voice):ff.run_ffmpeg(['ffmpeg','-y','-f','lavfi','-i','sine=frequency=440:duration=12',str(p)])
        r=bg_duck.mute_speech(bg,voice,out,spans=[(3,6)],mode='always',keep=False,strict=True)
        self.assertTrue(r['muted'],r['reason'])
        pcm=dub_audio._decode(out,DATA/'mute.pcm');power=dub_audio.levels(pcm);pcm._mmap.close()
        self.assertLess(power[7:11].max(),1e-6)

class ProviderTests(unittest.TestCase):
    def tearDown(self):fal_usage._cached=(0,{})
    def test_wallet_and_net_provider_spend_contract(self):
        fal_usage._cached=(0,{})
        def fetch(path,key):
            if path.startswith('account/'):return {'credits':{'current_balance':12.5,'currency':'USD'}}
            return {'summary':[{'cost_total':2.3,'quantity':460,'unit':'seconds','currency':'USD'}],'has_more':False,'next_cursor':None}
        r=fal_usage.read('fake',fetch);self.assertEqual(r['balance'],12.5);self.assertEqual(r['month_cost'],2.3)
    def test_permission_error_is_not_zero_balance(self):
        import urllib.error
        def fail(*a):raise urllib.error.HTTPError('url',403,'forbidden',{},None)
        r=fal_usage.read('fake',fail);self.assertIsNone(r['balance']);self.assertIn('admin-scoped',r['note'])
    def test_uncertain_emotion_keeps_current_style_without_accuracy_claim(self):
        sample=DATA/'style.mp3';sample.write_bytes(b'fake')
        answer={'candidates':[{'content':{'parts':[{'text':json.dumps({'suggested':'angry','uncertain':True,'reason':'ambiguous'})}]}}]}
        with patch.object(emotion_review.gemini_service,'call_gemini',return_value=(answer,None)),patch.object(emotion_review.gemini_service,'record_gemini'):
            r=emotion_review.inspect('job',sample,'angry','fake')
        self.assertEqual(r['fallback'],'');self.assertFalse(r['detected']);self.assertIsNone(r['accuracy'])

class EditorActionTests(unittest.TestCase):
    def setUp(self): CorrectionTests.setUp(self)
    def tearDown(self): CorrectionTests.tearDown(self)
    def test_insert_delete_and_empty_draft_never_restore_original_rows(self):
        new=edits.line_operation(self.parent,'insert','one')
        inserted=next(r for r in edits.rows(self.parent) if r['segment_id']==new)
        self.assertEqual((inserted['start'],inserted['end']),(3,5));self.assertTrue(inserted['manual_time'])
        self.assertEqual(ld.read_segments(self.parent),self.rows)
        for row in edits.rows(self.parent):edits.line_operation(self.parent,'delete',row['segment_id'])
        self.assertEqual(edits.rows(self.parent),[]);self.assertEqual(ld.read_segments(self.parent),self.rows)
    def test_split_uses_translation_and_preserves_original_and_confirmed_style(self):
        self.rows[0].update(text='hello everyone',emotion='happy',emotion_set=True)
        ld._write_segments(self.parent,self.rows)
        translated=lambda jid,rows,glossary:{r['segment_id']:('مرحبا','sad') for r in rows}
        with patch.object(ld,'_translate_batch',side_effect=translated):new=edits.line_operation(self.parent,'split','one',6)
        split=edits.rows(self.parent)[:2]
        self.assertEqual([r['text'] for r in split],['hello','everyone']);self.assertTrue(all(r['emotion']=='happy' for r in split))
        self.assertTrue(all(r['arabic_text']=='مرحبا' for r in split));self.assertEqual(split[1]['segment_id'],new)
        self.assertEqual(ld.read_segments(self.parent),self.rows)
    def test_failed_split_translation_makes_no_partial_change(self):
        self.rows[0]['text']='hello everyone';ld._write_segments(self.parent,self.rows)
        with patch.object(ld,'_translate_batch',return_value={}):
            with self.assertRaises(ValueError):edits.line_operation(self.parent,'split','one',6)
        self.assertEqual(edits.rows(self.parent),self.rows);self.assertNotIn('correction_draft',self.parent)
    def test_retranslation_preserves_confirmed_style_and_original(self):
        self.rows[0].update(emotion='happy',emotion_set=True);ld._write_segments(self.parent,self.rows)
        with patch.object(ld,'_translate_batch',return_value={'one':('ترجمة جديدة','sad')}):edits.line_operation(self.parent,'retranslate','one')
        self.assertEqual(edits.rows(self.parent)[0]['emotion'],'happy');self.assertEqual(ld.read_segments(self.parent),self.rows)
    def test_tashkeel_adds_marks_without_changing_words_or_original(self):
        import gemini_service
        self.rows[0]['arabic_text']='مرحبا';ld._write_segments(self.parent,self.rows)
        with patch.object(gemini_service,'add_tashkeel_lines',return_value={'one':'مَرْحَبًا'}):edits.line_operation(self.parent,'tashkeel','one')
        self.assertEqual(edits.rows(self.parent)[0]['arabic_text'],'مَرْحَبًا');self.assertEqual(ld.read_segments(self.parent),self.rows)
    def test_audio_edits_and_new_line_quote_use_saved_draft(self):
        new=edits.line_operation(self.parent,'insert','one')
        edits.edit(self.parent,[dict(segment_id=new,arabic_text='مرحبا',start=4,end=5,manual_time=True)])
        quote=edits.quote(self.parent,[new]);self.assertEqual(quote['rows'][0]['start'],4);self.assertTrue(quote['rows'][0]['manual_time'])
        self.assertEqual(quote['clones'],0);self.assertEqual(ld.read_segments(self.parent),self.rows)
    def test_active_correction_and_unknown_row_reject_line_actions(self):
        for operation in ['insert','split','delete','retranslate','tashkeel']:
            with patch.object(edits,'active',return_value=True):
                with self.assertRaises(ValueError):edits.line_operation(self.parent,operation,'one')
            with self.assertRaises(ValueError):edits.line_operation(self.parent,operation,'other-project-row')
        self.assertEqual(ld.read_segments(self.parent),self.rows)
    def test_review_checkbox_can_be_unchecked_without_affecting_other_pages(self):
        import asyncio
        job=dict(self.parent,reviewed_batches={'0':'old','1':'unchanged'})
        env=dict(_ld_job=lambda *a:('user',job,None),longdub_service=ld,longdub_edits=edits,dub_review=dub_review,Request=NS,
                 JSONResponse=lambda body,status_code:NS(body=body,status_code=status_code))
        extract('main.py',['longdub_review_page'],env)
        async def body():return {'page':0,'reviewed':False}
        with patch.object(ld,'_save'):
            asyncio.run(env['longdub_review_page']('owned',NS(json=body)))
        self.assertEqual(job['reviewed_batches'],{'1':'unchanged'})
    def test_foreign_project_route_stops_before_line_mutation(self):
        sentinel=object();operation=Mock()
        env=dict(_ld_job=lambda *a:(None,None,sentinel),LongDubCorrectionAction=NS,Request=NS,longdub_edits=NS(line_operation=operation))
        extract('main.py',['longdub_correction_line'],env)
        result=env['longdub_correction_line']('foreign','delete',NS(segment_id='one'),NS())
        self.assertIs(result,sentinel);operation.assert_not_called()

class OneSecondMusicTests(unittest.TestCase):
    def test_one_second_context_is_eligible_without_inventing_a_reference(self):
        import music_fill
        db=np.r_[np.full(10,-24.),np.full(50,-120.)]
        gaps,info=music_fill.find_gaps(db,[(1,6)])
        self.assertEqual(gaps,[(1.0,6.0)]);self.assertEqual(info['music_sec'],1.0)
        self.assertEqual(music_fill.find_gaps(np.full(60,-120.),[(0,6)])[0],[])
        self.assertEqual(music_fill.find_gaps(np.r_[np.full(9,-24.),np.full(51,-120.)],[(.9,6)])[0],[])
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_quote_and_fill_agree_for_one_second_music_context(self):
        import music_fill,dub_background,io,wave
        rate=music_fill.RATE;t=np.arange(rate*6)/rate;tone=np.repeat((np.sin(2*np.pi*220*t)*2200).astype('<i2')[:,None],2,axis=1)
        muted=tone.copy();muted[rate:]=0
        src=DATA/'one-second-context.wav';out=DATA/'one-second-repaired.wav';music_fill._write_wav(src,muted)
        self.assertEqual(dub_background.count_repairs(src,[(1,6)],DATA/'one-second.pcm',original=src),1)
        data=io.BytesIO()
        with wave.open(data,'wb') as audio:audio.setnchannels(2);audio.setsampwidth(2);audio.setframerate(rate);audio.writeframes(tone.tobytes())
        charges=[];runner=Mock(return_value=data.getvalue())
        result=music_fill.fill(src,out,[(1,6)],'fake',prompt='instrumental',runner=runner,on_filled=lambda *a:charges.append(a))
        self.assertTrue(result['filled'],result['reason']);self.assertEqual(runner.call_count,1);self.assertEqual(len(charges),1)
        self.assertTrue(out.exists());self.assertFalse(Path(str(out)+'.mf.pcm').exists())

if __name__=='__main__':unittest.main()


_LIFE_OWNED='11111111-1111-4111-8111-111111111111'
_LIFE_OTHER='22222222-2222-4222-8222-222222222222'

class LifecycleCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.long = root / 'long'; self.long.mkdir()
        self.out = root / 'outputs'; self.out.mkdir()
        self.parent = dict(id=_LIFE_OWNED, uid='owner', status='done', created=1, edit_assets=True,
                           edit_voice_last_used=100, voices_pending_delete=['clone-owned'],
                           dub={'voices': {'s1': 'clone-owned'}}, name='Personal project')
        self.other = dict(id=_LIFE_OTHER, uid='other', status='done', created=1, edit_assets=True,
                          edit_voice_last_used=100, voices_pending_delete=['clone-other'])
        self.jobs = {_LIFE_OWNED: self.parent, _LIFE_OTHER: self.other}
        self.lock = threading.RLock()
        self.provider_calls = []
        self.fail_provider = False
        def save(job):
            directory = self.long / job['id']
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'job.json').write_text(json.dumps(job))
        self.save = save
        for job in self.jobs.values():
            save(job)
            directory = self.long / job['id']
            (directory / 'editrefs').mkdir()
            (directory / 'editrefs' / 's1.wav').write_bytes(b'personal voice sample')
            (directory / 'segments.json').write_text('["personal text"]')
            (directory / 'src.mp4').write_bytes(b'personal media')
            (self.out / (job['id'] + '_final_dubbed_video.mp4')).write_bytes(b'final')
        def delete(job):
            self.provider_calls.append(job['id'])
            if not self.fail_provider:
                job['voices_pending_delete'] = []
                save(job)
        self.env = dict(account_lock=lambda uid: self.lock, _lock_for=lambda jid: self.lock,
                        _LOCK=self.lock, _JOBS=self.jobs, _RUNNING=set(), _now=lambda:100,
                        _valid_id=lambda jid: bool(re.fullmatch(r'[0-9a-f\-]{36}', jid)),
                        list_jobs_for_uid=lambda uid: [j for j in self.jobs.values() if j.get('uid') == uid],
                        job_dir=lambda jid:self.long/jid, LONG_DIR=self.long, OUTPUT_DIR=self.out,
                        TRACK_KINDS={'voices':'_final_voices.m4a','effects':'_final_effects.m4a'},
                        _delete_pending_voices=delete, _save=save, shutil=shutil,
                        load_job=lambda jid:self.jobs.get(jid))
        extract('longdub_service.py',['_delete_project_assets','delete_account_jobs','sweep_voices'],self.env)
        fake = ModuleType('longdub_edits'); fake.active=lambda job:False
        self.modules = patch.dict(sys.modules, {'longdub_edits':fake}); self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_ownership_and_complete_assets_removed(self):
        self.assertEqual(self.env['delete_account_jobs']('owner'), 1)
        self.assertFalse((self.long / _LIFE_OWNED).exists())
        self.assertFalse((self.out / (_LIFE_OWNED + '_final_dubbed_video.mp4')).exists())
        self.assertTrue((self.long / _LIFE_OTHER / 'editrefs' / 's1.wav').exists())
        self.assertTrue((self.out / (_LIFE_OTHER + '_final_dubbed_video.mp4')).exists())
        self.assertEqual(self.provider_calls, [_LIFE_OWNED])

    def test_every_busy_status_blocks_before_any_deletion(self):
        for status in ('payment_pending','accepted','analyzing','confirmed','dubbing'):
            self.parent['status'] = status
            with self.assertRaises(ValueError): self.env['delete_account_jobs']('owner')
        self.parent['status']='editing'; self.env['_RUNNING'].add(_LIFE_OWNED)
        with self.assertRaises(ValueError): self.env['delete_account_jobs']('owner')
        self.assertTrue((self.long / _LIFE_OWNED / 'editrefs' / 's1.wav').exists())
        self.assertEqual(self.provider_calls, [])

    def test_failed_provider_keeps_only_anonymous_tombstone_then_retry_removes_it(self):
        self.fail_provider=True
        self.env['delete_account_jobs']('owner')
        directory=self.long/_LIFE_OWNED
        self.assertEqual([p.name for p in directory.iterdir()], ['job.json'])
        tomb=json.loads((directory/'job.json').read_text())
        self.assertIsNone(tomb['uid']); self.assertEqual(tomb['status'], 'provider_cleanup')
        self.assertEqual(tomb['voices_pending_delete'], ['clone-owned'])
        self.assertNotIn('dub',tomb); self.assertNotIn('name',tomb)
        self.assertFalse((directory/'editrefs').exists())
        self.fail_provider=False
        self.env['sweep_voices']()
        self.assertFalse(directory.exists()); self.assertNotIn(_LIFE_OWNED,self.jobs)
        self.assertTrue((self.long/_LIFE_OTHER/'editrefs'/'s1.wav').exists())
        self.assertEqual(self.provider_calls,[_LIFE_OWNED,_LIFE_OWNED])

    def test_disk_failure_preserves_owned_checkpoint_and_pending_ids_for_retry(self):
        self.fail_provider=True
        original=shutil.rmtree
        def fail(path,*args,**kwargs):
            if Path(path) == self.long/_LIFE_OWNED/'editrefs': raise OSError('mock disk permission failure')
            return original(path,*args,**kwargs)
        with patch.object(shutil,'rmtree',side_effect=fail):
            with self.assertRaises(OSError):self.env['delete_account_jobs']('owner')
        self.assertEqual(self.parent['uid'],'owner')
        self.assertEqual(self.parent['status'],'account_cleanup')
        self.assertEqual(self.parent['voices_pending_delete'],['clone-owned'])
        self.assertTrue((self.long/_LIFE_OWNED/'editrefs'/'s1.wav').exists())
        self.fail_provider=False
        self.assertEqual(self.env['delete_account_jobs']('owner'),1)
        self.assertFalse((self.long/_LIFE_OWNED).exists())

    def test_empty_tombstone_after_restart_is_removed_without_provider_request(self):
        self.parent.clear();self.parent.update(id=_LIFE_OWNED,uid=None,status='provider_cleanup',voices_pending_delete=[])
        directory=self.long/_LIFE_OWNED
        shutil.rmtree(directory);self.save(self.parent)
        self.env['sweep_voices']()
        self.assertFalse(directory.exists());self.assertEqual(self.provider_calls,[])
