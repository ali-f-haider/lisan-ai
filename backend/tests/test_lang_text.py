import json
from pathlib import Path
import random
import re
import unittest
import lang_text as T
import languages as L

FIXTURES=Path(__file__).parent/'fixtures'


class LanguageTextTests(unittest.TestCase):
    def test_dialogue_fixtures(self):
        for code in L.NEW_LANGUAGES:
            data=json.loads((FIXTURES/'lang'/code/'dialogue.json').read_text(encoding='utf-8'))
            self.assertEqual(len(data['lines']),30)
            self.assertEqual(T.speaker_change_in_cue(data['shared_cue']['lines'],code),data['shared_cue']['expected_speaker_changes'])
            for r in data['lines']:
                with self.subTest(code=code,id=r['id']):
                    self.assertEqual(T.ends_sentence(r['text'],code),r['expected_ends_sentence'])
                    self.assertEqual(T.split_sentences(r['text'],code),r['expected_splits'])

    def test_subtitle_fixtures(self):
        for r in L.supported():
            expected=json.loads((FIXTURES/'lang'/r.code/'sample.expected.json').read_text(encoding='utf-8'))
            srt=(FIXTURES/'lang'/r.code/'sample.srt').read_text(encoding='utf-8')
            for cue in expected['cues']:
                self.assertTrue(all(line in srt for line in cue['lines']))
                self.assertEqual([v for line in cue['lines'] if (v:=T.strip_subtitle_markup(line,r.code))],cue['cleaned'])
                self.assertEqual(T.speaker_change_in_cue(cue['lines'],r.code),cue['speaker_changes'])

    def test_token_offsets_lossless_roundtrip(self):
        rng=random.Random(16016)
        alphabet='Aa Éé ñ ç œ ß Ä ö 0123.,!?—-’\'\t\n\u00a0\u202fعَ🙂\u0301'
        samples=[' l’homme aujourd’hui 3,5 bleu-vert ','dell’acqua e un’amica','I-If 3.5...']
        samples+=[''.join(rng.choice(alphabet) for _ in range(rng.randrange(80))) for _ in range(200)]
        for r in L.supported():
            for text in samples:
                tokens=T.tokenize(text,r)
                self.assertEqual(''.join(t.original for t in tokens),text)
                cursor=0
                for t in tokens:
                    self.assertEqual(t.start,cursor)
                    self.assertEqual(text[t.start:t.end],t.original)
                    cursor=t.end
                self.assertEqual(cursor,len(text))

    def test_matching_original_separate(self):
        self.assertEqual(T.normalise_for_match('L’homme bleu-vert coûte 3,5 €.','fr'),'l homme bleu vert coute 3,5')
        self.assertEqual(T.normalise_for_match('Un’amica dell’acqua','it'),'un amica dell acqua')
        self.assertEqual(T.normalise_for_match('Straße Äpfel 10','de'),'strasse apfel 10')
        self.assertEqual(T.fold_accents('Été œ Æ'),'Ete oe AE')

    def test_abbreviation_decimal_ordinal(self):
        for code,text in [('es','La Dra. Mora mide 3,5 m. Luego sale.'),('fr','M. Luc lit. Puis part.'),
                          ('it','La Sig.ra Mora viene. Poi parte.'),('de','Das ist z. B. am 1. Mai. Dann geht er.')]:
            parts=T.split_sentences(text,code)
            self.assertEqual(len(parts),2)
            self.assertEqual(''.join(parts),text)
        self.assertFalse(T.ends_sentence('Platz 1.','de'))
        self.assertEqual(T.split_sentences('Wert 3.5. Dann weiter.','de'),['Wert 3.5. ','Dann weiter.'])
        self.assertFalse(T.ends_sentence('Warte...','de',profile='subtitle'))

    def test_sentence_start_and_speaker_evidence(self):
        self.assertTrue(T.starts_sentence(' ¿qué?','es'))
        self.assertTrue(T.starts_sentence('« Bonjour »','fr'))
        self.assertFalse(T.starts_sentence('bonjour','fr'))
        self.assertEqual(T.speaker_change_in_cue(['« Bonjour »','« Bonsoir »'],'fr'),[])
        self.assertEqual(T.speaker_change_in_cue(['— Bonjour'],'fr',has_previous=True),[0])

    def test_accents_and_advisory_limits(self):
        self.assertEqual(T.latin_share('éàîöçß'),1.0)
        self.assertEqual(T.arabic_script_share('عَرَبِيّ ١٢'),1.0)
        self.assertEqual(T.latin_share('éع'),0.5)
        self.assertEqual(T.latin_share('123?!'),0.0)
        self.assertEqual(T.looks_like('é','fr')[0],'inconclusive')
        self.assertEqual(T.looks_like('اللغة العربية هنا','fr')[0],'mismatch')
        self.assertEqual(T.looks_like('Je suis dans la petite maison','fr')[0],'compatible')
        self.assertEqual(T.looks_like('la','es')[0],'inconclusive')
        json.dumps(T.looks_like('élève ¿qué?','fr'),allow_nan=False)

    def test_garbage_never_raises(self):
        for v in (None,[],{},5,float('nan'),object(),'\ud800','\0'):
            for r in L.supported():
                for f in (T.fold_accents,T.latin_share,T.arabic_script_share):
                    f(v)
                for f in (T.ends_sentence,T.starts_sentence,T.split_sentences,T.tokenize,T.normalise_for_match,
                          T.strip_subtitle_markup,T.looks_like,T.speaker_change_in_cue):
                    f(v,r)

    def test_gold_bounded_marked_regexes(self):
        from tools.language_trial import evaluate
        for code in L.NEW_LANGUAGES:
            data=json.loads((FIXTURES/'lang_gold'/f'{code}.json').read_text(encoding='utf-8'))
            self.assertEqual(len(data['cases']),25)
            self.assertEqual(len({c['id'] for c in data['cases']}),25)
            for c in data['cases']:
                self.assertTrue(c['context_lines'])
                for check in c['checks']:
                    re.compile(check['pattern'])
                    self.assertIn('needs a native check',check['native_review'])
                self.assertFalse(evaluate('unrelated text',c['checks'])['passed'])

    def test_gold_checker_modes(self):
        from tools.language_trial import evaluate
        self.assertTrue(evaluate('جَاهِزَة',[{'pattern':'جاهزة','mode':'strip_tashkeel'}])['passed'])
        self.assertFalse(evaluate('جَاهِزَة',[{'pattern':'جاهزة','mode':'raw'}])['passed'])
