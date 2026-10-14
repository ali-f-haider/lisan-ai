"""New screens are exclusive on this invented set; legacy false matches are findings."""
import json
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lang_text as T


class LanguageConfusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = json.loads((ROOT/'tests/fixtures/lang/confusion.json').read_text(encoding='utf-8'))['paragraphs']
        cls.recorded = json.loads((ROOT/'docs/languages/job17-confusion.json').read_text(encoding='utf-8'))
        cls.codes = ('en', 'es', 'fr', 'pt', 'it', 'de', 'nl', 'pl')

    def test_twenty_paragraphs_per_language_and_correct_checks(self):
        self.assertEqual(len(self.rows), 160)
        for code in self.codes:
            group = [r for r in self.rows if r['source_language'] == code]
            self.assertEqual(len(group), 20)
            for row in group:
                with self.subTest(id=row['id']):
                    self.assertTrue(3 <= len(row['text'].splitlines()) <= 6)
                    self.assertEqual(T.looks_like(row['text'], code)[0], 'compatible')

    def test_dutch_and_polish_screen_reject_other_seven_languages(self):
        for row in self.rows:
            for code in ('nl', 'pl'):
                with self.subTest(id=row['id'], tested_as=code):
                    self.assertEqual(T.looks_like(row['text'], code)[0] == 'compatible', row['source_language'] == code)

    def test_full_table_and_every_false_match_are_recorded_as_findings(self):
        matrix = {s:{t:0 for t in self.codes} for s in self.codes}
        findings = []
        for row in self.rows:
            for code in self.codes:
                if T.looks_like(row['text'], code)[0] == 'compatible':
                    matrix[row['source_language']][code] += 1
                    if code != row['source_language']:
                        findings.append({'paragraph_id':row['id'], 'source':row['source_language'], 'accepted_as':code, 'text':row['text']})
        self.assertEqual(matrix, self.recorded['compatible'])
        self.assertEqual(findings, self.recorded['false_matches'])
        # Deliberately pin legacy behavior, including false English matches.
        self.assertEqual(sum(f['accepted_as']=='en' for f in findings), 140)

    def test_short_names_numbers_and_balanced_mixture_stay_inconclusive(self):
        inputs = ('', 'Ja.', 'Tak.', 'Marek Łukasz IJsselmeer', '3,5 2026 01:30',
                  'ik jij het wij ona są nie jeśli', 'This is English. Dit is Nederlands.')
        for text in inputs:
            for code in ('nl', 'pl'):
                with self.subTest(text=text, code=code):
                    self.assertNotEqual(T.looks_like(text, code)[0], 'compatible')
        for text in inputs[:5]:
            for code in ('nl', 'pl'):
                self.assertEqual(T.looks_like(text, code)[0], 'inconclusive')


if __name__ == '__main__':
    unittest.main()
