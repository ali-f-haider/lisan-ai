"""Long-video charges and refunds: one payment per ID, one refund per reason, nothing lost when a reply is.

Offline: a small in-memory ledger stands in for the database (its rules are the ones the SQL enforces and the
SQL itself was run against a real PostgreSQL). No provider, email or network is used.
"""
import copy
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import test_shortdub_upgrade as short
import longdub_billing as lb
import credit_billing as cb

HELPERS = ['_ops', '_refunded_of', '_paid_key', '_sync_paid', '_charge', '_refund', '_remaining', '_music_slots', '_fail']


class Ledger:
    """Charges are idempotent per operation ID; a refund is idempotent per (payment, reason) and never exceeds the payment."""

    def __init__(self, balance=1000):
        self.balance = balance
        self.debits, self.refunds, self.charge_calls = {}, {}, []
        self.lose_next_reply = False
        self.confirm = True

    def charge(self, uid, amount, action, job_id, seconds=None, operation_id=None):
        self.charge_calls.append(operation_id)
        if operation_id not in self.debits:
            if self.balance < amount:
                return False
            self.balance -= amount
            self.debits[operation_id] = amount
        return self.balance

    def refund(self, uid, amount, job_id, debit_id=None, key=None):
        if debit_id is None:
            return None
        if (debit_id, key) not in self.refunds:
            paid_back = sum(v for (d, _), v in self.refunds.items() if d == debit_id)
            if paid_back + amount > self.debits[debit_id]:
                return None            # the ledger refuses to refund more than was paid
            self.refunds[(debit_id, key)] = amount
            self.balance += amount
        if self.lose_next_reply:
            self.lose_next_reply = False
            raise TimeoutError('reply lost after the ledger committed')
        return {'status': 'done'} if self.confirm else None


def make(ledger, store):
    saved = Mock(side_effect=lambda job: store.update(copy.deepcopy(job)))
    env = {'Hooks': NS(charge=ledger.charge, refund=ledger.refund, send_email=Mock()),
           '_lock_for': lambda _: threading.RLock(), '_save': saved, '_ev': Mock(), '_delete_pending_voices': Mock(),
           'uuid': uuid}
    return short.source_functions('longdub_service.py', HELPERS, env), env, saved


def job(**paid):
    return {'id': 'job', 'uid': 'user', 'filename': 'clip.mp4', 'status': 'confirmed', 'paid': {'fee': 0, 'analysis': 0, 'dub': 0, **paid}}


class ChargeTests(unittest.TestCase):
    def test_payment_id_is_saved_before_money_moves_and_survives_a_restart(self):
        ledger, store = Ledger(), {}
        n, env, saved = make(ledger, store)
        first = job()
        seen = []
        original = ledger.charge
        env['Hooks'].charge = lambda *a, **k: (seen.append(copy.deepcopy(store.get('ops'))), original(*a, **k))[1]
        n['_charge'](first, 'dub', 'user', 30, 'long_dub_dub')
        self.assertEqual(seen[0]['dub']['id'], first['ops']['dub']['id'], 'The ID must be on disk when the charge is made')
        # The process died before the job recorded the payment: the next process loads the saved job and tries again.
        restarted = copy.deepcopy(store)
        n['_charge'](restarted, 'dub', 'user', 30, 'long_dub_dub')
        self.assertEqual(len(ledger.debits), 1)
        self.assertEqual(ledger.balance, 970, 'Charged twice')

    def test_nothing_is_charged_when_the_payment_record_cannot_be_saved(self):
        ledger, store = Ledger(), {}
        n, env, saved = make(ledger, store)
        saved.side_effect = OSError('disk full')
        with self.assertRaises(OSError):
            n['_charge'](job(), 'dub', 'user', 30, 'long_dub_dub')
        self.assertEqual(ledger.charge_calls, [])
        self.assertEqual(ledger.balance, 1000)

    def test_each_music_repair_is_its_own_payment(self):
        ledger, store = Ledger(), {}
        n, _, _ = make(ledger, store)
        j = job()
        for hole in ('1.000:2.000', '5.000:6.000'):
            n['_charge'](j, 'music_fill:' + hole, 'user', 10, 'long_dub_music_fill')
            n['_sync_paid'](j)
        self.assertEqual(len(ledger.debits), 2)
        self.assertEqual(j['paid']['music_fill'], 20)
        n['_charge'](j, 'music_fill:1.000:2.000', 'user', 10, 'long_dub_music_fill')   # the same hole again after a restart
        self.assertEqual(len(ledger.debits), 2)

    def test_payment_made_by_an_older_version_is_kept_when_new_repairs_are_added(self):
        ledger, store = Ledger(), {}
        n, _, _ = make(ledger, store)
        j = job(dub=40, music_fill=10)         # paid before payments carried IDs
        n['_charge'](j, 'music_fill:9.000:10.000', 'user', 10, 'long_dub_music_fill')
        n['_sync_paid'](j)
        self.assertEqual(j['paid']['music_fill'], 20)


