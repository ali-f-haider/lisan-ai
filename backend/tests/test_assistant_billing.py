"""Synthetic checkpoint, lost reply, disk failure and restart tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import assistant_billing as ab
import credit_billing as cb
from billing_action_fixtures import UID
from test_credit_billing import AtomicLedger


class AssistantCheckpointTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)

    def ledger(self,owed=1):
        ledger=ab.Ledger(self.root,UID);ledger.save(owed=str(owed));return ledger

    def test_pending_uuid_and_amount_are_on_disk_before_debit(self):
        ledger=self.ledger(2)
        def debit(amount,operation_id):
            saved=ab.Ledger(self.root,UID).state
            self.assertEqual(saved['pending'],{'amount':2,'operation_id':operation_id})
            self.assertEqual(saved['owed'],'2');return 98
        self.assertEqual(ledger.settle(debit,100),(98,2,False))

    def test_lost_replies_and_process_restart_replay_one_uuid(self):
        ledger=self.ledger(2);db=AtomicLedger(0,100);lost=0;ids=[]
        def rpc(name,args,**kwargs):
            nonlocal lost
            result=db.rpc(name,args,**kwargs)
            if name=='lisan_atomic_debit' and lost<2:lost+=1;raise TimeoutError()
            return result
        def debit(amount,operation_id):
            ids.append(operation_id);return cb.debit(rpc,UID,amount,'assistant',operation_id=operation_id)
        self.assertTrue(ledger.settle(debit,100)[2])
        restored=ab.Ledger(self.root,UID)
        self.assertEqual(restored.settle(debit,98),(98,2,False))
        self.assertEqual(len(set(ids)),1);self.assertEqual(sum(x['credits'] for x in db.history),2)

    def test_failed_checkpoint_after_confirmed_debit_keeps_original_id(self):
        ledger=self.ledger();ids=[]
        def debit(amount,operation_id):ids.append(operation_id);return 99
        original=ab.write
        def save(path,state):
            if state['pending'] is None and state['owed']=='0':raise ab.CheckpointProblem('Synthetic disk failure')
            return original(path,state)
        with patch.object(ab,'write',side_effect=save):
            with self.assertRaises(ab.CheckpointProblem):ledger.settle(debit,100)
        self.assertEqual(ab.Ledger(self.root,UID).settle(debit,99),(99,1,False))
        self.assertEqual(len(set(ids)),1)

    def test_failure_before_debit_starts_no_charge(self):
        ledger=self.ledger();debit=Mock()
        with patch.object(ab.os,'replace',side_effect=OSError()):
            with self.assertRaises(ab.CheckpointProblem):ledger.settle(debit,100)
        debit.assert_not_called();self.assertIsNone(ab.Ledger(self.root,UID).state['pending'])

    def test_unfinished_provider_request_is_manual_review_not_another_call(self):
        ledger=self.ledger(0);ledger.begin_question();restored=ab.Ledger(self.root,UID)
        with self.assertRaises(ab.CheckpointProblem):restored.settle(Mock(),100)
        with self.assertRaises(ab.CheckpointProblem):restored.begin_question()

    def test_fractions_and_all_legacy_owed_credits_survive_migration(self):
        (self.root/'assistant_owed.json').write_text(json.dumps({UID:8.75}),encoding='utf-8')
        ledger=ab.Ledger(self.root,UID)
        self.assertEqual(ledger.settle(lambda *a:92,100),(92,8,False))
        self.assertEqual(ab.Ledger(self.root,UID).state['owed'],'0.75')
        ledger.begin_question();ledger.finish_question('0.25')
        self.assertEqual(ab.Ledger(self.root,UID).state['owed'],'1.00')

    def test_confirmed_insufficient_payment_preserves_debt_and_allows_funded_new_id(self):
        ledger=self.ledger();seen=[]
        def refuse(amount,operation_id):seen.append(operation_id);return False
        self.assertTrue(ledger.settle(refuse,100)[2]);self.assertEqual(ledger.state['owed'],'1')
        ledger.settle(lambda a,op:seen.append(op) or 99,100)
        self.assertEqual(len(set(seen)),2)

    def test_corrupt_or_nonfinite_debt_is_never_forgotten(self):
        for value in [float('nan'),float('inf'),-1,True]:
            (self.root/'assistant_owed.json').write_text(json.dumps({UID:value}),encoding='utf-8')
            with self.assertRaises(ab.CheckpointProblem):ab.Ledger(self.root,UID)

    def test_recovered_historical_balance_does_not_authorize_new_question(self):
        from test_credit_audit import CreditAuditProofs
        from test_shortdub_upgrade import source_functions,Response
        from types import SimpleNamespace as NS
        ledger=self.ledger();ledger.settle(lambda *a:None,100)
        provider=Mock()
        env=source_functions('main.py',['assistant_chat'],dict(_current_uid=lambda _:UID,
            _billing_pause_response=lambda:None,_rate_limited=lambda *a:False,DATA_DIR=self.root,
            assistant_service=NS(SETTINGS={'credits_per_cent':1},clean_messages=lambda x:x,handle=provider,add_charged=Mock()),
            get_credits=Mock(side_effect=[0,0]),deduct_credits=lambda *a,**k:99,
            debit_confirmed=lambda v:type(v)is int and v>=0,
            JSONResponse=lambda body,**k:Response(body,k.get('status_code',200))))
        result=env['assistant_chat'](NS(messages=[{}]),object())
        self.assertTrue(result.content['nocredits']);provider.assert_not_called()
