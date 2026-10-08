"""Synthetic intent/restart/refund tests; no provider runs. Credits return whenever no video was delivered."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import lipsync_operations as lo
from shortdub_operations import RequestProblem,read,write
from billing_action_fixtures import UID,OP,begin_receipt
from test_shortdub_upgrade import JOB_A


class LipSyncIntentTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        self.req=NS(job_id=JOB_A,resolution='720p',operation_id=OP)
        self.prepare=Mock(return_value=10);self.charge=Mock(return_value=90);self.launch=Mock()

    def run_intent(self,progress=None,rpc=begin_receipt):
        return lo.run(self.root,self.req,UID,'synthetic fingerprint',self.prepare,self.charge,self.launch,progress,rpc)

    def test_retry_and_new_uuid_in_flight_start_only_one_worker_and_debit(self):
        first=self.run_intent();again=self.run_intent({'status':'processing'})
        self.assertEqual(first['operation_id'],again['operation_id']);self.assertTrue(again['replayed'])
        self.req.operation_id='00000000-0000-4000-9000-000000000003'
        with self.assertRaises(RequestProblem) as ex:self.run_intent({'status':'processing'})
        self.assertEqual(ex.exception.status,409);self.charge.assert_called_once();self.launch.assert_called_once()

    def test_lost_debit_reply_replays_same_id_and_blocks_other_intent(self):
        self.charge.return_value=None
        with self.assertRaises(RequestProblem):self.run_intent()
        self.req.operation_id='00000000-0000-4000-9000-000000000003'
        with self.assertRaises(RequestProblem):self.run_intent()
        self.req.operation_id=OP;self.charge.return_value=90;self.run_intent()
        self.assertEqual({c.args[1] for c in self.charge.call_args_list},{OP});self.launch.assert_called_once()

    def test_restart_of_running_job_is_unknown_and_never_starts_again(self):
        self.run_intent()
        with self.assertRaises(RequestProblem) as ex:self.run_intent(None)
        self.assertEqual(ex.exception.status,503);self.launch.assert_called_once();self.charge.assert_called_once()
        self.assertFalse(lo.saved_progress(self.root,JOB_A)['operation_complete'])

    def test_completed_retry_returns_old_take_but_new_id_makes_new_take(self):
        self.run_intent();path,_=lo.paths(self.root,JOB_A,OP)
        final=self.root/(JOB_A+'_final_lipsync.mp4');final.write_bytes(b'synthetic output')
        lo.finish(path,self.root,{'status':'done','result':{'video':final.name}})
        self.assertTrue(self.run_intent()['replayed']);self.charge.assert_called_once()
        self.req.operation_id='00000000-0000-4000-9000-000000000003';self.run_intent()
        self.assertEqual((self.charge.call_count,self.launch.call_count),(2,2))

    def test_missing_completed_asset_refuses_replay_without_new_charge(self):
        self.run_intent();path,_=lo.paths(self.root,JOB_A,OP)
        final=self.root/(JOB_A+'_final_lipsync.mp4');final.write_bytes(b'synthetic output')
        lo.finish(path,self.root,{'status':'done','result':{'video':final.name}});final.unlink()
        with self.assertRaises(RequestProblem) as ex:self.run_intent()
        self.assertEqual(ex.exception.status,410);self.charge.assert_called_once()

    def test_missing_sql_or_unconfirmed_payment_starts_no_worker(self):
        with self.assertRaises(RequestProblem):self.run_intent(rpc=Mock(return_value=None))
        self.charge.assert_not_called();self.launch.assert_not_called()

    def test_old_page_missing_intent_id_gets_a_clear_reload_message(self):
        self.req.operation_id=None
        with self.assertRaises(RequestProblem) as ex:self.run_intent()
        self.assertEqual(ex.exception.status,400);self.assertIn('lip-sync',str(ex.exception))
        self.charge.assert_not_called();self.launch.assert_not_called()

    def test_unconfirmed_worker_result_stays_unresolved(self):
        self.run_intent();path,_=lo.paths(self.root,JOB_A,OP)
        self.assertEqual(lo.finish(path,self.root,{'status':'processing'}),'unknown')
        self.assertFalse(lo.saved_progress(self.root,JOB_A)['operation_complete'])
        with self.assertRaises(RequestProblem):self.run_intent({'status':'error'})
        self.charge.assert_called_once();self.launch.assert_called_once()

    def test_sql_completed_receipt_without_local_outcome_cannot_start_again(self):
        rpc=lambda name,args,**k:dict(begin_receipt(name,args),status='done')
        with self.assertRaises(RequestProblem) as ex:self.run_intent(rpc=rpc)
        self.assertEqual(ex.exception.status,410);self.charge.assert_not_called();self.launch.assert_not_called()

    def test_known_worker_failure_without_a_refund_callback_adds_no_refund(self):
        self.run_intent();path,_=lo.paths(self.root,JOB_A,OP)
        lo.finish(path,self.root,{'status':'error','error':'synthetic provider detail'})
        progress=lo.saved_progress(self.root,JOB_A)
        self.assertTrue(progress['operation_complete']);self.assertNotIn('provider',progress['error'])
        self.assertEqual(self.charge.call_count,1);self.assertNotIn('refund',read(path))

    def failed_run(self,provider_video,refund,**extra):
        self.run_intent();path,_=lo.paths(self.root,JOB_A,OP)
        progress={'status':'error','error':'synthetic provider detail'}
        if provider_video is not None:progress['provider_video']=provider_video
        lo.finish(path,self.root,progress,refund=refund,**extra)
        return path,progress

    def test_no_video_from_provider_refunds_the_charge_once_with_the_payment_id(self):
        refund=Mock(return_value={'ok':True});path,progress=self.failed_run(False,refund)
        refund.assert_called_once_with(10,OP);record=read(path)
        self.assertEqual(record['refund'],{'amount':10,'confirmed':True});self.assertEqual(record['status'],'failed')
        self.assertIn('returned',progress['error']);self.assertIn('returned',lo.saved_progress(self.root,JOB_A)['error'])
        self.assertNotIn('provider',progress['error'])

    def test_our_own_finishing_failure_also_refunds_because_no_video_was_delivered(self):
        refund=Mock(return_value={'ok':True});path,progress=self.failed_run(True,refund)
        refund.assert_called_once_with(10,OP);self.assertTrue(read(path)['refund']['confirmed']);self.assertIn('were returned',progress['error'])

    def test_a_delivered_video_is_never_refunded(self):
        self.run_intent();path,_=lo.paths(self.root,JOB_A,OP);final=self.root/(JOB_A+'_final_lipsync.mp4');final.write_bytes(b'v')
        refund=Mock();lo.finish(path,self.root,{'status':'done','result':{'video':final.name}},refund=refund)
        refund.assert_not_called();self.assertEqual(read(path)['status'],'done');self.assertNotIn('refund',read(path))

    def test_unknown_outcome_waits_for_a_person_and_is_never_refunded(self):
        self.run_intent();path,_=lo.paths(self.root,JOB_A,OP);refund=Mock()
        self.assertEqual(lo.finish(path,self.root,{'status':'processing'},refund=refund),'unknown')
        refund.assert_not_called()

    def test_unconfirmed_refund_is_honest_and_recorded_for_the_background_retry(self):
        for refund in (Mock(return_value=None),Mock(side_effect=RuntimeError('synthetic'))):
            with self.subTest(refund=refund):
                folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
                path,progress=self.failed_run(False,refund)
                self.assertEqual(read(path)['refund']['confirmed'],False);self.assertEqual(read(path)['status'],'failed')
                self.assertIn('will be returned',progress['error']);self.assertNotIn('were returned',progress['error'])

    def test_nothing_to_refund_when_nothing_was_charged(self):
        path,_=lo.paths(self.root,JOB_A,OP);refund=Mock()
        write(path,{'operation_id':OP,'uid':UID,'work':'x','status':'running','amount':0,'result':{}})
        lo.finish(path,self.root,{'status':'error','provider_video':False},refund=refund)
        refund.assert_not_called()

    def test_disk_failure_before_launch_keeps_payment_identity_for_recovery(self):
        original=lo.write
        def save(path,data):
            if data.get('status')=='running':raise RequestProblem('Synthetic disk outage',503)
            original(path,data)
        with patch.object(lo,'write',side_effect=save):
            with self.assertRaises(RequestProblem):self.run_intent()
        self.launch.assert_not_called();self.charge.assert_called_once()
        with self.assertRaises(RequestProblem):self.run_intent()
        self.charge.assert_called_once()

    def test_changed_work_is_refused_and_cleanup_keeps_intents(self):
        self.run_intent();path,_=lo.paths(self.root,JOB_A,OP)
        record=read(path);record['work']='other';write(path,record)
        with self.assertRaises(RequestProblem) as ex:self.run_intent({'status':'processing'})
        self.assertEqual(ex.exception.status,409)
        os.utime(path.parent,(1,1));lo.maintain_requests(self.root)
        self.assertGreater(path.parent.stat().st_mtime,1);self.assertTrue(path.exists())
