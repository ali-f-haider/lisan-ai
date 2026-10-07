"""Offline paid-line replay, readiness cache and admin diagnostics tests."""
import asyncio
import copy
import io
import json
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError

import credit_billing as cb
import shortdub_operations as ops
from billing_health import load_health, CATEGORIES
from test_credit_billing import AtomicLedger, UID
from test_shortdub_upgrade import request, segment, source_functions, Response, JOB_A


class BeginLedger(AtomicLedger):
    def _transaction(self, name, args):
        if name == 'lisan_credit_begin':
            old = self.operations.get(args['p_operation_id'])
            if old:
                return old
            return {'uid':args['p_uid'], 'amount':args['p_amount'], 'kind':'debit',
                    'operation_id':args['p_operation_id'], 'status':'pending'}
        return super()._transaction(name, args)


class RegenerationTests(unittest.TestCase):
    def setUp(self):
        self.store = tempfile.TemporaryDirectory()
        self.addCleanup(self.store.cleanup)
        self.output = Path(self.store.name)
        self.req = request(accepted_credits=3)
        self.db = BeginLedger(2,20)
        self.paths = [self.output/'take.mp3']
        self.generated = Mock(side_effect=self.make_audio)
        self.prepared = Mock(return_value=3)
        self.charge = Mock(side_effect=lambda amount, op: cb.debit(self.db.rpc,UID,amount,'regenerate',JOB_A,operation_id=op))
        self.settle = Mock(side_effect=lambda op,outcome,amount: cb.settle_clone(self.db.rpc,self.output,UID,op,outcome,amount))
    def make_audio(self):
        self.paths[0].write_bytes(b'synthetic take')
        return {'status':'success','stretched_duration':1.5}
    def run_request(self, req=None):
        return ops.run(self.output, req or self.req, UID, self.prepared, self.charge,
                       self.generated, self.settle, self.db.rpc, lambda:self.paths)
    def test_same_id_returns_the_saved_take_with_one_generation_and_charge(self):
        first=self.run_request(); second=self.run_request()
        self.assertEqual(first['operation_id'],second['operation_id'])
        self.assertTrue(second['replayed']); self.assertEqual(self.generated.call_count,1)
        self.assertEqual(self.charge.call_count,1); self.assertEqual(len(self.db.history),1)
    def test_new_id_buys_a_distinct_take(self):
        self.run_request(); self.req.operation_id=str(uuid.uuid4()); self.run_request()
        self.assertEqual(self.generated.call_count,2); self.assertEqual(self.charge.call_count,2)
        self.assertEqual([h['credits'] for h in self.db.history],[3,3])
    def test_changed_line_voice_price_and_timing_refuse_the_same_id(self):
        self.run_request()
        for field,value in [('voice_id','another voice'),('accepted_credits',4),('total_duration',9)]:
            changed=copy.deepcopy(self.req); setattr(changed,field,value)
            with self.assertRaises(ops.RequestProblem): self.run_request(changed)
        changed=copy.deepcopy(self.req); changed.segment=segment(sid='another line')
        with self.assertRaises(ops.RequestProblem): self.run_request(changed)
        self.assertEqual(self.generated.call_count,1); self.assertEqual(self.charge.call_count,1)
    def test_restart_reloads_disk_without_a_provider_or_payment_call(self):
        self.run_request()
        result=ops.run(self.output,copy.deepcopy(self.req),UID,Mock(side_effect=AssertionError),
                       Mock(side_effect=AssertionError),Mock(side_effect=AssertionError),
                       Mock(side_effect=AssertionError),self.db.rpc,lambda:self.paths)
        self.assertTrue(result['replayed'])
    def test_missing_or_replaced_audio_is_honest_and_not_charged_again(self):
        self.run_request(); self.paths[0].unlink()
        with self.assertRaises(ops.RequestProblem) as caught: self.run_request()
        self.assertEqual(caught.exception.status,410); self.assertEqual(self.charge.call_count,1)
        self.paths[0].write_bytes(b'a later take')
        with self.assertRaises(ops.RequestProblem): self.run_request()
    def test_failed_generation_is_fully_refunded_once_in_original_buckets(self):
        self.generated.side_effect=None; self.generated.return_value={'error':'Synthetic failure'}
        first=self.run_request(); second=self.run_request()
        self.assertEqual(first['credits_refunded'],3); self.assertEqual(second['credits_charged'],0)
        self.assertEqual(self.db.balance,{'subscription':2,'permanent':20})
        self.assertEqual([h['credits'] for h in self.db.history],[3,-3]); self.assertEqual(self.generated.call_count,1)
    def test_refund_outage_keeps_a_pending_journal_without_false_history(self):
        self.generated.side_effect=None; self.generated.return_value={'error':'Synthetic failure'}
        normal=self.db.rpc
        def failed_refund(name,args,strict=False):
            if name=='lisan_credit_refund': raise TimeoutError('Synthetic outage')
            return normal(name,args,strict)
        self.settle.side_effect=lambda op,outcome,amount:cb.settle_clone(failed_refund,self.output,UID,op,outcome,amount)
        first=self.run_request(); self.assertEqual(first['refund_pending'],3)
        self.assertEqual([h['credits'] for h in self.db.history],[3])
        cb.reconcile_clones(normal,self.output)
        self.assertEqual([h['credits'] for h in self.db.history],[3,-3])
    def test_unconfirmed_debit_starts_no_provider_and_reuses_the_id(self):
        self.charge.side_effect=[None,19]
        with self.assertRaises(ops.RequestProblem): self.run_request()
        self.generated.assert_not_called(); self.run_request()
        self.assertEqual(self.charge.call_args_list[0],self.charge.call_args_list[1])
    def test_lost_debit_replies_replay_the_committed_charge_before_generation(self):
        normal=self.db.rpc; failures=0
        def lost(name,args,strict=False):
            nonlocal failures
            result=normal(name,args,strict)
            if name=='lisan_atomic_debit' and failures<2:
                failures+=1; raise TimeoutError('Synthetic lost committed reply')
            return result
        self.charge.side_effect=lambda amount,op:cb.debit(lost,UID,amount,'regenerate',JOB_A,operation_id=op)
        with self.assertRaises(ops.RequestProblem): self.run_request()
        self.generated.assert_not_called(); self.run_request()
        self.assertEqual(self.generated.call_count,1); self.assertEqual(len(self.db.history),1)
    def test_worker_restart_after_provider_start_blocks_an_unknown_outcome(self):
        self.generated.side_effect=RuntimeError('Interrupted outside provider wrapper')
        with self.assertRaises(ops.RequestProblem): self.run_request()
        self.generated.side_effect=self.make_audio
        with self.assertRaises(ops.RequestProblem): self.run_request()
        self.assertEqual(self.generated.call_count,1); self.assertEqual(self.charge.call_count,1)
    def test_erased_project_receipt_cannot_make_a_completed_sql_id_generate_again(self):
        self.run_request(); ops.operation_path(self.output,JOB_A,self.req.operation_id).unlink()
        with self.assertRaises(ops.RequestProblem) as caught: self.run_request()
        self.assertEqual(caught.exception.status,410); self.assertEqual(self.generated.call_count,1)
    def test_record_failure_before_debit_calls_no_paid_service(self):
        with patch.object(ops,'write',side_effect=ops.RequestProblem('Synthetic disk failure',503)):
            with self.assertRaises(ops.RequestProblem): self.run_request()
        self.charge.assert_not_called(); self.generated.assert_not_called()
    def test_failure_checkpoint_error_still_journals_the_refund(self):
        self.generated.side_effect=None; self.generated.return_value={'error':'Synthetic failure'}
        original=ops.write
        def fail(path,record):
            if record['status']=='failed': raise ops.RequestProblem('Synthetic disk failure',503)
            return original(path,record)
        with patch.object(ops,'write',side_effect=fail):
            with self.assertRaises(ops.RequestProblem): self.run_request()
        self.assertEqual([h['credits'] for h in self.db.history],[3,-3])
    def test_record_limit_never_evicts_a_paid_id(self):
        self.run_request(); self.req.operation_id=str(uuid.uuid4())
        with patch.object(ops,'MAX_RECORDS',1):
            with self.assertRaises(ops.RequestProblem): self.run_request()
        self.assertEqual(self.charge.call_count,1)
    def test_missing_or_denied_registration_never_opens_free_work(self):
        with patch.object(ops,'rpc_result',return_value=None):
            with self.assertRaises(ops.RequestProblem): self.run_request()
        self.charge.assert_not_called(); self.generated.assert_not_called()
    def test_project_cleanup_keeps_receipts_for_a_retained_export_and_then_removes_them(self):
        self.run_request()
        path=ops.operation_path(self.output,JOB_A,self.req.operation_id)
        asset=self.output/(JOB_A+'_final_dubbed.mp3');asset.write_bytes(b'final export')
        ops.maintain_receipts(self.output,self.output)
        self.assertTrue(path.exists())
        asset.unlink();ops.maintain_receipts(self.output,self.output)
        self.assertFalse(path.exists())
    def test_cleanup_does_not_follow_a_redirected_project_folder(self):
        self.run_request()
        with patch.object(Path,'resolve',return_value=Path(self.output.parent/'outside/redirected')):
            ops.clear_receipts(self.output,JOB_A)
        self.assertTrue(ops.operation_path(self.output,JOB_A,self.req.operation_id).exists())
    def test_missing_id_and_foreign_account_are_refused(self):
        with self.assertRaises(ops.RequestProblem): ops.operation_path(self.output,JOB_A,None)
        self.run_request()
        with self.assertRaises(ops.RequestProblem):
            ops.run(self.output,self.req,'another account',self.prepared,self.charge,self.generated,self.settle,self.db.rpc,lambda:self.paths)


