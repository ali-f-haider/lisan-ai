"""Offline callers, invoice service periods and health privacy; no app import."""
import asyncio
import io
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from urllib.error import HTTPError

import credit_actions as ca
from billing_health import load_health, CATEGORIES, EXTRA_CATEGORIES
from billing_action_fixtures import Actions, UID, OP, START, END
from test_shortdub_upgrade import source_functions, Response


def missing():
    return HTTPError('https://example.test/rpc',404,'Missing',{},io.BytesIO(b'{"code":"PGRST202"}'))


class CreditActionTests(unittest.TestCase):
    def body(self, **updates):
        return dict(uid=UID, operation_id=OP, delta=50, reason='Synthetic reason', **updates)

    def test_lost_admin_replies_replay_the_identical_uuid_and_delta(self):
        actions=Actions();lost=0
        def rpc(name,args,**kwargs):
            nonlocal lost
            receipt=actions.rpc(name,args,**kwargs)
            if name=='lisan_admin_adjust' and lost<2:
                lost+=1;raise TimeoutError()
            return receipt
        with self.assertRaises(ca.ActionProblem): ca.adjust(rpc,self.body())
        result=ca.adjust(rpc,self.body())
        self.assertEqual((actions.state['credits'],result['adjusted_credits'],len(actions.history)),(150,50,1))
        self.assertEqual({a['p_operation_id'] for _,a in actions.calls},{OP})

    def test_new_deliberate_uuid_allows_a_second_identical_adjustment(self):
        actions=Actions();body=self.body()
        ca.adjust(actions.rpc,body)
        body['operation_id']='00000000-0000-4000-9000-000000000003'
        self.assertEqual(ca.adjust(actions.rpc,body)['new_credits'],200)

    def test_reusing_admin_id_with_different_work_is_refused(self):
        actions=Actions();ca.adjust(actions.rpc,self.body())
        for key,value in [('uid','00000000-0000-4000-8000-000000000003'),('delta',51),('reason','Changed')]:
            body=self.body();body[key]=value
            with self.assertRaises(ca.ActionProblem) as ex: ca.adjust(actions.rpc,body)
            self.assertEqual(ex.exception.status,409)
        self.assertEqual(actions.state['credits'],150)

    def test_missing_sql_pauses_admin_without_using_legacy_money_path(self):
        rpc=Mock(side_effect=lambda *a,**k: (_ for _ in ()).throw(missing()))
        with self.assertRaises(ca.ActionProblem) as ex: ca.adjust(rpc,self.body())
        self.assertIn('not installed',str(ex.exception));self.assertEqual(ex.exception.status,503)
        self.assertEqual([c.args[0] for c in rpc.call_args_list],['lisan_admin_begin'])

    def test_malformed_server_receipt_is_a_pause_not_invalid_customer_input(self):
        actions=Actions();ca.adjust(actions.rpc,self.body());actions.admin[OP]['balance_after']=float('nan')
        with self.assertRaises(ca.ActionProblem) as ex: ca.adjust(actions.rpc,self.body())
        self.assertEqual(ex.exception.status,503)

    def test_lost_renewal_replies_deliver_once_and_never_touch_permanent(self):
        actions=Actions();lost=0
        def rpc(name,args,**kwargs):
            nonlocal lost
            receipt=actions.rpc(name,args,**kwargs)
            if name=='lisan_subscription_grant' and lost<2:
                lost+=1;raise TimeoutError()
            return receipt
        with self.assertRaises(ca.ActionProblem): ca.subscription_grant(rpc,UID,'invoice',120,START,END)
        self.assertEqual(ca.subscription_grant(rpc,UID,'invoice',120,START,END)['status'],'done')
        self.assertEqual((actions.state['subscription'],actions.state['permanent']),(120,100))
        self.assertEqual(len(actions.invoices),1)

    def test_missing_subscription_sql_is_not_ready_and_never_calls_old_rpc(self):
        rpc=Mock(side_effect=lambda *a,**k: (_ for _ in ()).throw(missing()))
        self.assertFalse(ca.subscription_ready(rpc))
        with self.assertRaises(ca.ActionProblem): ca.subscription_grant(rpc,UID,'invoice',120,START,END)
        self.assertEqual({c.args[0] for c in rpc.call_args_list},{'lisan_subscription_ready','lisan_subscription_begin'})

    def test_subscription_identity_includes_account_amount_and_both_periods(self):
        actions=Actions();ca.subscription_grant(actions.rpc,UID,'invoice',120,START,END)
        args=[UID,'invoice',120,START,END]
        for index,value in [(0,'00000000-0000-4000-8000-000000000003'),(2,121),
                            (3,'2026-10-02T00:00:00Z'),(4,'2026-11-02T00:00:00Z')]:
            changed=args[:];changed[index]=value
            self.assertEqual(ca.subscription_grant(actions.rpc,*changed)['status'],'mismatch')

    def test_invalid_periods_or_amount_never_reach_rpc(self):
        rpc=Mock()
        for start,end in [(None,END),(END,START),(START,START),(float('nan'),END),('2026-10-01',END)]:
            with self.assertRaises(ValueError):ca.subscription_grant(rpc,UID,'invoice',120,start,end)
        for amount in [True,0,-1,1.5,float('inf')]:
            with self.assertRaises(ValueError):ca.subscription_grant(rpc,UID,'invoice',amount,START,END)
        rpc.assert_not_called()

    def test_nonfulfilled_status_never_acknowledges_success(self):
        env=source_functions('main.py',['_subscription_grant_response'],{'JSONResponse':Response})
        for status in ['stale','mismatch','legacy_review','pending']:
            self.assertGreaterEqual(env['_subscription_grant_response']({'status':status}).status_code,400)
        self.assertIsNone(env['_subscription_grant_response']({'status':'done'}))


