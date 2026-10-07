"""Offline billing review proofs. Failing cases assert the required behavior.

No live app import, database, provider, email or paid service call is made.
"""
import copy
import io
import json
import math
import sys
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import test_shortdub_upgrade as short
from shortdub_billing import debit_confirmed
from user_errors import customer_message, customer_payload


def extract(file, names, env):
    env.setdefault('customer_message', customer_message)
    env.setdefault('customer_payload', customer_payload)
    return short.source_functions(file, names, env)


# Ali supplied these deployed definitions on 2026-10-07. This is an offline
# semantic model, not a connection to the database. The snapshot is evidence,
# not a migration to execute. Update it when the real RPC migration changes.
RPC_SQL = (Path(__file__).parent/'fixtures'/'billing_rpc_definitions.sql').read_text(encoding='utf-8')


class DeployedCreditLedger:
    def __init__(self, subscription, permanent):
        self.balance = {'subscription':subscription,'permanent':permanent}

    def rpc(self, name, data):
        amount = data['amount']
        sql = ' '.join(RPC_SQL.lower().split())
        if name == 'deduct_subscription_credits':
            assert 'take_amt := least(greatest(cur, 0), amount)' in sql
            taken = min(max(self.balance['subscription'],0),amount)
            self.balance['subscription'] -= taken
            return [{'taken':taken,'shortfall':amount-taken}]
        if name == 'deduct_credits':
            assert 'credits = greatest(credits - amount, 0)' in sql
            self.balance['permanent'] = max(self.balance['permanent']-amount,0)
            return self.balance['permanent']
        raise AssertionError('Unexpected mocked RPC: '+name)