class ReadyCacheTests(unittest.TestCase):
    def setUp(self): cb.reset_ready_cache()
    def tearDown(self): cb.reset_ready_cache()
    def test_positive_is_cached_for_at_most_thirty_monotonic_seconds(self):
        rpc=Mock(return_value={'version':1, 'debit':True,'pack':True,'refund':True,'cancel':True})
        with patch.object(cb.time,'monotonic',return_value=100):
            for _ in range(10): self.assertTrue(cb.ready(rpc))
        self.assertEqual(rpc.call_count,1)
        with patch.object(cb.time,'monotonic',return_value=129.999): self.assertTrue(cb.ready(rpc))
        self.assertEqual(rpc.call_count,1)
        with patch.object(cb.time,'monotonic',return_value=130): self.assertTrue(cb.ready(rpc))
        self.assertEqual(rpc.call_count,2)
    def test_bound_rpc_adapters_share_the_positive_cache(self):
        ledger=AtomicLedger()
        self.assertTrue(cb.ready(ledger.rpc));self.assertTrue(cb.ready(ledger.rpc))
        self.assertEqual(len(ledger.calls),1)
    def test_negative_is_not_cached_and_recovery_is_immediate(self):
        rpc=Mock(return_value={'version':1,'debit':False})
        self.assertFalse(cb.ready(rpc)); self.assertFalse(cb.ready(rpc)); self.assertEqual(rpc.call_count,2)
        rpc.return_value={'version':1,'debit':True,'pack':True,'refund':True,'cancel':True}
        self.assertTrue(cb.ready(rpc)); self.assertEqual(rpc.call_count,3)
    def test_errors_are_never_cached_or_treated_as_ready(self):
        rpc=Mock(side_effect=TimeoutError('Synthetic outage'))
        self.assertFalse(cb.ready(rpc)); attempts=rpc.call_count
        self.assertFalse(cb.ready(rpc)); self.assertGreater(rpc.call_count,attempts)
    def test_simultaneous_threads_share_one_positive_refresh(self):
        rpc=Mock(return_value={'version':1,'debit':True,'pack':True,'refund':True,'cancel':True})
        barrier=threading.Barrier(8)
        def check(_): barrier.wait(); return cb.ready(rpc)
        with ThreadPoolExecutor(max_workers=8) as pool: self.assertTrue(all(pool.map(check,range(8))))
        self.assertEqual(rpc.call_count,1)
        cb.reset_ready_cache(); self.assertTrue(cb.ready(rpc)); self.assertEqual(rpc.call_count,2)