class InvoicePeriodTests(unittest.TestCase):
    def setUp(self):
        self.env=source_functions('main.py',['_as_id','_invoice_subscription_id','_invoice_billing_period'],{})

    def invoice(self):
        return {'id':'invoice','subscription':'sub','period_start':10,'period_end':20,
                'lines':{'data':[{'type':'subscription','subscription':'sub','amount':2900,
                                  'period':{'start':1790812800,'end':1793491200}}]}}

    def test_uses_line_service_period_instead_of_invoice_accrual_dates(self):
        self.assertEqual(self.env['_invoice_billing_period'](self.invoice()),(START,END))

    def test_modern_parent_shape_and_negative_old_proration(self):
        invoice=self.invoice();line=invoice['lines']['data'][0]
        invoice.pop('subscription');invoice['parent']={'subscription_details':{'subscription':'sub'}}
        line.pop('subscription');line.pop('type');line['parent']={'subscription_item_details':{'subscription':'sub'}}
        invoice['lines']['data'].append({'type':'subscription','amount':-100,'period':{'start':1,'end':2}})
        self.assertEqual(self.env['_invoice_billing_period'](invoice),(START,END))

    def test_ambiguous_partial_or_missing_line_periods_pause(self):
        for mutate in [lambda i:i['lines'].update(has_more=True),lambda i:i['lines'].update(data=[]),
                       lambda i:i['lines']['data'].append({'type':'subscription','amount':100,'period':{'start':1,'end':2}})]:
            invoice=self.invoice();mutate(invoice)
            with self.assertRaises(ValueError):self.env['_invoice_billing_period'](invoice)

    def test_sync_confirms_newest_paid_subscription_invoice_and_propagates_pause(self):
        invoice=self.invoice();grant=Mock(return_value={'status':'done'})
        invoices=Mock(return_value=NS(data=[invoice]))
        stripe=NS(checkout=NS(Session=NS(list=lambda **k:NS(data=[]))),Invoice=NS(list=invoices))
        env=source_functions('main.py',['billing_sync','_subscription_grant_response'],dict(self.env,
            stripe=stripe,STRIPE_SECRET_KEY='synthetic',_current_uid=lambda _:UID,
            _read_subscription_profile=lambda _:{'stripe_subscription_id':'sub','subscription_plan_key':'studio'},
            _get_subscription_plan=lambda _:{'credits':120},_grant_subscription_credits=grant,JSONResponse=Response))
        self.assertEqual(env['billing_sync'](object()),{'added_sessions_credits':0})
        invoices.assert_called_once_with(subscription='sub',status='paid',limit=1)
        grant.assert_called_once_with(UID,'invoice',120,START,END)
        grant.return_value=None
        self.assertEqual(env['billing_sync'](object()).status_code,503)

    def webhook(self, grant, known_uid=True):
        invoice=self.invoice();invoice['billing_reason']='subscription_cycle'
        provider=NS(Webhook=NS(construct_event=lambda *a:{'type':'invoice.paid','data':{'object':invoice}}),
                    Subscription=NS(retrieve=lambda _: {'metadata':{'uid':UID,'plan_key':'studio'},'customer':'customer'}))
        env=source_functions('main.py',['stripe_webhook','_subscription_grant_response'],dict(self.env,
            stripe=provider,STRIPE_WEBHOOK_SECRET='synthetic',STRIPE_SECRET_KEY='synthetic',
            _uid_for_subscription=lambda _:(UID if known_uid else None),
            _read_subscription_profile=lambda _:{'subscription_pending_plan_key':'studio'},
            _get_profile_plan_key=lambda _:'creator',_get_subscription_plan=lambda _:{'credits':120},
            _grant_subscription_credits=Mock(return_value=grant),_set_subscription_fields=Mock(return_value=True),
            _set_subscription_plan_key=Mock(return_value=True),_set_subscription_pending_plan=Mock(return_value=True),
            JSONResponse=Response))
        request=NS(body=AsyncMock(return_value=b'synthetic'),headers={'stripe-signature':'synthetic'})
        return env,asyncio.run(env['stripe_webhook'](request))

    def test_webhook_unconfirmed_grant_returns_retryable_status_without_changing_plan(self):
        env,result=self.webhook(None)
        self.assertEqual(result.status_code,503);env['_set_subscription_plan_key'].assert_not_called()
        env['_set_subscription_pending_plan'].assert_not_called()

    def test_metadata_recovery_for_stale_invoice_does_not_change_current_subscription(self):
        env,result=self.webhook({'status':'stale'},known_uid=False)
        self.assertEqual(result.status_code,409);env['_set_subscription_fields'].assert_not_called()
        env['_set_subscription_plan_key'].assert_not_called()

    def test_confirmed_webhook_grant_completes_scheduled_plan_change(self):
        env,result=self.webhook({'status':'done'})
        self.assertEqual(result,{'ok':True});env['_set_subscription_plan_key'].assert_called_once_with(UID,'studio')
        env['_set_subscription_pending_plan'].assert_called_once_with(UID,None)


