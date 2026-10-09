"""Signup limits, real setting mapping and exact rollback evidence; offline only."""
import ast
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import credit_billing as cb
from signup_credits import configured_amount,validate_amount
from test_shortdub_upgrade import source_functions

ROOT=Path(__file__).resolve().parents[1]


class SignupCreditTests(unittest.TestCase):
    def test_configured_whole_allowances_include_both_limits(self):
        for value in (1,100,250,1000,'250'):
            self.assertEqual(configured_amount(value),int(value))
            self.assertEqual(validate_amount(value),int(value))

    def test_missing_invalid_nonfinite_zero_or_unbounded_setting_falls_back_to_100(self):
        for value in (None,True,False,0,-1,1001,1.5,'bad','NaN','Infinity','-Infinity',10**30):
            self.assertEqual(configured_amount(value),100)
            with self.assertRaises(ValueError):validate_amount(value)

    def test_invalid_signup_amount_is_refused_before_any_pricing_write(self):
        env=source_functions('main.py',['_save_pricing_config'],{'SUPABASE_URL':'https://example.test','SUPABASE_SERVICE_KEY':'synthetic'})
        for value in (0,1001,True,1.5,float('nan')):
            with patch('urllib.request.urlopen') as network:
                ok,message=env['_save_pricing_config']({'freeCredits':value})
                self.assertFalse(ok);self.assertIn('1 to 1000',message);network.assert_not_called()
        self.assertEqual(cb.validate_pricing({'freeCredits':'250'})['freeCredits'],250)

    def test_setting_is_the_same_column_and_singleton_as_the_real_admin_save_and_read(self):
        source=(ROOT/'main.py').read_text(encoding='utf-8-sig');tree=ast.parse(source)
        functions={n.name:ast.get_source_segment(source,n) for n in tree.body if isinstance(n,ast.FunctionDef)}
        self.assertIn('"free_credits": config.get("freeCredits"',functions['_save_pricing_config'])
        self.assertIn('"id": "singleton"',functions['_save_pricing_config'])
        self.assertIn('pricing_config?id=eq.singleton',functions['_get_pricing_config'])
        self.assertIn('order=updated_at.desc',functions['_get_pricing_config'])
        sql=(ROOT/'sql/2026-10-08-04-signup-credits.sql').read_text(encoding='utf-8')
        self.assertIn("to_jsonb(c)->>'free_credits'",sql);self.assertIn("WHERE c.id='singleton' ORDER BY c.updated_at DESC",sql)

    def test_optional_rollback_contains_the_exact_supplied_original_function(self):
        old=(ROOT/'tests/fixtures/signup_credit_trigger.sql').read_text(encoding='utf-8')
        definition=old[old.index('CREATE OR REPLACE FUNCTION'):old.index('CREATE TRIGGER')].strip()
        sql=(ROOT/'sql/2026-10-08-04-signup-credits.sql').read_text(encoding='utf-8')
        comments='\n'.join(line[3:] for line in sql.splitlines() if line.startswith('-- '))
        self.assertIn(definition,comments)
        self.assertEqual(sql.count('CREATE OR REPLACE FUNCTION public.handle_new_user()'),2)

    def test_public_and_admin_pricing_show_the_same_bounded_signup_allowance_as_the_trigger(self):
        env=source_functions('main.py',['_get_pricing_config'],dict(SUPABASE_URL='https://example.test',
            SUPABASE_SERVICE_KEY='synthetic',DEFAULT_PACKS=[],DEFAULT_SUBSCRIPTION_PLANS=[],json=json,
            _assistant_cfg=lambda value:value,_conc_cfg=lambda value:value,_gemini_cpc=lambda value:value,
            _pricing_last_good=type('NoCopy',(),{'remember':lambda self,config:None,'recall':lambda self:(None,None)})()))
        for stored,expected in [(250,250),(None,100),(0,100),(1001,100)]:
            with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps([{'free_credits':stored}]).encode())):
                self.assertEqual(env['_get_pricing_config']()['freeCredits'],expected)
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'[]')):
            self.assertEqual(env['_get_pricing_config']()['freeCredits'],100)
