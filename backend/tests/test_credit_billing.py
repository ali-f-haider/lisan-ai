"""Offline money-safety tests. The ledger below MODELS, not executes, SQL.

No main/config import, real database, provider, email or paid call.
"""
import copy
import asyncio
import io
import json
import tempfile
import threading
import unittest
import uuid
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError

import credit_billing as cb
from test_shortdub_upgrade import source_functions, Response

ROOT = Path(__file__).resolve().parents[1]
UID = '00000000-0000-4000-8000-000000000001'
OP = '00000000-0000-4000-8000-000000000002'


class AtomicLedger:
    """Offline transactional reference model used by route and proof tests.

    The supplied old RPC bodies remain in fixtures as historical evidence.
    These tests exercise the actual Python callers with the new RPC contract.
    They do not establish that the SQL draft ran in PostgreSQL.
    """
    def __init__(self, subscription=5, permanent=10):
        self.balance = {'subscription': subscription, 'permanent': permanent}
        self.operations, self.orders, self.history = {}, {}, []
        self.legacy_orders = set()
        self.lock = threading.RLock()
        self.fail_history = False
        self.fail_pack = False
        self.calls = []

    def rpc(self, name, args, strict=False):
        with self.lock:
            self.calls.append((name,copy.deepcopy(args)))
            snapshot = copy.deepcopy((self.balance,self.operations,self.orders,self.history))
            try:
                return copy.deepcopy(self._transaction(name,args))
            except Exception:
                self.balance,self.operations,self.orders,self.history = snapshot
                raise

    def _transaction(self, name, a):
        if name == 'lisan_billing_ready':
            return {'version':1, 'debit':True,'pack':True,'refund':True,'cancel':True}
        if name == 'lisan_pending_clone_refunds':
            return [{'operation_id':op['operation_id'],'uid':op['uid'],'refund_due':op['refund_due']}
                    for op in self.operations.values() if op['kind']=='debit' and op['action'] in ('clone','custom_voice')
                    and op.get('refund_due',0)>sum(r['amount'] for r in self.operations.values()
                    if r.get('debit_id')==op['operation_id'] and r['status']=='done')]
        if name == 'lisan_fulfill_pack':
            receipt,uid,amount = a['p_receipt'],a['p_uid'],a['p_amount']
            if not uid or not receipt or type(amount) is not int or amount <= 0: raise ValueError('invalid')
            old = self.orders.get(receipt)
            if old and (old['uid'] != uid or old['amount'] != amount): raise ValueError('receipt conflict')
            if old and old['status']=='done': return dict(old,status='already_fulfilled')
            result = {'uid':uid,'receipt':receipt,'amount':amount,'status':'pending'}
            self.orders[receipt]=result
            if receipt in self.legacy_orders: return dict(result,status='legacy_review')
            if self.fail_pack: raise OSError('mock grant transaction failure')
            self.balance['permanent']+=amount
            self.orders[receipt]=dict(result,status='done',permanent_balance=self.balance['permanent'])
            return self.orders[receipt]
        if name == 'lisan_cancel_clone_debit':
            oid,uid,amount=a['p_operation_id'],a['p_uid'],a['p_amount']
            op=self.operations.get(oid)
            if op is None:
                op={'operation_id':oid,'uid':uid,'amount':amount,'kind':'debit','status':'failed','reason':'cancelled',
                    'action':None,'taken_subscription':0,'taken_permanent':0}
                self.operations[oid]=op
            if (op['uid'],op['amount'],op['kind'])!=(uid,amount,'debit'):raise ValueError('ID conflict')
            if op['status']=='done':op.update(work_status='failed',refund_due=amount)
            return op
        if name == 'lisan_credit_finish':
            op=self.operations[a['p_operation_id']]
            state,due=a['p_work_status'],a['p_refund_due']
            if op['status']!='done' or not 0 <= due <= op['amount']: raise ValueError('invalid settlement')
            if op.get('work_status','pending')!='pending' and (op['work_status']!=state or op['refund_due']!=due):
                raise ValueError('settlement conflict')
            op.update(work_status=state,refund_due=due)
            return op
        if name not in ('lisan_atomic_debit','lisan_credit_refund'):
            raise AssertionError('Legacy/unexpected RPC: '+name)
        uid,amount,oid=a['p_uid'],a['p_amount'],a['p_operation_id']
        if type(amount) is not int or not 0 < amount <= cb.MAX_CREDITS: raise ValueError('invalid amount')
        kind='debit' if name=='lisan_atomic_debit' else 'refund'
        old=self.operations.get(oid)
        if old:
            if (old['uid'],old['amount'],old['kind']) != (uid,amount,kind): raise ValueError('ID conflict')
            if kind=='debit' and old['action'] is not None and (old['action'],old.get('job_id')) != (a['p_action'],a.get('p_job_id')):
                raise ValueError('work conflict')
            if kind=='refund' and (old['debit_id'],old['split_rule']) != (a['p_debit_id'],a['p_split_rule']):
                raise ValueError('refund conflict')
            return dict(old,replayed=True)
        result={'uid':uid,'amount':amount,'operation_id':oid,'kind':kind,'status':'done','replayed':False}
        sub,perm=self.balance['subscription'],self.balance['permanent']
        if kind=='debit':
            result.update(action=a['p_action'],job_id=a.get('p_job_id'),work_status='pending',refund_due=0)
            if sub+perm < amount:
                result.update(status='failed',reason='insufficient',taken_subscription=0,taken_permanent=0,
                              subscription_balance=sub,permanent_balance=perm)
                self.operations[oid]=result
                return result
            st,pt=min(sub,amount),max(0,amount-sub)
            self.balance['subscription']-=st; self.balance['permanent']-=pt
            signed=amount
        else:
            orig=self.operations[a['p_debit_id']]
            if orig['uid'] != uid or orig['kind']!='debit' or orig['status']!='done': raise ValueError('original')
            refunds=[r for r in self.operations.values() if r.get('debit_id')==a['p_debit_id'] and r['status']=='done']
            ps,pp=sum(r['taken_subscription'] for r in refunds),sum(r['taken_permanent'] for r in refunds)
            if ps+pp+amount > orig['refund_due']: raise ValueError('excess refund')
            if a['p_split_rule']=='subscription_first': st=min(amount,orig['taken_subscription']-ps)
            else:
                cumulative=((ps+pp+amount)*orig['taken_subscription']+orig['amount']-1)//orig['amount']
                st=min(amount,max(0,cumulative-ps),orig['taken_subscription']-ps)
                st=max(st,amount-(orig['taken_permanent']-pp))
            pt=amount-st
            self.balance['subscription']+=st; self.balance['permanent']+=pt
            result.update(debit_id=a['p_debit_id'],split_rule=a['p_split_rule'])
            signed=-amount
        if self.fail_history: raise OSError('mock history insert failure')
        self.history.append({'operation_id':oid,'credits':signed})
        result.update(taken_subscription=st,taken_permanent=pt,
                      subscription_balance=self.balance['subscription'],permanent_balance=self.balance['permanent'])
        self.operations[oid]=result
        return result