class ExtendedHealthTests(unittest.TestCase):
    def payload(self,version=2):
        groups=CATEGORIES+(EXTRA_CATEGORIES if version==2 else ())
        ready=dict(version=version,debit=True,pack=True,refund=True,cancel=True,admin_adjust=True,subscription_grant=True)
        row=dict(operation_id=OP,account_id=UID,amount=120,age_seconds=2,kind='subscription_grant',email='private')
        return {'ready':ready,'lists':{k:{'count':25,'rows':[row]*25} for k in groups}}

    def test_whitelist_limits_every_new_list_and_keeps_unknown_legacy_dates(self):
        payload=self.payload();legacy=payload['lists']['subscription_legacy']['rows']
        payload['lists']['subscription_legacy']['rows']=[dict(r,age_seconds=None,kind='subscription_legacy') for r in legacy]
        out=load_health(Mock(return_value=payload))
        self.assertTrue(out['ready']);self.assertTrue(out['extended'])
        for group in out['lists'].values():self.assertEqual((group['count'],len(group['rows'])),(25,20))
        self.assertIsNone(out['lists']['subscription_legacy']['rows'][0]['age_seconds'])
        self.assertNotIn('private',json.dumps(out))

    def test_missing_extension_reads_unchanged_base_health(self):
        def rpc(name,args,**kwargs):
            if name.endswith('_v2'):raise missing()
            return self.payload(1)
        out=load_health(rpc);self.assertTrue(out['installed']);self.assertFalse(out['extended'])
        self.assertEqual(set(out['lists']),set(CATEGORIES))

    def test_outage_does_not_silently_fall_back_to_old_health(self):
        rpc=Mock(side_effect=TimeoutError());self.assertIsNone(load_health(rpc)['installed'])
        self.assertEqual(rpc.call_count,1)

    def test_missing_money_function_is_not_ready_and_bad_rows_are_removed(self):
        payload=self.payload();payload['ready']['subscription_grant']=False
        payload['lists']['admin_pending']['rows']=[{'email':'private'}]
        out=load_health(Mock(return_value=payload));self.assertFalse(out['ready'])
        self.assertEqual(out['lists']['admin_pending']['rows'],[])
