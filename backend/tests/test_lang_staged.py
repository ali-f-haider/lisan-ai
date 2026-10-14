"""Job17 independent punctuation fixtures, staged gates and translation screens."""
import dataclasses
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import languages as L
import lang_text as T
import language_prompts as P
import source_jobs
from tools import language_trial as trial

FIX = ROOT / 'tests' / 'fixtures'


class StagedRegistryTests(unittest.TestCase):
    def test_enabled_public_lookups_are_eight(self):
        self.assertEqual(tuple(r.code for r in L.supported()), ('en','es','fr','pt','it','de','nl','pl'))
        self.assertEqual(L.LanguagePolicy().enabled_languages, L.ENABLED_LANGUAGES)
        for code in ('nl','pl',' NL ','PL'):
            self.assertTrue(L.is_supported(code))
            self.assertEqual(L.get(code).code, code.strip().lower())
            self.assertEqual(source_jobs.validate(code), code.strip().lower())

    def test_old_and_new_saved_jobs_keep_their_original_source(self):
        with tempfile.TemporaryDirectory() as d:
            job = '12345678-1234-1234-1234-123456789abc'
            self.assertEqual(source_jobs.language(d, job), 'en')
            for code in L.ENABLED_LANGUAGES:
                source_jobs.set_language(d, job, code)
                self.assertEqual(source_jobs.language(d, job), code)

    def test_metadata_and_frozen_records(self):
        for code, name in [('nl', 'الهولندية'), ('pl', 'البولندية')]:
            r = L.get_record(code)
            self.assertEqual(r.arabic_name, name)
            self.assertEqual(r.whisper_code, code)
            self.assertEqual(len(r.stopwords), 40)
            self.assertEqual(len(set(r.stopwords)), 40)
            self.assertFalse(r.beta)
            self.assertEqual(r.variants, ())
            self.assertIsNone(r.speech_rate.value)
            self.assertEqual(r.timing_ratio_to_english, 1.0)
            self.assertEqual(r.timing_status, 'estimated')
            self.assertIn(('„', '”'), r.quote_pairs)
            self.assertNotIn(('„', '“'), r.quote_pairs)
            self.assertEqual(L.arabic_word_budget(r), (2.0, 2.5))
            with self.assertRaises(dataclasses.FrozenInstanceError):
                r.code = 'other'

    def test_unknown_missing_case_and_garbage(self):
        for value in (None, '', '??', [], {}, 42):
            self.assertEqual(L.get_record(value).code, 'en')
            self.assertEqual(L.get(value).code, 'en')
            self.assertFalse(L.is_supported(value))
        self.assertEqual(L.get(' FR ').code, 'fr')
        for value in (None, {}, [], 42, float('nan')):
            for code in ('nl', 'pl'):
                self.assertFalse(T.ends_sentence(value, code))
                self.assertEqual(T.split_sentences(value, code), [])
                self.assertEqual(T.tokenize(value, code), [])
                self.assertEqual(T.strip_subtitle_markup(value, code), '')
                self.assertEqual(T.speaker_change_in_cue(value, code), [])
                self.assertEqual(T.looks_like(value, code)[0], 'inconclusive')
                self.assertEqual(T.latin_share(value), 0)
                self.assertEqual(T.arabic_script_share(value), 0)