class CreditBillingTests(unittest.TestCase):
    def test_main_helper_returns_confirmed_atomic_bucket_split(self):
        db=AtomicLedger()
        env=source_functions('main.py',['deduct_credits'],{'_sb_rpc':db.rpc})
        self.assertEqual(env['deduct_credits'](UID,8,operation_id=OP),7)
        op=db.operations[OP]
        self.assertEqual((op['taken_subscription'],op['taken_permanent']),(5,3))
        self.assertEqual(len(db.history),1)

    def test_sql_not_deployed_pauses_without_any_legacy_fallback(self):
        rpc=Mock(side_effect=HTTPError('https://database.invalid',404,'missing',{},io.BytesIO(b'{}')))
        self.assertIsNone(cb.debit(rpc,UID,5))
        self.assertIsNone(cb.fulfill(rpc,UID,'receipt',100))
        self.assertFalse(cb.ready(rpc))
        self.assertEqual([c.args[0] for c in rpc.call_args_list],['lisan_atomic_debit','lisan_fulfill_pack','lisan_billing_ready'])

    def test_paid_checkout_and_subscribe_start_no_payment_without_sql(self):
        for name in ('billing_checkout','billing_subscribe','billing_change_plan'):
            with self.subTest(route=name):
                payments=Mock()
                env=source_functions('main.py',[name],{'stripe':payments,'STRIPE_SECRET_KEY':'mock',
                    '_current_uid':lambda _:UID,'_billing_pause_response':lambda:Response({'error':cb.UNAVAILABLE},503),
                    'JSONResponse':Response})
                result=env[name]({},object()) if name=='billing_checkout' else env[name](object())
                self.assertEqual(result.status_code,503)
                self.assertEqual(payments.mock_calls,[])

    def test_paid_account_gate_pauses_before_provider_work(self):
        env=source_functions('main.py',['_paid_uid','_billing_pause_response'],{'_current_uid':lambda _:UID,
            '_sb_rpc':Mock(return_value=None),'JSONResponse':Response})
        uid,response=env['_paid_uid'](object())
        self.assertIsNone(uid);self.assertEqual(response.status_code,503)

    def test_invalid_amounts_never_call_database(self):
        for amount in (0,-1,True,False,'bad','',None,1.2,float('nan'),float('inf'),cb.MAX_CREDITS+1):
            with self.subTest(amount=repr(amount)):
                rpc=Mock()
                with self.assertRaises(ValueError): cb.debit(rpc,UID,amount)
                rpc.assert_not_called()

    def test_zero_remaining_balance_is_still_a_confirmed_full_debit(self):
        db=AtomicLedger(3,2)
        self.assertEqual(cb.debit(db.rpc,UID,5),0)
        self.assertEqual(db.balance,{'subscription':0,'permanent':0})

    def test_lost_committed_reply_replays_same_id_once(self):
        db=AtomicLedger()
        calls=[]
        def rpc(name,args,strict=False):
            calls.append(args['p_operation_id'])
            result=db.rpc(name,args,strict)
            if len(calls)==1: raise TimeoutError('mock lost reply after commit')
            return result
        self.assertEqual(cb.debit(rpc,UID,8),7)
        self.assertEqual(calls[0],calls[1]);self.assertEqual(len(db.history),1)

    def test_history_write_failure_rolls_back_the_debit(self):
        db=AtomicLedger();db.fail_history=True
        self.assertIsNone(cb.debit(db.rpc,UID,8))
        self.assertEqual(db.balance,{'subscription':5,'permanent':10})
        self.assertEqual(db.operations,{});self.assertEqual(db.history,[])

    def test_split_mismatch_and_json_unsafe_receipts_are_unconfirmed(self):
        db=AtomicLedger();real=db.rpc('lisan_atomic_debit',{'p_operation_id':OP,'p_uid':UID,'p_amount':8,'p_action':'deduction'})
        for change in ({'taken_permanent':2},{'taken_subscription':True},{'permanent_balance':float('nan')},
                       {'amount':9},{'uid':'other'},{'kind':'refund'},{'operation_id':str(uuid.uuid4())}):
            with self.subTest(change=change):
                self.assertIsNone(cb.debit(Mock(return_value=dict(real,**change)),UID,8,operation_id=OP))

    def test_same_operation_can_be_replayed_after_restart(self):
        db=AtomicLedger()
        self.assertEqual(cb.debit(db.rpc,UID,8,operation_id=OP),7)
        self.assertEqual(cb.debit(db.rpc,UID,8,operation_id=OP),7)
        self.assertEqual(len(db.history),1)
        self.assertIsNone(cb.debit(db.rpc,UID,9,operation_id=OP))
        self.assertEqual(db.balance,{'subscription':0,'permanent':7})

    def test_duplicate_pack_receipt_grants_once_and_rejects_mismatched_details(self):
        db=AtomicLedger()
        self.assertEqual(cb.fulfill(db.rpc,UID,'purchase',100),110)
        self.assertEqual(cb.fulfill(db.rpc,UID,'purchase',100),'already-fulfilled')
        self.assertIsNone(cb.fulfill(db.rpc,UID,'purchase',200))
        self.assertIsNone(cb.fulfill(db.rpc,'other','purchase',100))
        self.assertEqual(db.balance['permanent'],110)

    def test_failed_pack_grant_leaves_no_completed_receipt_and_is_recoverable(self):
        db=AtomicLedger();db.fail_pack=True
        self.assertIsNone(cb.fulfill(db.rpc,UID,'purchase',100));self.assertEqual(db.orders,{})
        db.fail_pack=False
        self.assertEqual(cb.fulfill(db.rpc,UID,'purchase',100),110)

    def test_duplicate_concurrent_pack_delivery_grants_once(self):
        db=AtomicLedger()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:cb.fulfill(db.rpc,UID,'purchase',100),range(2)))
        self.assertCountEqual(results,[110,'already-fulfilled']);self.assertEqual(db.balance['permanent'],110)

    def test_legacy_receipt_is_left_for_review_without_a_guessed_grant(self):
        db=AtomicLedger();db.legacy_orders.add('old')
        self.assertEqual(cb.fulfill(db.rpc,UID,'old',100),'needs-review')
        self.assertEqual(db.balance['permanent'],10)
        self.assertEqual(db.orders['old']['status'],'pending')

    def test_admin_invalid_prices_are_refused_before_any_write(self):
        env=source_functions('main.py',['_save_pricing_config'],{'SUPABASE_URL':'https://database.invalid','SUPABASE_SERVICE_KEY':'mock'})
        cases=[{'cloneCredits':-1},{'transcribeCredits':'wrong'},{'charsPerCredit':0},{'geminiCreditsPerCent':float('nan')},
               {'longDubFlatCredits':-1},{'packs':[{'credits':100,'price_usd':-5}]},
               {'subscriptionPlans':[{'credits_per_month':100,'price_usd':'bad'}]},
               {'assistant':{'creditsPerCent':-1}},{'mergeCredits':1,'assistant':{'dailyBudgetUsd':float('inf')}}]
        for cfg in cases:
            with self.subTest(cfg=cfg),patch('urllib.request.urlopen') as network:
                ok,message=env['_save_pricing_config'](cfg)
                self.assertFalse(ok);self.assertTrue(message);network.assert_not_called()

    def test_only_intended_free_pricing_fields_accept_zero(self):
        cfg={'freeCredits':1,'minReserve':0,'longDubFlatCredits':0,'longDubAnalysisPerMin':0,
             'assistant':{'creditsPerCent':0,'dailyBudgetUsd':0}}
        self.assertEqual(cb.validate_pricing(cfg),cfg)
        for key in ('freeCredits','transcribeCredits','cloneCredits','inworldCloneCredits','charsPerCredit','subscriptionPriceUsd','musicFillCredits','mergeCredits'):
            with self.subTest(key=key),self.assertRaises(ValueError):cb.validate_pricing({key:0})

    def test_admin_valid_numeric_strings_are_normalized_without_changing_other_fields(self):
        result=cb.validate_pricing({'cloneCredits':'5','subscriptionPriceUsd':'29.99',
                                   'assistant':'{"creditsPerCent":"1"}','uiStyle':'classic'})
        self.assertEqual(result,{'cloneCredits':5,'subscriptionPriceUsd':29.99,'assistant':{'creditsPerCent':1.0},'uiStyle':'classic'})

    def test_full_clone_refund_restores_exact_original_buckets_and_records_once(self):
        db=AtomicLedger(3,10)
        cb.debit(db.rpc,UID,5,'clone',operation_id=OP)
        with tempfile.TemporaryDirectory() as store:
            result=cb.settle_clone(db.rpc,store,UID,OP,'failed',5)
            self.assertEqual((result['taken_subscription'],result['taken_permanent']),(3,2))
            cb.settle_clone(db.rpc,store,UID,OP,'failed',5)
            self.assertEqual(db.balance,{'subscription':3,'permanent':10})
            self.assertEqual([h['credits'] for h in db.history],[5,-5])

    def test_partial_clone_refund_rounds_subscription_share_up(self):
        db=AtomicLedger(3,10);cb.debit(db.rpc,UID,5,'clone',operation_id=OP)
        with tempfile.TemporaryDirectory() as store:
            result=cb.settle_clone(db.rpc,store,UID,OP,'partial',2)
        self.assertEqual((result['taken_subscription'],result['taken_permanent']),(2,0))
        self.assertEqual(db.balance,{'subscription':2,'permanent':8})

    def test_unconfirmed_refund_has_no_history_and_is_recovered_without_provider_calls(self):
        db=AtomicLedger();cb.debit(db.rpc,UID,5,'clone',operation_id=OP)
        def failing(name,args,strict=False):
            if name=='lisan_credit_refund':return None
            return db.rpc(name,args,strict)
        with tempfile.TemporaryDirectory() as store:
            self.assertIsNone(cb.settle_clone(failing,store,UID,OP,'failed',5))
            self.assertEqual([h['credits'] for h in db.history],[5])
            self.assertTrue(list((Path(store)/'credit_settlements').glob('*.json')))
            cb.reconcile_clones(db.rpc,store)
            cb.reconcile_clones(db.rpc,store)
            self.assertEqual(db.balance,{'subscription':5,'permanent':10})
            self.assertEqual([h['credits'] for h in db.history],[5,-5])
            self.assertFalse(list((Path(store)/'credit_settlements').glob('*.json')))

    def test_unconfirmed_settlement_survives_restart_in_the_local_journal(self):
        db=AtomicLedger();cb.debit(db.rpc,UID,5,'clone',operation_id=OP)
        with tempfile.TemporaryDirectory() as store:
            self.assertIsNone(cb.settle_clone(Mock(return_value=None),store,UID,OP,'failed',5))
            cb.reconcile_clones(db.rpc,store)
            self.assertEqual(db.balance,{'subscription':5,'permanent':10})
            self.assertEqual(len(db.history),2)

    def test_cancel_after_both_debit_replies_are_lost_refunds_the_unstarted_clone(self):
        db=AtomicLedger()
        def lost(name,args,strict=False):
            db.rpc(name,args,strict)
            raise TimeoutError('mock all debit replies lost after commit')
        self.assertIsNone(cb.debit(lost,UID,5,'clone',operation_id=OP))
        with tempfile.TemporaryDirectory() as store:
            self.assertIsNotNone(cb.settle_clone(db.rpc,store,UID,OP,'cancelled',5))
        self.assertEqual(db.balance,{'subscription':5,'permanent':10})
        self.assertEqual([h['credits'] for h in db.history],[5,-5])

    def test_cancel_before_a_late_debit_request_prevents_any_charge(self):
        db=AtomicLedger()
        with tempfile.TemporaryDirectory() as store:
            self.assertEqual(cb.settle_clone(db.rpc,store,UID,OP,'cancelled',5),{'not_charged':True})
        self.assertIsNone(cb.debit(db.rpc,UID,5,'clone',operation_id=OP))
        self.assertEqual(db.balance,{'subscription':5,'permanent':10});self.assertEqual(db.history,[])

    def test_cancelled_debit_recovery_survives_a_database_outage(self):
        db=AtomicLedger();cb.debit(db.rpc,UID,5,'clone',operation_id=OP)
        with tempfile.TemporaryDirectory() as store:
            self.assertIsNone(cb.settle_clone(Mock(return_value=None),store,UID,OP,'cancelled',5))
            cb.reconcile_clones(db.rpc,store)
            self.assertEqual(db.balance,{'subscription':5,'permanent':10})

    def test_ready_requires_all_billing_capabilities(self):
        db=AtomicLedger();self.assertTrue(cb.ready(db.rpc))
        self.assertFalse(cb.ready(Mock(return_value={'version':1,'debit':True,'pack':True,'refund':True})))

    def clone_route(self, provider, db, store, engine='elevenlabs'):
        from test_billing_review import BillingReviewTests
        env,request,_=BillingReviewTests().clone_env()
        env['deduct_credits']=lambda *args,**kwargs:cb.debit(db.rpc,*args,**kwargs)
        env['_short_clone_settle']=lambda *args:cb.settle_clone(db.rpc,store,*args)
        env['_active_voice_engine']=lambda:engine
        env['eleven_service']=NS(clone_voices=provider)
        env['inworld_service']=NS(clone_voices=provider)
        return env,request

    def test_mixed_clone_refunds_failed_speaker_share_for_both_engines(self):
        for engine in ('elevenlabs','inworld'):
            with self.subTest(engine=engine),tempfile.TemporaryDirectory() as store:
                db=AtomicLedger(3,10)
                provider=Mock(return_value={'error':'one voice failed', 'cloned_voices':{'A':'voice-A','B':'voice-B','C':'ERROR: Failed'}})
                env,request=self.clone_route(provider,db,store,engine)
                request.segments=[NS(speaker=speaker,text='speech') for speaker in ('A','B','C')]
                result=env['clone'](request,object())
                self.assertEqual((result['credits_charged'],result['credits_refunded']),(3,2))
                self.assertNotIn('error',result)  # UI can still apply the two delivered voices
                self.assertEqual(db.balance,{'subscription':2,'permanent':8})
                self.assertEqual([h['credits'] for h in db.history],[5,-2])
                self.assertEqual(env['_save_user_voice'].call_count,2)
                env['_increment_clone_usage'].assert_called_once_with('user',2)

    def test_provider_exception_refunds_all_and_never_claims_private_diagnostics(self):
        with tempfile.TemporaryDirectory() as store:
            db=AtomicLedger()
            provider=Mock(side_effect=RuntimeError('private provider diagnostic'))
            env,request=self.clone_route(provider,db,store)
            result=env['clone'](request,object())
            self.assertEqual(result['credits_refunded'],5);self.assertEqual(result['credits_charged'],0)
            self.assertIn('refunded 5 credits',result['error']);self.assertNotIn('private',result['error'])
            self.assertEqual(db.balance,{'subscription':5,'permanent':10})

    def test_missing_or_malformed_clone_ids_are_failed_speakers_without_false_deliveries(self):
        with tempfile.TemporaryDirectory() as store:
            db=AtomicLedger();provider=Mock(return_value={'cloned_voices':{'Speaker 1':None}})
            env,request=self.clone_route(provider,db,store)
            result=env['clone'](request,object())
            self.assertEqual(result['credits_refunded'],5)
            self.assertTrue(result['cloned_voices']['Speaker 1'].startswith('ERROR'))
            env['_save_user_voice'].assert_not_called()

    def test_empty_selection_of_real_speakers_starts_no_paid_operation(self):
        with tempfile.TemporaryDirectory() as store:
            db=AtomicLedger();provider=Mock()
            env,request=self.clone_route(provider,db,store)
            request.speakers_to_clone=['not-in-the-project']
            result=env['clone'](request,object())
            self.assertEqual(result.status_code,400);provider.assert_not_called();self.assertEqual(db.history,[])

    def test_clone_route_cancels_a_committed_but_unconfirmed_debit_before_provider(self):
        with tempfile.TemporaryDirectory() as store:
            db=AtomicLedger();provider=Mock()
            env,request=self.clone_route(provider,db,store)
            def lost(name,args,strict=False):
                db.rpc(name,args,strict);raise TimeoutError('mock all debit replies lost')
            env['deduct_credits']=lambda *args,**kwargs:cb.debit(lost,*args,**kwargs)
            result=env['clone'](request,object())
            self.assertEqual(result.status_code,503);provider.assert_not_called()
            self.assertEqual(db.balance,{'subscription':5,'permanent':10})
            self.assertEqual([h['credits'] for h in db.history],[5,-5])

    def test_failed_custom_voice_uses_the_same_confirmed_refund_rule(self):
        with tempfile.TemporaryDirectory() as store:
            db=AtomicLedger(3,10)
            provider=Mock(return_value='ERROR: Voice failed')
            env=source_functions('main.py',['upload_custom_voice'],{'File':lambda *args:None,'Form':lambda value:value,'_current_uid':lambda _:UID,
                '_billing_pause_response':lambda:None,'_job_guard':lambda *args:None,'_active_voice_engine':lambda:'elevenlabs',
                'debit_confirmed':__import__('shortdub_billing').debit_confirmed,
                '_authorize_new_clones':lambda *args,**kwargs:(True,{}),'_get_pricing_config':lambda:{'cloneCredits':5},
                'get_credits':lambda _:13,'JSONResponse':Response,'OUTPUT_DIR':Path(store),
                'deduct_credits':lambda *args,**kwargs:cb.debit(db.rpc,*args,**kwargs),
                '_short_clone_settle':lambda *args:cb.settle_clone(db.rpc,store,*args),
                'eleven_service':NS(add_custom_voice=provider),'ELEVENLABS_API_KEY':'mock',
                '_save_user_voice':Mock(),'_increment_clone_usage':Mock()})
            upload=NS(filename='reference.wav',read=AsyncMock(return_value=b'synthetic mock audio'))
            result=asyncio.run(env['upload_custom_voice'](object(),upload,'Speaker 1',''))
            self.assertEqual((result['credits_charged'],result['credits_refunded']),(0,5))
            self.assertEqual(db.balance,{'subscription':3,'permanent':10})
            self.assertEqual([h['credits'] for h in db.history],[5,-5])
            self.assertFalse(list(Path(store).glob('custom_upload_*')))

    def test_pack_sync_returns_pending_instead_of_acknowledging_unfulfilled_payment(self):
        stripe=NS(api_key=None,checkout=NS(Session=NS(list=lambda **kwargs:NS(data=[
            {'id':'paid','client_reference_id':UID,'payment_status':'paid','metadata':{'credits':'100'}}]))))
        env=source_functions('main.py',['billing_sync'],{'stripe':stripe,'STRIPE_SECRET_KEY':'mock',
            '_current_uid':lambda _:UID,'_fulfill_order':lambda *args:None,'JSONResponse':Response})
        self.assertEqual(env['billing_sync'](object()).status_code,503)

    def test_refund_amount_rule_is_general_and_capped_by_the_total_fee(self):
        for fee in (1,5,17,100):
            for total in (1,3,7):
                for failed in range(total+1):
                    expected=(fee*failed+total-1)//total
                    self.assertEqual(cb.clone_refund_amount(fee,failed,total),expected)
                    self.assertLessEqual(expected,fee)

    def test_rpc_uses_server_authorization_without_exposing_credentials(self):
        env=source_functions('main.py',['_sb_rpc'],{'SUPABASE_URL':'https://database.invalid',
            'SUPABASE_SERVICE_KEY':'synthetic-server-key','urllib':NS(request=urllib.request),'json':json})
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'{"version":1}')) as network:
            self.assertEqual(env['_sb_rpc']('lisan_billing_ready',{},strict=True),{'version':1})
            request=network.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'),'Bearer synthetic-server-key')

    def test_pack_lost_commit_reply_replays_the_same_receipt_without_extra_grant(self):
        db=AtomicLedger();calls=[]
        def lost(name,args,strict=False):
            calls.append(args['p_receipt'])
            result=db.rpc(name,args,strict)
            if len(calls)==1:raise TimeoutError('mock lost pack response after commit')
            return result
        self.assertEqual(cb.fulfill(lost,UID,'purchase',100),'already-fulfilled')
        self.assertEqual(calls,['purchase','purchase']);self.assertEqual(db.balance['permanent'],110)

    def test_sql_drafts_keep_row_locks_transactions_and_server_only_grants(self):
        for name in ('2026-10-credit-operations.sql','2026-10-atomic-credit-debit.sql'):
            text=(ROOT/'sql'/name).read_text(encoding='utf-8')
            install=text.split('-- INSTALL BEGIN')[1].split('-- INSTALL END')[0]
            self.assertIn('BEGIN;',install);self.assertIn('COMMIT;',install)
            self.assertIn('FOR UPDATE',install);self.assertIn('SET search_path = pg_catalog, pg_temp',install)
            self.assertIn('ENABLE ROW LEVEL SECURITY',install)
            self.assertIn('FROM PUBLIC,anon,authenticated',install.replace('PUBLIC, anon, authenticated','PUBLIC,anon,authenticated'))
            self.assertIn('TO postgres,service_role',install.replace('postgres, service_role','postgres,service_role'))
            self.assertNotIn('CREATE OR REPLACE FUNCTION public.deduct_credits(',install)
            self.assertNotIn('CREATE OR REPLACE FUNCTION public.add_credits(',install)


if __name__=='__main__':unittest.main()
