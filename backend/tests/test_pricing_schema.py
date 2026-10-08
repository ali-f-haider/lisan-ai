"""Run the real admin form builder and all pricing writes against Ali's schema.

No live main import, database or provider calls. Column types were supplied by
Ali on 2026-10-08, not inferred from the code under test.
"""
import copy
import asyncio
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError

import credit_billing as cb
from billing_health import CATEGORIES, EXTRA_CATEGORIES, load_health
from test_shortdub_upgrade import source_functions, Response

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'tests/fixtures/pricing_config_columns.json').read_text(encoding='utf-8'))


class PricingSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Node is required to exercise the real admin pricing form.')
        output = subprocess.check_output([node, str(ROOT / 'tests/test_job8_ui.js'), '--pricing-payload'], cwd=ROOT)
        cls.payload = json.loads(output)

    def environment(self):
        return source_functions('main.py', ['_save_pricing_config', '_http_error_detail', '_conc_cfg', '_assistant_cfg', '_gemini_cpc'],
                                dict(SUPABASE_URL='https://example.test', SUPABASE_SERVICE_KEY='synthetic', json=json, time=time))

    def write(self, payload, fail_request=None):
        bodies = []
        def network(req, **kwargs):
            raw = req.data.decode('utf-8')
            body = json.loads(raw, parse_constant=lambda value: self.fail('Nonfinite JSON: ' + value))
            bodies.append(body)
            if fail_request == len(bodies):
                raise HTTPError(req.full_url, 400, 'Synthetic refused write', {}, io.BytesIO(b'{"message":"Synthetic column refusal"}'))
            return io.BytesIO(b'')
        with patch('urllib.request.urlopen', side_effect=network):
            ok, reason = self.environment()['_save_pricing_config'](payload)
        return ok, reason, bodies

    def assert_schema(self, bodies):
        seen = set()
        for body in bodies:
            for column, value in body.items():
                self.assertIn(column, SCHEMA)
                seen.add(column)
                kind = SCHEMA[column]
                if kind == 'integer':
                    self.assertIs(type(value), int, (column, value))
                    self.assertNotIn('.', json.dumps(value))
                    self.assertLessEqual(value, 2147483647)
                elif kind == 'numeric':
                    self.assertIn(type(value), (int, float), (column, value))
                    self.assertTrue(math.isfinite(value))
                elif kind == 'boolean':
                    self.assertIs(type(value), bool, (column, value))
                elif kind in ('text', 'timestamp with time zone'):
                    self.assertIsInstance(value, str, (column, value))
                elif kind == 'jsonb':
                    self.assertIsInstance(value, (dict, list), (column, value))
                    json.dumps(value, allow_nan=False)
        # These two legacy columns are no longer saved by the admin form.
        self.assertEqual(seen, set(SCHEMA) - {'price_per_min', 'markup'})

    def test_real_admin_payload_all_eight_requests_match_supplied_column_types(self):
        checked = cb.validate_pricing(copy.deepcopy(self.payload))
        ok, reason, bodies = self.write(checked)
        self.assertTrue(ok, reason)
        self.assertEqual(len(bodies), 8)
        self.assert_schema(bodies)
        plan = bodies[0]['subscription_plans'][0]
        self.assertIsNone(plan['clones_per_month'])
        self.assertIsNone(plan['storage_gb'])
        self.assertEqual(bodies[1]['long_dub_analysis_per_min'], 2.25)
        self.assertEqual(bodies[0]['lipsync_credits_per_sec'], 40)

    def test_integer_fields_accept_whole_floats_and_strings_without_decimal_json(self):
        fields = ['freeCredits','minReserve','maxVideoMin','transcribeCredits','mergeCredits','charsPerCredit',
                  'cloneCredits','inworldCharsPerCredit','inworldCloneCredits','inworldSlotLimit',
                  'longDubMaxMin','longDubFlatCredits','longDubLipsyncMaxMin','lipsyncCreditsPerSec','subscriptionCredits']
        for convert in (lambda x: float(x), lambda x: str(float(x))):
            payload = copy.deepcopy(self.payload)
            for field in fields:
                payload[field] = convert(payload[field])
            ok, reason, bodies = self.write(payload)
            self.assertTrue(ok, reason)
            self.assert_schema(bodies)

    def test_fractional_missing_and_nonfinite_integer_inputs_fail_before_any_write(self):
        for field in ['maxVideoMin','inworldSlotLimit','longDubMaxMin','longDubLipsyncMaxMin','lipsyncCreditsPerSec']:
            for value in [1.5, '', None, True, 'NaN', 'Infinity', float('nan'), 2147483648]:
                with self.subTest(field=field, value=value):
                    payload = dict(self.payload, **{field:value})
                    ok, reason, bodies = self.write(payload)
                    self.assertFalse(ok)
                    self.assertEqual(bodies, [])

    def test_text_switch_and_json_types_are_checked_before_any_write(self):
        for field, value in [('subscriptionName', 42), ('gaMeasurementId', []), ('siteGateEnabled', 'false'),
                             ('diskAlertsEnabled', 0), ('assistant', {'dailyBudgetUsd':float('nan')}),
                             ('concurrency', {'needGb':float('nan')})]:
            with self.subTest(field=field):
                ok, reason, bodies = self.write(dict(self.payload, **{field:value}))
                self.assertFalse(ok)
                self.assertEqual(bodies, [])

    def test_optional_plan_blank_strings_and_nulls_remain_uncapped_without_changing_zero(self):
        for value in ['', '  ', None, 0]:
            payload = copy.deepcopy(self.payload)
            payload['subscriptionPlans'][0].update(clones_per_month=value, storage_gb=value)
            ok, reason, bodies = self.write(payload)
            self.assertTrue(ok, reason)
            expected = 0 if value == 0 else None
            self.assertEqual(bodies[0]['subscription_plans'][0]['clones_per_month'], expected)
            self.assertEqual(bodies[0]['subscription_plans'][0]['storage_gb'], expected)

    def test_existing_price_limits_whole_cents_and_free_credit_bounds_remain_enforced(self):
        for field, value in [('freeCredits',0),('freeCredits',1001),('mergeCredits',0),('transcribeCredits',0),('cloneCredits',0)]:
            ok, reason, bodies = self.write(dict(self.payload, **{field:value}))
            self.assertFalse(ok); self.assertEqual(bodies, [])
        for key in ['packs','subscriptionPlans']:
            payload = copy.deepcopy(self.payload);payload[key][0]['price_usd'] = 1.001
            ok, reason, bodies = self.write(payload)
            self.assertFalse(ok);self.assertEqual(bodies, [])

    def test_each_followup_failure_reports_partial_save_and_the_actual_reason(self):
        for number in range(2, 9):
            with self.subTest(request=number):
                ok, reason, bodies = self.write(self.payload, fail_request=number)
                self.assertFalse(ok)
                self.assertIn('Some settings were saved', reason)
                self.assertIn('Synthetic column refusal', reason)
                self.assertEqual(len(bodies), 8)

    def test_main_write_failure_stops_followups_and_shows_actual_reason(self):
        ok, reason, bodies = self.write(self.payload, fail_request=1)
        self.assertFalse(ok)
        self.assertIn('Synthetic column refusal', reason)
        self.assertEqual(len(bodies), 1)

    def test_unreadable_admin_request_never_saves_default_settings(self):
        save = Mock()
        env = source_functions('main.py', ['admin_save_pricing'], dict(_admin_check=lambda request:True,
                               _save_pricing_config=save, JSONResponse=Response))
        request = Mock(json=AsyncMock(side_effect=ValueError('Synthetic malformed JSON')))
        response = asyncio.run(env['admin_save_pricing'](request))
        self.assertEqual(response.status_code, 400)
        self.assertIs(response.content['ok'], False)
        self.assertIn('Reload', response.content['error'])
        save.assert_not_called()