class RefundTests(unittest.TestCase):
    def paid_job(self, n, ledger):
        j = job()
        n['_charge'](j, 'dub', 'user', 20, 'long_dub_dub')
        n['_sync_paid'](j)
        j['paid']['dub'] = 20
        return j

    def test_a_refund_repeated_with_the_same_reason_grants_once(self):
        ledger, store = Ledger(), {}
        n, _, _ = make(ledger, store)
        j = self.paid_job(n, ledger)
        before = ledger.balance
        self.assertTrue(n['_refund'](j, 'user', 5, 'dub', 'clip:0'))
        self.assertTrue(n['_refund'](j, 'user', 5, 'dub', 'clip:0'))
        self.assertEqual(ledger.balance, before + 5)
        self.assertEqual(j['paid']['dub'], 15)

    def test_a_lost_reply_is_not_a_second_refund_and_the_job_figures_do_not_drift(self):
        ledger, store = Ledger(), {}
        n, _, _ = make(ledger, store)
        j = self.paid_job(n, ledger)
        before = ledger.balance
        ledger.lose_next_reply = True
        self.assertFalse(n['_refund'](j, 'user', 5, 'dub', 'clip:0'), 'An unconfirmed refund must not be reported as done')
        self.assertEqual(j['paid']['dub'], 20)           # nothing in the job changed
        self.assertTrue(n['_refund'](j, 'user', 5, 'dub', 'clip:0'))    # the retry repeats the same request
        self.assertEqual(ledger.balance, before + 5)
        self.assertEqual(j['paid']['dub'], 15)

    def test_a_failed_job_gets_back_exactly_what_is_left(self):
        ledger, store = Ledger(), {}
        n, env, _ = make(ledger, store)
        j = self.paid_job(n, ledger)
        n['_refund'](j, 'user', 5, 'dub', 'clip:0')
        for hole in ('1.000:2.000', '5.000:6.000'):
            n['_charge'](j, 'music_fill:' + hole, 'user', 10, 'long_dub_music_fill')
        n['_sync_paid'](j)
        spent = 20 + 20
        n['_fail'](j, 'The video could not be finished.', 'dub')
        self.assertEqual(ledger.balance, 1000 - spent + 5 + 15 + 20)
        self.assertEqual(j['paid']['dub'], 0)
        self.assertEqual(j['paid']['music_fill'], 0)
        body = env['Hooks'].send_email.call_args.args[2]
        self.assertIn('We refunded 35 credits', body)
        # A restart repeats the failure: nothing more is granted.
        n['_fail'](j, 'The video could not be finished.', 'dub')
        self.assertEqual(ledger.balance, 1000 - spent + 40)

    def test_an_unconfirmed_refund_keeps_the_pending_charge_and_tells_the_customer_honestly(self):
        ledger, store = Ledger(), {}
        n, env, _ = make(ledger, store)
        j = self.paid_job(n, ledger)
        ledger.confirm = False
        n['_fail'](j, 'The video could not be finished.', 'dub')
        self.assertEqual(j['paid']['dub'], 20)
        body = env['Hooks'].send_email.call_args.args[2]
        self.assertNotIn('We refunded', body)
        self.assertIn('being returned', body)
        ledger.confirm = True
        n['_fail'](j, 'The video could not be finished.', 'dub')      # the retry (same IDs) now completes it
        self.assertEqual(j['paid']['dub'], 0)

    def test_the_failure_is_saved_before_any_refund_and_nothing_is_refunded_if_it_cannot_be_saved(self):
        ledger, store = Ledger(), {}
        n, env, saved = make(ledger, store)
        j = self.paid_job(n, ledger)
        saved.side_effect = OSError('disk full')
        before = ledger.balance
        with self.assertRaises(OSError):
            n['_fail'](j, 'The video could not be finished.', 'dub')
        self.assertEqual(ledger.balance, before)
        self.assertEqual(ledger.refunds, {})


