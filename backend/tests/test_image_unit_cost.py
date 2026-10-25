import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import image_unit as I
import resource_meter as R

FIX=Path(__file__).parent/'fixtures/picture'


class CostTests(unittest.TestCase):
    def test_hand_computed_one_minute_low_resolution(self):
        with patch.object(R,'GEMINI_IN_USD_PER_M',.3),patch.object(R,'GEMINI_OUT_USD_PER_M',2.5):
            r=I.estimate_usd(60,10,{'fps':1.,'media_resolution':'low'})
        self.assertEqual(r['input_tokens'],{'low':5052,'high':7024})
        self.assertEqual(r['output_tokens'],{'low':896,'high':2304})
        self.assertEqual(r['thinking_tokens'],{'low':1024,'high':8192})
        self.assertAlmostEqual(r['low_usd'],.0063156)
        self.assertAlmostEqual(r['high_usd'],.0283472)
        self.assertEqual(r['audio_tokens'],0)

    def test_admin_rate_override_and_chunk_count_honoured(self):
        with patch.object(R,'GEMINI_IN_USD_PER_M',3),patch.object(R,'GEMINI_OUT_USD_PER_M',25):
            r=I.estimate_usd(60,10,{'fps':1.,'media_resolution':'low'})
        self.assertAlmostEqual(r['low_usd'],.063156)
        self.assertEqual(I.estimate_usd(3600,600)['chunks'],6)
        self.assertGreater(I.estimate_usd(60,10,{'fps':2})['low_usd'],I.estimate_usd(60,10,{'fps':1})['low_usd'])

    def test_actual_tokens_thinking_and_unexpected_audio_use_existing_pricer(self):
        raw=json.loads((FIX/'usage.json').read_text())
        with patch.object(R,'GEMINI_IN_USD_PER_M',.3),patch.object(R,'GEMINI_OUT_USD_PER_M',2.5),patch.object(R,'GEMINI_AUDIO_IN_USD_PER_M',1):
            original=copy.deepcopy(raw)
            self.assertEqual(I.cost_report(raw)['usd'],.0105)
            raw['usageMetadata']['promptTokensDetails']=[dict(modality='AUDIO',tokenCount=2000)]
            r=I.cost_report(raw)
            self.assertAlmostEqual(r['usd'],.0119);self.assertEqual(r['audio_tokens'],2000)
            self.assertEqual(I.cost_report([original,raw])['usd'],.0224)

    def test_missing_and_invalid_usage_is_json_safe_not_fabricated(self):
        for value in (None,{},[],{'usageMetadata':{'promptTokenCount':float('nan'),'candidatesTokenCount':float('inf')}}):
            report=I.cost_report(value);json.dumps(report,allow_nan=False)
        self.assertEqual(I.cost_report(None)['missing_usage'],1)
        self.assertEqual(I.cost_report({'usageMetadata':{}})['usd'],0.)

    def test_usage_counters_once_and_generic_tokens_not_double_counted(self):
        import app_state
        job='picture-unit-invented-test'
        app_state.USAGE.pop(job,None)
        try:
            report=I.cost_report(json.loads((FIX/'usage.json').read_text()))
            I._record(job,report)
            b=app_state.usage_bucket(job)
            self.assertEqual(b['gemini_in'],10000);self.assertEqual(b['picture_in'],10000)
            self.assertEqual(b['gemini_out'],1000);self.assertEqual(b['gemini_thoughts'],2000)
            self.assertEqual(b['picture_out'],3000);self.assertAlmostEqual(b['picture_usd'],report['usd'])
        finally:app_state.USAGE.pop(job,None)


if __name__=='__main__':unittest.main()