class StagedTextTests(unittest.TestCase):
    def test_sixty_independent_sentence_expectations(self):
        for code in ('nl', 'pl'):
            data = json.loads((FIX / 'lang' / code / 'dialogue.json').read_text(encoding='utf-8'))
            self.assertEqual(len(data['lines']), 30)
            for row in data['lines']:
                with self.subTest(code=code, id=row['id']):
                    self.assertEqual(T.ends_sentence(row['text'], code), row['expected_ends_sentence'])
                    self.assertEqual(T.split_sentences(row['text'], code), row['expected_splits'])
                    self.assertEqual(''.join(T.split_sentences(row['text'], code)), row['text'])
                    tokens = T.tokenize(row['text'], code)
                    self.assertEqual(''.join(t.original for t in tokens), row['text'])
                    end = 0
                    for token in tokens:
                        self.assertEqual(token.start, end)
                        self.assertEqual(row['text'][token.start:token.end], token.original)
                        end = token.end
                    self.assertEqual(end, len(row['text']))
            self.assertEqual(T.speaker_change_in_cue(data['shared_cue']['lines'], code), [1])

    def test_subtitles_match_independent_cleaned_text_and_change_positions(self):
        for code in ('nl', 'pl'):
            base = FIX / 'lang' / code
            expected = json.loads((base / 'sample.expected.json').read_text(encoding='utf-8'))['cues']
            raw = (base / 'sample.srt').read_text(encoding='utf-8').strip().split('\n\n')
            self.assertEqual(len(raw), len(expected))
            for block, cue in zip(raw, expected):
                lines = block.splitlines()[2:]
                self.assertEqual(lines, cue['lines'])
                self.assertEqual([s for s in (T.strip_subtitle_markup(x, code) for x in lines) if s], cue['cleaned'])
                self.assertEqual(T.speaker_change_in_cue(lines, code), cue['speaker_changes'])
        self.assertEqual(T.speaker_change_in_cue(['- Hallo.'], 'nl', has_previous=True), [0])

    def test_polish_fold_is_scoped_and_names_are_not_stemmed(self):
        for text, folded in [('łódź', 'lodz'), ('Łukasz', 'Lukasz'), ('wąż', 'waz'), ('żółć', 'zolc')]:
            self.assertEqual(T.fold_accents(text, 'pl'), folded)
        self.assertEqual(T.fold_accents('łŁ'), 'łŁ')
        for code in ('en', 'es', 'fr', 'pt', 'it', 'de', 'nl'):
            self.assertEqual(T.fold_accents('łŁ', code), 'łŁ')
        self.assertEqual(T.normalise_for_match('Marek Marka Markiem', 'pl'), 'marek marka markiem')
        self.assertEqual(T.latin_share('łŁąęćńóśźż'), 1.0)

    def test_dutch_elisions_and_compound_matching_never_change_source(self):
        text = "'t 's avonds ’n m'n z’n d’r zo'n ie IJsselmeer bedrijfsaansprakelijkheidsverzekering"
        self.assertEqual(T.normalise_for_match(text, 'nl'), 't s avonds n m n z n d r zo n ie ijsselmeer bedrijfsaansprakelijkheidsverzekering')
        self.assertEqual(''.join(t.original for t in T.tokenize(text, 'nl')), text)
        self.assertTrue(T.starts_sentence('„IJsselmeer is mooi.”', 'nl'))
        self.assertFalse(T.starts_sentence('als hij komt', 'nl'))

    def test_new_profile_endings_abbreviations_and_no_syntax_guess(self):
        for code in ('nl', 'pl'):
            for ending in ('Wacht?!', '„Wacht.” ', 'Wacht…'):
                self.assertTrue(T.ends_sentence(ending, code))
            self.assertFalse(T.ends_sentence('Wacht…', code, profile='subtitle'))
        self.assertEqual(T.split_sentences('We lezen etc. en meer.', 'nl'), ['We lezen etc. en meer.'])
        self.assertFalse(T.ends_sentence('mr.', 'nl'))
        self.assertFalse(T.ends_sentence('1.', 'pl'))
        self.assertFalse(T.ends_sentence('w 2026 r.', 'pl'))


class StagedPromptAndGoldTests(unittest.TestCase):
    def test_every_builder_has_notes_without_rewriting_source_data(self):
        rows = [{'segment_id':'one', 'start':0., 'end':6., 'text':'INVENTED DATA', 'speaker':'A'}]
        for code in ('nl', 'pl'):
            notes = P.language_notes(code)
            for prompt in (P.translation_prompt(rows, code), P.emotion_prompt('invented', code),
                           P.scene_prompt(6., True, code), P.speaker_prompt('audio', rows, code),
                           P.speaker_prompt('dialogue', rows, code), P.line_planner_prompt(rows, source_language=code),
                           P.gender_rules({'A':'unknown'}, code), P.people_rules(code)):
                self.assertIn(notes, prompt)
            self.assertEqual(rows[0]['text'], 'INVENTED DATA')
        self.assertIn('zrobiłem/zrobiłam', P.language_notes('pl'))
        self.assertIn('02:30/14:30', P.language_notes('nl'))

    def test_fifty_provisional_screens_have_positive_and_negative_examples(self):
        for code in ('nl', 'pl'):
            cases = json.loads((FIX / 'lang_gold' / (code+'.json')).read_text(encoding='utf-8'))['cases']
            self.assertEqual(len(cases), 25)
            self.assertEqual(len({x['id'] for x in cases}), 25)
            for case in cases:
                with self.subTest(id=case['id']):
                    self.assertIn('needs a native check', case['native_review'])
                    self.assertTrue(case['context_lines'])
                    self.assertTrue(trial.evaluate(case['screen_example'], case['checks'])['passed'])
                    self.assertFalse(trial.evaluate(case['screen_counterexample'], case['checks'])['passed'])

    def test_explicit_dry_prepares_fifty_without_keys_engines_or_network(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
             patch.object(trial, 'request_translation', side_effect=AssertionError('paid call forbidden')):
            report = trial.run(['nl', 'pl'], dry=True)
        self.assertEqual(len(report['cases']), 50)
        for total in report['languages'].values():
            self.assertEqual(total['prepared'], 25)
            self.assertEqual(total['attempted'], 0)
            self.assertIsNone(total['pass_percent'])
            self.assertEqual(total['reported_cost_usd'], 0)
        self.assertEqual(trial.TRIAL_LANGUAGES[:5], L.NEW_LANGUAGES)


if __name__ == '__main__':
    unittest.main()