class RecoveryTests(unittest.TestCase):
    def rpc_for(self, state):
        def rpc(name, args, strict=False):
            if name != 'lisan_credit_refund_part':
                raise AssertionError(name)
            if state['down']:
                raise OSError('database unreachable')
            state['calls'].append(args)
            return {'uid': args['p_uid'], 'amount': args['p_amount'], 'kind': 'refund', 'status': 'done',
                    'operation_id': args['p_operation_id'], 'taken_subscription': 0, 'taken_permanent': args['p_amount'],
                    'subscription_balance': 0, 'permanent_balance': 50}
        return rpc

    def test_refund_id_depends_only_on_the_payment_and_the_reason(self):
        a = str(uuid.uuid4())
        self.assertEqual(lb.refund_id(a, 'fail'), lb.refund_id(a, 'fail'))
        self.assertNotEqual(lb.refund_id(a, 'fail'), lb.refund_id(a, 'clip:0'))
        self.assertNotEqual(lb.refund_id(a, 'fail'), lb.refund_id(str(uuid.uuid4()), 'fail'))

    def test_an_unreachable_database_leaves_a_record_that_is_completed_later_with_the_same_id(self):
        state = {'down': True, 'calls': []}
        debit = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(lb.settle(self.rpc_for(state), folder, 'user', 7, debit, 'fail'))
            records = list((Path(folder) / 'credit_refunds').glob('*.json'))
            self.assertEqual(len(records), 1)
            state['down'] = False
            self.assertEqual(lb.reconcile(self.rpc_for(state), folder), 1)
            self.assertEqual(list((Path(folder) / 'credit_refunds').glob('*.json')), [])
            self.assertEqual(state['calls'][0]['p_operation_id'], lb.refund_id(debit, 'fail'))
            self.assertEqual(state['calls'][0]['p_amount'], 7)

    def test_a_confirmed_refund_leaves_no_record(self):
        state = {'down': False, 'calls': []}
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNotNone(lb.settle(self.rpc_for(state), folder, 'user', 7, str(uuid.uuid4()), 'fail'))
            self.assertEqual(list((Path(folder) / 'credit_refunds').glob('*.json')), [])

    def test_the_old_single_grant_writes_history_only_after_it_is_confirmed(self):
        record = Mock()
        self.assertIsNone(lb.legacy(Mock(side_effect=OSError('down')), record, 'user', 5, 'job'))
        self.assertIsNone(lb.legacy(Mock(return_value=0), record, 'user', 5, 'job'))
        record.assert_not_called()
        self.assertIsNotNone(lb.legacy(Mock(return_value=105), record, 'user', 5, 'job'))
        record.assert_called_once_with('user', 'long_dub_refund', -5, 'job')


if __name__ == '__main__':
    unittest.main()
