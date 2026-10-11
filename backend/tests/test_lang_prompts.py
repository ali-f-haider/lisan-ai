import ast
import json
from types import SimpleNamespace
import unittest
import language_prompts as P
import languages as L
from lang_source_capture import source_namespace,translation,emotion,gender_namespace,ROOT


def rows():
    return [{'segment_id':'s1','start':0.0,'end':3.25,'speaker':'A','text':'Good morning, sir.','cut_check':'end'},
            {'segment_id':'s2','start':3.25,'end':6.0,'speaker':'B','text':'Wait... English is a label here.'}]


class LanguagePromptTests(unittest.TestCase):
    def test_translation_current_source_offline(self):
        sample=rows()
        segments=[SimpleNamespace(**r) for r in sample]
        genders={'A':{'gender':'female','source':'chosen'},'B':{'gender':'male','source':'heard'}}
        space=gender_namespace()
        g={'speakers':space['speaker_table'](genders),'lines':space['build_context'](sample,['s1','s2'],genders)}
        for options in ({},{'glossary':[{'en':'English','ar':'إنجليش'}]},{'paces':{'s1':'fast','s2':'very fast'},'gender':g}):
            current=translation(segments,**options)
            for code in (None,'en','EN','bad'):
                self.assertEqual(P.translation_prompt(segments,code,**options).encode(),current.encode())
                self.assertEqual(P.translation_prompt(sample,code,**options),current)

    def test_emotion_current_source(self):
        for scene in ('','A room\n with English text.','x'*4500,None):
            self.assertEqual(P.emotion_prompt(scene,'en'),emotion(scene))

    def test_scene_current_source(self):
        old=source_namespace('scene_context.py',('_prompt',),('SETTINGS',))['_prompt']
        for sec in (0,3.5,3600):
            for frames in (False,True):
                self.assertEqual(P.scene_prompt(sec,frames,'en'),old(sec,frames))

    def test_speaker_prompt_read_only_source(self):
        old=source_namespace('speaker_vote_live.py',('_row_json','transcript','prompt_for'))['prompt_for']
        for kind in ('audio','dialogue','other'):
            self.assertEqual(P.speaker_prompt(kind,rows(),'en'),old(kind,rows()))

    def test_planner_current_source(self):
        old=source_namespace('line_planner.py',('_label','build_prompt'),('PROMPT',))['build_prompt']
        for before,after in (([],[]),(rows()[:1],rows()[1:])):
            self.assertEqual(P.line_planner_prompt(rows(),before,after,'en'),old(rows(),before,after))

    def test_gender_rules_context_roles_people(self):
        old=gender_namespace()
        genders={'A':{'gender':'female','source':'chosen'},'B':{'gender':'male','source':'heard'}}
        roles={'B':'judge'}
        sample=rows()+[{'segment_id':'s3','start':6,'end':8,'speaker':'A','text':'Your Honor, thank you.'}]
        expected={'speakers':old['speaker_table'](genders,roles),'lines':old['build_context'](sample,['s1','s3'],genders,roles)}
        self.assertEqual(P.gender_context(sample,['s1','s3'],genders,'en',roles=roles),expected)
        rules=source_namespace('gemini_service.py',(),('GENDER_RULES',))['GENDER_RULES']
        self.assertEqual(P.gender_rules(expected['speakers'],'en'),rules.replace('{speakers}',json.dumps(expected['speakers'],ensure_ascii=False)))
        self.assertEqual(P.people_rules('en'),old['PEOPLE_PROMPT'])

    def test_new_languages_keep_data_untouched(self):
        for code in L.NEW_LANGUAGES:
            prompt=P.translation_prompt(rows(),code,glossary=[{'en':'English','ar':'English'}])
            self.assertIn('following '+L.names(code)+' audio segments',prompt)
            self.assertNotIn('20% longer',prompt)
            self.assertIn('English is a label here.',prompt)
            self.assertIn('Modern Standard Arabic',prompt)
            self.assertIn(L.names(code),P.emotion_prompt('English in data',code))
            self.assertIn('English in data',P.emotion_prompt('English in data',code))
            self.assertIn(L.names(code),P.scene_prompt(30,False,code))
            self.assertIn(L.names(code),P.speaker_prompt('audio',rows(),code))
            self.assertIn(L.names(code),P.line_planner_prompt(rows(),source_language=code))
            self.assertIn(L.names(code),P.people_rules(code))

    def test_timing_setting_new_languages_only(self):
        p=L.LanguagePolicy(timing_overrides=(('es',1.25),('en',1.25)))
        self.assertIn('Maximum 1.6 to 2 Arabic words',P.translation_prompt(rows(),'es',policy=p))
        self.assertEqual(P.translation_prompt(rows(),'en',policy=p),P.translation_prompt(rows(),'en'))

    def test_no_network_engine_imports(self):
        for file in ('languages.py','lang_text.py','language_prompts.py'):
            imports=[]
            for n in ast.walk(ast.parse((ROOT/file).read_text(encoding='utf-8'))):
                if isinstance(n,ast.Import):
                    imports.extend(v.name for v in n.names)
                elif isinstance(n,ast.ImportFrom):
                    imports.append(n.module)
            self.assertTrue(set(imports)<= {'dataclasses','math','html','re','unicodedata','languages','json'})

    def test_dry_no_key_no_transport(self):
        from unittest.mock import patch
        from tools import language_trial as trial
        with patch.dict('os.environ',{},clear=True),patch.object(trial,'request_translation',side_effect=AssertionError('network')):
            report=trial.run(['es'],dry=True,limit=2)
        self.assertEqual(report['languages']['es']['attempted'],0)
        self.assertIsNone(report['languages']['es']['pass_percent'])
        self.assertEqual(len(report['cases']),2)

    def test_trial_mock_reports_tokens_thought_cost_and_failures(self):
        from unittest.mock import patch
        from tools import language_trial as trial
        answer={'candidates':[{'content':{'parts':[{'text':json.dumps([{'segment_id':'es_gender_female','arabic_text':'جَاهِزَة'}])}]}}],
                'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':20,'thoughtsTokenCount':30}}
        with patch.dict('os.environ',{'GEMINI_API_KEY':'synthetic-test-key'}),patch.object(trial,'request_translation',return_value=(answer,None)) as request:
            report=trial.run(['es'],limit=1,input_price=2.0,output_price=4.0)
        self.assertEqual(request.call_count,1)
        total=report['languages']['es']
        self.assertEqual((total['attempted'],total['passed'],total['input'],total['output'],total['thoughts']),(1,1,100,20,30))
        self.assertAlmostEqual(total['reported_cost_usd'],0.0004)
        self.assertEqual(total['pass_percent'],100.0)
        with patch.dict('os.environ',{'GEMINI_API_KEY':'synthetic-test-key'}),patch.object(trial,'request_translation',return_value=(None,'synthetic failure')):
            result=trial.run(['fr'],limit=1,input_price=2.0,output_price=4.0)
        self.assertEqual(result['languages']['fr']['failed'],1)
        self.assertEqual(result['languages']['fr']['missing_usage'],1)
        self.assertIsNone(result['cases'][0]['reported_cost_usd'])

    def test_live_trial_requires_explicit_prices_and_key(self):
        from unittest.mock import patch
        from tools import language_trial as trial
        with patch.dict('os.environ',{},clear=True),patch.object(trial,'request_translation',side_effect=AssertionError('network')):
            for prices in ((None,None),(-1,2),(float('nan'),2),(1,2)):
                with self.assertRaises(ValueError):
                    trial.run(['es'],input_price=prices[0],output_price=prices[1])
