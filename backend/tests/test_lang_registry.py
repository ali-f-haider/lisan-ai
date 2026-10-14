import dataclasses
import json
import math
import unittest
import languages as L


class LanguageRegistryTests(unittest.TestCase):
    def test_records_and_names(self):
        self.assertEqual(tuple(r.code for r in L.supported()), ('en','es','fr','pt','it','de','nl','pl'))
        for r in L.supported():
            with self.subTest(code=r.code):
                self.assertEqual(r.whisper_code,r.code)
                self.assertEqual(r.script,'latin')
                self.assertEqual(len(r.stopwords),40)
                self.assertEqual(len(set(r.stopwords)),40)
                self.assertEqual(L.names(r.code,'ar'),r.arabic_name)
                self.assertEqual(L.names(r.code,'native'),r.native_name)
                self.assertFalse(r.beta)
                self.assertEqual(r.variants,())
                json.dumps(dataclasses.asdict(r),allow_nan=False)

    def test_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            L.get('es').code='xx'

    def test_missing_unknown_case_and_garbage(self):
        for value in (None,'','unknown',{},[],6,float('nan'),object()):
            self.assertIs(L.get(value),L.get('en'))
            self.assertFalse(L.is_supported(value))
        self.assertIs(L.get(' ES '),L.get('es'))
        self.assertIs(L.get(dataclasses.replace(L.get('en'),code=[])),L.get('en'))
        self.assertTrue(L.is_supported('FR'))

    def test_policy_matches_ali_decisions(self):
        p=L.LanguagePolicy()
        self.assertEqual(p.enabled_languages,tuple(r.code for r in L.supported()))
        self.assertEqual(p.beta_languages,())
        self.assertFalse(p.auto_detect)
        self.assertFalse(p.regional_entries)
        self.assertEqual(p.subtitle_policy,'source_only')
        self.assertEqual(p.price_policy,'same_as_english')

    def test_estimates_not_measurements(self):
        for r in L.supported():
            self.assertEqual(r.speech_rate.status, 'unmeasured' if r.code in ('nl','pl') else 'estimated')
            self.assertIn('estimated',r.timing_status)
            self.assertIn('not a measured top-40',' '.join(r.native_check))
            self.assertEqual(L.arabic_word_budget(r),(2.0,2.5))

    def test_timing_setting_validates_estimates(self):
        p=L.LanguagePolicy(timing_overrides=(('es',1.25),('fr',math.nan),('pt',0),('de',True)))
        self.assertEqual(L.arabic_word_budget('es',p),(1.6,2.0))
        for code in ('fr','pt','de'):
            self.assertEqual(L.arabic_word_budget(code,p),(2.0,2.5))