class HealthTests(unittest.TestCase):
    def payload(self):
        row={'operation_id':str(uuid.uuid4()),'account_id':UID,'amount':3,'age_seconds':700,'kind':'debit',
             'email':'private@example.test','receipt':'private checkout','text':'private customer text'}
        return {'ready':{'version':1,'debit':True,'pack':True,'refund':True,'cancel':True},
                'lists':{key:{'count':25,'rows':[row]*25} for key in CATEGORIES}}
    def test_route_shape_and_whitelist_exclude_all_personal_text(self):
        rpc=Mock(return_value=self.payload())
        env=source_functions('main.py',['admin_billing_health'],{'_admin_check':Mock(return_value=True),'_sb_rpc':rpc,
                       'JSONResponse':lambda content,**kwargs:Response(content,kwargs.get('status_code',200))})
        result=env['admin_billing_health'](object()).content
        self.assertTrue(result['ready']); self.assertTrue(result['installed'])
        for group in result['lists'].values():
            self.assertEqual(group['count'],25); self.assertEqual(len(group['rows']),20)
            self.assertEqual(set(group['rows'][0]),{'operation_id','account_id','amount','age_seconds','kind'})
        encoded=json.dumps(result)
        for private in ('private@example.test','private checkout','private customer text'):
            self.assertNotIn(private,encoded)
        rpc.assert_called_once_with('lisan_billing_health',{},strict=True)
    def test_missing_function_is_not_installed_and_outage_is_distinct(self):
        error=HTTPError('https://example.test/rpc',404,'Missing',{},io.BytesIO(b'{"code":"PGRST202"}'))
        self.assertEqual(load_health(Mock(side_effect=error))['installed'],False)
        self.assertIsNone(load_health(Mock(side_effect=TimeoutError()))['installed'])
    def test_admin_check_precedes_all_database_calls(self):
        rpc=Mock()
        env=source_functions('main.py',['admin_billing_health'],{'_admin_check':Mock(return_value=False),'_sb_rpc':rpc,'JSONResponse':Response})
        self.assertEqual(env['admin_billing_health'](object()).status_code,401); rpc.assert_not_called()


