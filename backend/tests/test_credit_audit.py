"""Offline 6c proofs. Reported failures intentionally assert required safety.

No live app import, database, provider, or payment request. No hidden failures.
"""
import asyncio
import io
import json
import re
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import credit_billing as cb
from test_credit_billing import AtomicLedger, UID
from test_shortdub_upgrade import source_functions, Response, JOB_A


class CreditAuditProofs(unittest.TestCase):
    def subscription(self, write=None):
        state={'subscription':0,'permanent':0,'invoices':set()}
        def open_request(request,**kwargs):
            if request.get_method()=='GET':
                data=[{'invoice_id':'invoice'}] if state['invoices'] else []
            else:
                state['invoices'].add(json.loads(request.data)['invoice_id']); data={}
            return io.BytesIO(json.dumps(data).encode())
        def grant(uid,amount):
            if write is not None: return write(uid,amount)
            state['subscription']=amount; return True
        def permanent(name,args,**kwargs):
            state['permanent']+=args['amount']; return state['permanent']
        env=source_functions('main.py',['_grant_subscription_credits'],dict(
            SUPABASE_URL='https://example.test',SUPABASE_SERVICE_KEY='synthetic',json=json,
            _reset_subscription_clone_usage=Mock(),_set_subscription_credits=grant,
            _sb_rpc=permanent,_http_err_detail=str))
        return env,state,open_request
    def test_subscription_receipt_does_not_hide_a_failed_grant_on_retry(self):
        env,state,network=self.subscription(write=lambda *_:False)
        env['_sb_rpc']=Mock(return_value=None)
        with patch('urllib.request.urlopen',side_effect=network):
            env['_grant_subscription_credits'](UID,'invoice',120)
            env['_grant_subscription_credits'](UID,'invoice',120)
        self.assertEqual(state['subscription'],120,'A retry must complete an invoice whose first grant failed.')
    def test_subscription_write_outage_does_not_turn_expiring_credits_permanent(self):
        env,state,network=self.subscription(write=lambda *_:False)
        with patch('urllib.request.urlopen',side_effect=network): env['_grant_subscription_credits'](UID,'invoice',120)
        self.assertEqual(state['permanent'],0,'A failed renewal write must not silently change the credit bucket.')
    def test_reused_subscription_invoice_checks_user_and_amount(self):
        env,state,network=self.subscription()
        with patch('urllib.request.urlopen',side_effect=network):
            env['_grant_subscription_credits'](UID,'invoice',120)
            result=env['_grant_subscription_credits']('another account','invoice',240)
        self.assertNotEqual(result,'already-fulfilled','An invoice for different work must be refused, not reported fulfilled.')
    def admin(self,state):
        return source_functions('main.py',['admin_adjust_credits'],dict(_admin_check=Mock(return_value=True),
            _get_permanent_credits=lambda _:state['credits'],
            set_credits=lambda _,amount:state.update(credits=amount) or True,
            _log_spend=Mock(return_value=True),_log_audit=Mock(return_value=True),JSONResponse=Response))
    def admin_request(self):
        return Mock(json=AsyncMock(return_value={'uid':UID,'delta':50,'reason':'Synthetic test',
                                                'operation_id':'same-intent'}))
    def test_admin_adjustment_does_not_overwrite_a_concurrent_debit(self):
        state={'credits':100}; env=self.admin(state)
        def concurrent(uid,amount):
            state['credits']-=50  # A different atomic paid action committed after the admin read.
            state['credits']=amount; return True
        env['set_credits']=concurrent
        asyncio.run(env['admin_adjust_credits'](self.admin_request()))
        self.assertEqual(state['credits'],100,'100 plus grant 50 minus concurrent spend 50 must be 100.')
    def test_admin_lost_response_replays_one_adjustment(self):
        state={'credits':100}; env=self.admin(state); req=self.admin_request()
        asyncio.run(env['admin_adjust_credits'](req)); asyncio.run(env['admin_adjust_credits'](req))
        self.assertEqual(state['credits'],150,'One admin intent retried after a lost response must grant once.')
    def test_admin_history_failure_cannot_commit_an_unlogged_change(self):
        state={'credits':100}; env=self.admin(state); env['_log_spend'].return_value=False
        asyncio.run(env['admin_adjust_credits'](self.admin_request()))
        self.assertEqual(state['credits'],100,'Balance and spend/audit records need one transaction.')
    def test_assistant_lost_debit_replies_do_not_charge_the_same_owed_credit_again(self):
        ledger=AtomicLedger(0,100); lost=0
        def rpc(name,args,strict=False):
            nonlocal lost
            result=ledger.rpc(name,args,strict)
            if name=='lisan_atomic_debit' and lost<2:
                lost+=1; raise TimeoutError('Synthetic lost committed debit reply')
            return result
        service=NS(SETTINGS={'credits_per_cent':1},clean_messages=lambda m:m,
                   handle=lambda *args:{'ok':True,'answer':'Synthetic answer','usd':0.01},add_charged=Mock())
        env=source_functions('main.py',['assistant_chat'],dict(_current_uid=lambda _:UID,_billing_pause_response=lambda:None,
            _rate_limited=lambda *args:False, assistant_service=service, get_credits=lambda _:ledger.balance['permanent'],
            _assistant_account_text=lambda *args:'',_assistant_pricing_text=lambda:'',_assist_owed={},
            _assist_owed_lock=threading.Lock(),_assist_owed_save=Mock(),_assistant_acct_cache={},
            deduct_credits=lambda *args,**kwargs:cb.debit(rpc,*args,**kwargs),debit_confirmed=lambda b:type(b)is int and b>=0,
            JSONResponse=lambda body,**kwargs:Response(body,kwargs.get('status_code',200))))
        req=NS(messages=[{'role':'user','text':'Synthetic'}],job_id='',lang='en',page='app')
        env['assistant_chat'](req,object()); env['assistant_chat'](req,object())
        self.assertEqual(sum(h['credits'] for h in ledger.history),2,
                         'Two one-credit questions must not debit three after a lost first response.')
    def test_signup_grant_tracks_the_configured_free_credit_amount(self):
        # Execute the supplied trigger's literal insert in an offline signup
        # mock. SQL integration also executes this actual supplied function.
        sql=(Path(__file__).parent/'fixtures/signup_credit_trigger.sql').read_text(encoding='utf-8')
        amount=int(re.search(r"new\.email\),\s*(\d+)\)",sql).group(1))
        configured=250
        profiles={}; signup=Mock(side_effect=lambda uid:profiles.update({uid:{'credits':amount}}))
        signup(UID)
        self.assertEqual(profiles[UID]['credits'],configured,'Changing admin freeCredits must change the signup grant too.')
    def test_inflight_lipsync_click_does_not_charge_and_start_a_second_job(self):
        from test_shortdub_operations import InputBoundaryTests
        fixture=InputBoundaryTests();env,req=fixture.lip_env()
        try:
            env['deduct_credits'].return_value=90
            env['lipsync'](req,object());env['lipsync'](req,object())
            self.assertEqual(env['deduct_credits'].call_count,1,'A second click while lip-sync is running must not buy another job.')
            self.assertEqual(env['threading'].Thread.call_count,1)
        finally: fixture.doCleanups()


if __name__=='__main__': unittest.main()