class BillingActionReadinessTests(unittest.TestCase):
    def test_only_boolean_action_flags_are_exposed_and_no_extra_rpc_is_called(self):
        flags=dict(version=2,debit=True,pack=False,refund='false',cancel=True,admin_adjust=True,
                   subscription_grant=False,signup_credits=False,email='private')
        payload={'ready':flags,'lists':{k:{'count':0,'rows':[]} for k in CATEGORIES+EXTRA_CATEGORIES}}
        rpc=Mock(return_value=payload); result=load_health(rpc)
        self.assertEqual(rpc.call_count,1)
        self.assertIs(result['action_ready']['pack'],False)
        self.assertIsNone(result['action_ready']['refund'])
        self.assertNotIn('private',json.dumps(result))
        self.assertEqual(set(result['action_ready']),{'debit','pack','refund','cancel','admin_adjust','subscription_grant','signup_credits'})

    def test_legacy_health_keeps_unknown_new_actions_distinct_from_paused(self):
        payload={'ready':dict(version=1,debit=True,pack=True,refund=True,cancel=True),
                 'lists':{k:{'count':0,'rows':[]} for k in CATEGORIES}}
        def rpc(name, *args, **kwargs):
            if name.endswith('_v2'):
                raise HTTPError('https://example.test',404,'Missing',{},io.BytesIO(b'{}'))
            return payload
        result=load_health(rpc)
        self.assertFalse(result['extended'])
        self.assertNotIn('admin_adjust',result['action_ready'])
        self.assertIs(result['action_ready']['debit'],True)