class InputBoundaryTests(unittest.TestCase):
    def lip_env(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        output=Path(self.folder.name);video=output/'synthetic.mp4';video.write_bytes(b'synthetic')
        from types import SimpleNamespace as NS
        env=source_functions('main.py',['lipsync'],dict(_job_guard=Mock(return_value=None),LIPSYNC_ENABLED=True,
            _rate_limited=Mock(return_value=False),LIGHT_RATE_MAX=1,LIGHT_RATE_WINDOW_SEC=1,
            _paid_uid=lambda _:('user',None),find_job_video=lambda _:video,
            ffmpeg_utils=NS(get_media_duration=lambda _:5),_storage_block=lambda *args:None,OUTPUT_DIR=output,
            LIPSYNC_MIN_SEC=1,LIPSYNC_MAX_SEC=30,lipsync_res=lambda _: '720p',lipsync_rate=lambda *args:2,
            _get_pricing_config=lambda:{},LIPSYNC_TEST_MODE=False,get_credits=Mock(return_value=100),
            deduct_credits=Mock(return_value=None),debit_confirmed=lambda value:type(value)is int and value>=0,
            JSONResponse=Response,jobs_progress={},threading=NS(Thread=Mock(return_value=NS(start=Mock()))),
            lipsync_service=NS(lipsync_worker=Mock()),ELEVENLABS_API_KEY='synthetic',FAL_API_KEY='synthetic',
            DASHSCOPE_API_KEY='synthetic',DASHSCOPE_WORKSPACE_ID='synthetic',DASHSCOPE_REGION='synthetic',_wm_needed=lambda _:False))
        return env,NS(job_id=JOB_A,resolution='720p')
    def test_unconfirmed_lipsync_payment_starts_no_worker(self):
        env,req=self.lip_env()
        self.assertEqual(env['lipsync'](req,object()).status_code,503)
        env['threading'].Thread.assert_not_called()
    def test_unknown_lipsync_balance_starts_no_debit_or_worker(self):
        env,req=self.lip_env();env['get_credits'].return_value=None
        self.assertEqual(env['lipsync'](req,object()).status_code,503)
        env['deduct_credits'].assert_not_called();env['threading'].Thread.assert_not_called()
    def test_background_watcher_never_reports_an_unconfirmed_charge_as_paid(self):
        from types import SimpleNamespace as NS
        env=source_functions('main.py',['_watch_and_deduct'],dict(jobs_progress={JOB_A:{'status':'done'}},
            _abandoned_jobs=set(),_get_pricing_config=lambda:{'transcribeCredits':3},deduct_credits=Mock(return_value=None),
            debit_confirmed=lambda value:type(value)is int and value>=0,_job_charges={},
            threading=NS(Thread=lambda target,**kwargs:NS(start=target))))
        env['_watch_and_deduct'](JOB_A,'user','transcribe')
        self.assertEqual(env['_job_charges'][JOB_A],{'credits_charged':0,'payment_pending':True})
    def test_invalid_balance_and_subscription_values_never_reach_database(self):
        env=source_functions('main.py',['set_credits','_set_subscription_credits','_grant_subscription_credits'],
             {'SUPABASE_URL':'https://example.test','SUPABASE_SERVICE_KEY':'synthetic'})
        for value in (-1,'bad',True,float('nan'),float('inf'),1.5):
            with patch('urllib.request.urlopen') as network:
                self.assertFalse(env['set_credits'](UID,value))
                self.assertFalse(env['_set_subscription_credits'](UID,value))
                self.assertIsNone(env['_grant_subscription_credits'](UID,'invoice',value))
                network.assert_not_called()
    def admin_env(self,current=50):
        return source_functions('main.py',['admin_adjust_credits'],dict(_admin_check=Mock(return_value=True),
            _get_permanent_credits=Mock(return_value=current),set_credits=Mock(return_value=True),
            _log_spend=Mock(return_value=True),_log_audit=Mock(return_value=True),JSONResponse=Response))
    def test_invalid_signed_admin_adjustment_is_a_clear_400(self):
        for delta in ('bad',1.5,True,float('nan'),float('inf'),0,10001):
            env=self.admin_env(); req=Mock(json=AsyncMock(return_value={'uid':UID,'delta':delta,'reason':'Test'}))
            self.assertEqual(asyncio.run(env['admin_adjust_credits'](req)).status_code,400)
            env['set_credits'].assert_not_called()
    def test_balance_outage_does_not_replace_an_unknown_balance(self):
        env=self.admin_env(None); req=Mock(json=AsyncMock(return_value={'uid':UID,'delta':10,'reason':'Test'}))
        self.assertEqual(asyncio.run(env['admin_adjust_credits'](req)).status_code,503)
        env['set_credits'].assert_not_called()
    def test_admin_history_sign_and_clamping_match_the_actual_balance_change(self):
        env=self.admin_env(5); req=Mock(json=AsyncMock(return_value={'uid':UID,'delta':-10,'reason':'Test'}))
        result=asyncio.run(env['admin_adjust_credits'](req))
        self.assertEqual(result['adjusted_credits'],-5)
        env['_log_spend'].assert_called_once_with(UID,'admin_adjustment',5,job_id=None,reason='Test')
        env['_log_audit'].assert_called_once_with(UID,-5,'Test')


if __name__=='__main__': unittest.main()