class BillingReviewTests(unittest.TestCase):
    def short_routes(self):
        fixture = short.RouteTests()
        fixture.setUp()
        fixture.n.update(customer_message=customer_message, customer_payload=customer_payload)
        self.addCleanup(fixture.tearDown)
        return fixture

    def test_failed_combined_bucket_debit_preserves_the_entire_balance(self):
        db = DeployedCreditLedger(5,5)
        def rpc(name,data):
            if name == 'deduct_credits': return None  # HTTP failure before this RPC commits
            return db.rpc(name,data)
        n = extract('main.py', ['deduct_credits'], {'_sb_rpc':rpc})
        self.assertFalse(debit_confirmed(n['deduct_credits']('user',10)))
        self.assertEqual(db.balance, {'subscription':5,'permanent':5}, 'A failed second RPC must not leave the first bucket charged')

    def test_lost_subscription_rpc_response_does_not_charge_the_other_bucket_again(self):
        ledger = {'subscription':5, 'permanent':10}
        def rpc(name, data):
            if name == 'deduct_subscription_credits':
                ledger['subscription'] -= 5
                return None  # commit succeeded; its HTTP response was lost
            ledger['permanent'] -= data['amount']
            return ledger['permanent']
        n = extract('main.py', ['deduct_credits'], {'_sb_rpc':rpc})
        n['deduct_credits']('user', 10)
        self.assertGreaterEqual(sum(ledger.values()), 5, 'A 10-credit operation may not consume 15 credits')

    def test_credit_purchase_is_recoverable_when_grant_fails_after_order_insert(self):
        orders, granted = set(), []
        class Reply(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): return False
        def open_request(req, timeout=0):
            if req.data is None:
                return Reply(json.dumps([{'session_id':'checkout'}] if 'checkout' in orders else []).encode())
            orders.add(json.loads(req.data)['session_id'])
            return Reply(b'{}')
        attempts = []
        def rpc(name, values):
            attempts.append(values)
            if len(attempts) == 1: return None
            granted.append(values['amount']); return values['amount']
        n = extract('main.py', ['_fulfill_order'], {'SUPABASE_SERVICE_KEY':'mock', 'SUPABASE_URL':'https://database.invalid',
                   'urllib':NS(request=NS(Request=urllib.request.Request, urlopen=open_request)), 'json':json, '_sb_rpc':rpc})
        n['_fulfill_order']('user', 'checkout', 100)
        n['_fulfill_order']('user', 'checkout', 100)
        self.assertEqual(granted, [100], 'A paid order recorded before a failed grant must still be recoverable')

    def clone_env(self):
        provider = Mock(return_value={'cloned_voices':{'Speaker 1':'ERROR: Voice could not be cloned.'}})
        n = extract('main.py', ['clone'], {'_rate_limited':Mock(return_value=False), 'LIGHT_RATE_MAX':1,
                   'LIGHT_RATE_WINDOW_SEC':1, '_job_guard':Mock(return_value=None), '_paid_uid':Mock(return_value=('user',None)),
                   '_active_voice_engine':lambda:'elevenlabs', '_authorize_new_clones':Mock(return_value=(True,{})),
                   'get_credits':Mock(return_value=100), '_get_pricing_config':lambda:{'cloneCredits':5},
                   'deduct_credits':Mock(return_value=95), 'JSONResponse':short.Response,
                   'ELEVENLABS_API_KEY':'mock', 'INWORLD_API_KEY':'mock', 'eleven_service':NS(clone_voices=provider),
                   '_save_user_voice':Mock(), '_increment_clone_usage':Mock(), '_tag_voice_engine':Mock(),
                   '_ld_refund':Mock(), '_sb_rpc':Mock()})
        req = short.request(speakers_to_clone=[], segments=[NS(speaker='Speaker 1', text='original')])
        return n, req, provider

    def test_failed_short_voice_clone_is_not_left_charged(self):
        n, req, _ = self.clone_env()
        n['clone'](req, object())
        charged = sum(c.args[1] for c in n['deduct_credits'].call_args_list)
        refunded = sum(c.args[1] for c in n['_ld_refund'].call_args_list)
        self.assertEqual(charged-refunded, 0, 'A clone request where every speaker failed must not leave a charge')

    def test_short_voice_clone_does_not_run_after_an_unconfirmed_debit(self):
        n, req, provider = self.clone_env()
        n['deduct_credits'].return_value = None
        n['clone'](req, object())
        provider.assert_not_called()

    def test_retry_after_lost_short_regeneration_response_does_not_generate_and_charge_twice(self):
        f = self.short_routes()
        req = short.request(accepted_credits=1)
        first = f.n['regenerate_line'](req, object())
        self.assertEqual(first['credits_charged'], 1)
        # The response never reached the client; it retries the identical work.
        f.n['regenerate_line'](req, object())
        self.assertEqual(f.n['deduct_credits'].call_count, 1, 'Retries need an operation ID, distinct from an intentional new take')

    def long_fail_env(self, refund):
        hooks = NS(refund=refund, send_email=Mock())
        n = extract('longdub_service.py', ['_fail'], {'Hooks':hooks, '_lock_for':lambda _:threading.RLock(),
                    '_save':Mock(), '_ev':Mock(), '_delete_pending_voices':Mock()})
        return n, hooks

    def test_unconfirmed_long_refund_preserves_the_pending_charge(self):
        n, hooks = self.long_fail_env(Mock(return_value=None))
        job = {'id':'job', 'uid':'user', 'filename':'original.mp4', 'paid':{'dub':7,'music_fill':10}}
        n['_fail'](job, 'The video could not be finished.', 'dub')
        self.assertEqual(job['paid'], {'dub':7,'music_fill':10}, 'Do not erase a debt that was not confirmed refunded')
        self.assertNotIn('We refunded', hooks.send_email.call_args.args[2])

    def test_refund_history_is_not_written_until_the_credit_grant_is_confirmed(self):
        record = Mock()
        n = extract('main.py', ['_ld_refund'], {'_sb_rpc':Mock(return_value=None), '_record_spend':record})
        n['_ld_refund']('user', 17, 'job')
        record.assert_not_called()

    def test_worker_restart_after_charge_before_job_save_does_not_charge_again(self):
        persisted = {'id':'job', 'uid':'user', 'filename':'original.mp4', 'status':'editing', 'paid':{}}
        price = {'lines':1, 'due':7, 'chars':6, 'chars_per_credit':60, 'clone_each':5, 'merge':1,
                 'speakers_used':[{'id':'s1'}], 'voice':1, 'clones':5}
        charge = Mock(return_value=True)
        n = extract('longdub_service.py', ['confirm'], {'has_media':lambda _:True, 'ensure_tashkeel':lambda _:(0,None),
                    'dub_price':lambda _:price, 'INWORLD_API_KEY':'mock', 'music_quote':lambda _:{'max_credits':0},
                    'Hooks':NS(get_credits=lambda _:100, charge=charge), '_lock_for':lambda _:threading.RLock(),
                    '_debit_ok':debit_confirmed, '_now':lambda:1, 'ROOM_CHOICES':{'auto':0},
                    '_save':Mock(side_effect=OSError('mock checkpoint write failure')), '_ev':Mock(), 'start_worker':Mock()})
        with self.assertRaises(OSError): n['confirm'](copy.deepcopy(persisted), 'user', 7)
        n['_save'].side_effect = None
        n['confirm'](copy.deepcopy(persisted), 'user', 7)  # process restarted from the unpaid saved job
        self.assertEqual(charge.call_count, 1, 'Billing needs a durable operation ID shared with its job checkpoint')

    def merge_env(self, folder, background=False):
        out = Path(folder)
        video = out/'original.mp4'; video.write_bytes(b'mock video')
        (out/f'{short.JOB_A}_final_dubbed.mp3').write_bytes(b'mock voice')
        bed = out/'background.wav'; bed.write_bytes(b'mock bed')
        def mux(video, audio, final): final.write_bytes(b'mocked completed mux')
        n = extract('main.py', ['_merge_video_run', '_short_merge_price'], {
                    'OUTPUT_DIR':out, 'Path':Path, 'os':os, 'json':json, 'FAL_API_KEY':'mock', 'GEMINI_API_KEY':'mock',
                    '_rate_limited':lambda *args:False, 'HEAVY_RATE_MAX':1, 'HEAVY_RATE_WINDOW_SEC':1,
                    '_job_guard':lambda *args,**kwargs:None, '_paid_uid':lambda _:('user',None), 'get_credits':lambda _:100,
                    '_get_pricing_config':lambda:{'mergeCredits':1,'musicFillCredits':10}, 'find_job_video':lambda _:video,
                    '_storage_block':lambda *args:None, '_merge_say':Mock(), 'job_background_audio':lambda _:bed if background else None,
                    'voice_clean':NS(ENABLED=False), '_wm_needed':lambda _:False, 'deduct_credits':Mock(return_value=90),
                    'debit_confirmed':debit_confirmed, 'JSONResponse':short.Response,
                    'ffmpeg_utils':NS(mux_audio_into_video=mux, mix_two_audio=Mock()),
                    '_short_speech_spans':lambda *args:[(1,2),(3,4)],
                    'bg_duck':NS(prepare_reactions=lambda *args:{'path':bed,'temps':[]})})
        req = short.request(enhance_background=False, keep_music=True)
        return n, req, bed

    def test_local_only_short_video_merge_is_free(self):
        with tempfile.TemporaryDirectory() as folder:
            n, req, _ = self.merge_env(folder)
            price = n['_short_merge_price'](req)
            n['_merge_video_run'](req, object(), price)
            n['deduct_credits'].assert_not_called()

    def test_short_merge_charges_each_successful_ai_music_hole_once(self):
        with tempfile.TemporaryDirectory() as folder:
            n, req, bed = self.merge_env(folder, background=True)
            def prepare(*args, **kwargs):
                kwargs['on_filled'](1,2); kwargs['on_filled'](3,4)
                return {'path':bed,'temps':[],'music_fill':{'incomplete':False}}
            n['dub_background'] = NS(prepare=prepare)
            price = {'max_total':21,'music_each':10,'music_max':20,'music_charged':0,'music_kept':True}
            n['_merge_video_run'](req, object(), price)
            paid_holes = [c.args for c in n['deduct_credits'].call_args_list if c.args[2]=='short_dub_music_fill']
            self.assertEqual(paid_holes, [('user',10,'short_dub_music_fill',short.JOB_A)]*2)
            self.assertEqual(price['music_charged'], 20)

    def test_regeneration_price_is_accepted_and_only_this_line_is_charged(self):
        f = self.short_routes()
        f.buckets[short.JOB_A] = {'gemini_out':900000}
        req = short.request(segment=short.segment('x'*51), accepted_credits=2)
        self.assertEqual(f.n['regenerate_quote'](req, object())['credits'], 2)
        self.assertEqual(f.n['regenerate_line'](req, object())['credits_charged'], 2)
        f.n['deduct_credits'].assert_called_once_with('user',2,'regenerate',short.JOB_A)

    def test_inflight_double_click_is_blocked_and_failed_regeneration_is_free(self):
        f = self.short_routes()
        req = short.request(accepted_credits=1)
        short.begin_operation(short.JOB_A)
        self.assertEqual(f.n['regenerate_line'](req,object()).status_code,409)
        f.n['eleven_service'].regenerate_line.assert_not_called()
        short.finish_operation(short.JOB_A)
        f.n['eleven_service'].regenerate_line.return_value = {'error':'Voice generation failed.'}
        f.n['regenerate_line'](req,object())
        f.n['deduct_credits'].assert_not_called()

    def test_local_retiming_and_remix_do_not_charge(self):
        f = self.short_routes()
        f.n['restretch_line'](short.request(),object())
        f.n['remix_audio'](short.request(),object())
        f.n['deduct_credits'].assert_not_called()
        f.n['eleven_service'].regenerate_line.assert_not_called()

    def test_insufficient_combined_balance_is_not_reported_as_fully_paid(self):
        db=DeployedCreditLedger(5,0)
        n=extract('main.py',['deduct_credits'],{'_sb_rpc':db.rpc})
        result=n['deduct_credits']('user',10)
        self.assertFalse(debit_confirmed(result),'The deployed SQL returns zero after deducting only five of ten requested credits')

    def test_positive_debits_do_not_make_either_sql_bucket_negative(self):
        db=DeployedCreditLedger(5,5)
        n=extract('main.py',['deduct_credits'],{'_sb_rpc':db.rpc})
        for amount in (8,5,100):n['deduct_credits']('user',amount)
        self.assertGreaterEqual(db.balance['subscription'],0)
        self.assertGreaterEqual(db.balance['permanent'],0)

    def test_negative_debit_cannot_create_subscription_credits(self):
        db=DeployedCreditLedger(5,5)
        n=extract('main.py',['deduct_credits'],{'_sb_rpc':db.rpc})
        try:result=n['deduct_credits']('user',-3)
        except ValueError:result=None  # explicit rejection is also correct
        self.assertEqual(db.balance,{'subscription':5,'permanent':5},'Invalid prices must not become credit grants')
        self.assertFalse(debit_confirmed(result))



    def test_concurrent_bucket_debits_do_not_lose_credits_for_the_failed_request(self):
        from concurrent.futures import ThreadPoolExecutor
        first_took_subscription = threading.Event()
        second_finished = threading.Event()
        db = DeployedCreditLedger(5,5)
        def rpc(name,data):
            result = db.rpc(name,data)
            if name == 'deduct_subscription_credits' and data['amount'] == 8:
                first_took_subscription.set()
                if not second_finished.wait(3): raise AssertionError('Mock scheduling timed out')
            return result
        n = extract('main.py',['deduct_credits'],{'_sb_rpc':rpc})
        # Both callers already saw ten credits; one wants eight, one five.
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(n['deduct_credits'],'user',8)
            self.assertTrue(first_took_subscription.wait(3))
            second=debit_confirmed(n['deduct_credits']('user',5))
            second_finished.set()
            first_ok=debit_confirmed(first.result())
        self.assertLessEqual(sum([8 if first_ok else 0,5 if second else 0]),10,
                             'Concurrent accepted charges may not exceed the initial funded balance')

    def test_checkpoint_failure_after_refund_cannot_refund_the_same_charge_twice(self):
        persisted = {'id':'job','uid':'user','filename':'clip.mp4','paid':{'dub':7}}
        refund = Mock(return_value=True)
        n,_ = self.long_fail_env(refund)
        n['_save'].side_effect = OSError('mock checkpoint write failure')
        with self.assertRaises(OSError): n['_fail'](copy.deepcopy(persisted),'Generation failed.','dub')
        n['_save'].side_effect = None
        n['_fail'](copy.deepcopy(persisted),'Generation failed.','dub')
        self.assertEqual(refund.call_count,1,'A replayed saved job may not repeat its already-paid refund')

    def test_local_merge_component_in_long_and_correction_prices_is_free(self):
        rows=[{'segment_id':'line','arabic_text':'مرحبا','text':'Hello','speaker_id':'s1','emotion':'neutral'}]
        cfg={'chars_per_credit':60,'clone_credits':5,'merge_credits':1}
        parent={'id':'job','uid':'user','status':'done','paid':{},'duration':5,'edit_assets':True,
                'speaker_list':[{'id':'s1','name':'Speaker 1'}],'dub':{'voices':{'s1':'voice'}}}
        env={'Hooks':NS(pricing=lambda:cfg),'read_segments':lambda _:rows,'math':math,
             'lipsync_price':lambda *args:None,'_speed_factor':lambda _:1,'DUB_BASE_SEC':1}
        long=extract('longdub_service.py',['dub_price'],env)
        corrections=extract('longdub_edits.py',['quote'], {'rows':lambda _:rows,'active':lambda _:False,
                  'restore_source':lambda _:None,'background':lambda _:Path('retained.wav'), 'math':math,
                  'ld':NS(Hooks=NS(pricing=lambda:cfg)), 'hashlib':__import__('hashlib'),'json':json})
        with patch.dict(sys.modules, {'inworld_service':NS(instruction_tag=lambda _: '')}):
            for kind,price in [('long',long['dub_price'](parent)),('correction',corrections['quote'](parent,['line']))]:
                with self.subTest(kind=kind): self.assertEqual(price['merge'],0,'Local assembly must add no credits')

    def test_completed_music_checkpoint_is_reused_without_paid_repairs(self):
        with tempfile.TemporaryDirectory() as folder:
            work=Path(folder); matched=work/'part_matched.wav'; matched.write_bytes(b'mock music')
            cached={'ready':True,'started':True,'music_fill':{'filled_sec':2},'measurements':{}}
            prepare=Mock(); save=Mock()
            n=extract('dub_background.py',['checkpointed_prepare'],{'Path':Path,'prepare':prepare})
            job={'background_checkpoint':{'part':cached},'paid':{'music_fill':10}}
            result=n['checkpointed_prepare'](job,save,1,None,None,None,work,'part')
            self.assertEqual(result['path'],matched);prepare.assert_not_called();save.assert_not_called()

    def test_interrupted_paid_music_is_not_submitted_a_second_time(self):
        with tempfile.TemporaryDirectory() as folder:
            prepare=Mock();save=Mock()
            n=extract('dub_background.py',['checkpointed_prepare'],{'Path':Path,'prepare':prepare})
            job={'background_checkpoint':{'part':{'started':True}},'paid':{'music_fill':10}}
            with self.assertRaises(RuntimeError):n['checkpointed_prepare'](job,save,1,None,None,None,Path(folder),'part')
            prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
