"""The source-language API contract is wired, not an unapplied proposal."""
import json
from pathlib import Path
import unittest
import api_schema

ROOT=Path(__file__).resolve().parents[1]

class ApiLanguageProposalTests(unittest.TestCase):
    def test_proposal_matches_source_and_schema(self):
        spec=json.loads((ROOT/'docs/api/openapi.yaml').read_text(encoding='utf-8'))
        self.assertEqual(spec['components']['schemas']['CreateJob'],api_schema.BODY_SCHEMAS['CreateJob'])
        field=api_schema.BODY_SCHEMAS['CreateJob']['properties']['source_language']
        self.assertEqual(field,spec['components']['schemas']['Job']['properties']['source_language'])
        self.assertEqual(field['default'],'en')
        self.assertNotIn('source_language',api_schema.BODY_SCHEMAS['CreateJob']['required'])

    def test_real_validator_optional_enums_defaults_and_unknowns(self):
        body={'kind':'long','filename':'invented.mp4','size_bytes':100,'rights_confirmed':True,'terms_version':'1'}
        self.assertIsNone(api_schema.validate_body('CreateJob',body))
        for code in ('en','es','fr','pt','it','de','nl','pl'):
            self.assertIsNone(api_schema.validate_body('CreateJob',dict(body,source_language=code)))
        for code in ('ES','','xx',None,{},4):
            self.assertIsNotNone(api_schema.validate_body('CreateJob',dict(body,source_language=code)))
        self.assertIsNotNone(api_schema.validate_body('CreateJob',dict(body,other_field='es')))
