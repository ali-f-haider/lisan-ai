"""Pins complete outputs captured from the six helpers BEFORE Job17 edits."""
import dataclasses
import hashlib
import json
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import languages as L
import lang_text as T
import language_prompts as P


class Job17LegacyParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = json.loads((ROOT/'tests/fixtures/lang/job17_legacy_outputs.json').read_text(encoding='utf-8'))

    def test_six_frozen_records_are_identical(self):
        self.assertEqual(json.loads(json.dumps([dataclasses.asdict(L.get_record(r['code'])) for r in self.before['records']])), self.before['records'])

    def test_2754_full_text_return_structures_are_identical(self):
        count = 0
        for code, data in self.before['languages'].items():
            for expected in data['samples']:
                value = expected['input']
                with self.subTest(code=code, text=value):
                    actual={'input':value,'tidy_end':T.ends_sentence(value,code), 'subtitle_end':T.ends_sentence(value,code,profile='subtitle'),
                        'tidy_split':T.split_sentences(value,code), 'subtitle_split':T.split_sentences(value,code,profile='subtitle'),
                        'start':T.starts_sentence(value,code),'tokens':[dataclasses.asdict(t) for t in T.tokenize(value,code)],
                        'matching':T.normalise_for_match(value,code),'clean':list(T.clean_subtitle_line(value,code)),
                        'looks_like':json.loads(json.dumps(T.looks_like(value,code))), 'arabic_share':T.arabic_script_share(value),'latin_share':T.latin_share(value)}
                    digest=hashlib.sha256(json.dumps(actual,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()
                    self.assertEqual(digest, expected['output_sha256'])
                    count += 1
        self.assertEqual(count, 2754)

    def test_72_prompt_and_context_outputs_are_identical(self):
        rows, genders = self.before['prompt_inputs']['rows'], self.before['prompt_inputs']['genders']
        count = 0
        for code, data in self.before['languages'].items():
            actual={'notes':P.language_notes(code),'gender':P.gender_rules(genders,code),
                'translation':P.translation_prompt(rows,code), 'translation_context':P.translation_prompt(rows,code,gender=P.gender_context(rows,['gold_0'],genders,code)),
                'emotion':P.emotion_prompt('An invented setting',code),'scene':P.scene_prompt(12.,True,code),
                'speaker_audio':P.speaker_prompt('audio',rows,code),'speaker_dialogue':P.speaker_prompt('dialogue',rows,code),
                'planner':P.line_planner_prompt(rows,source_language=code),'people':P.people_rules(code),
                'context':P.gender_context(rows,['gold_0','gold_1'],genders,code),'glossary':P.glossary_block([{'en':'Mina','ar':'مينا'}],code)}
            with self.subTest(code=code):
                self.assertEqual(actual, data['prompts'])
                count += len(actual)
        self.assertEqual(count, 72)

    def test_all_original_subtitle_fixtures_are_identical(self):
        for code, data in self.before['languages'].items():
            for cue in data['subtitle']['cues']:
                self.assertEqual([list(T.clean_subtitle_line(x,code)) for x in cue['lines']], cue['baseline_clean'])
                self.assertEqual(T.speaker_change_in_cue(cue['lines'],code), cue['baseline_speakers'])


if __name__ == '__main__':
    unittest.main()
