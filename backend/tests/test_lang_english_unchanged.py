import json
from pathlib import Path
import unittest
import line_tidy
import subs_align
import lang_check
import lang_text as T

FIXTURES=Path(__file__).parent/'fixtures'/'lang'/'en'


class EnglishUnchangedTests(unittest.TestCase):
    def test_at_least_300_current_helper_comparisons(self):
        strings=json.loads((FIXTURES/'comparison.json').read_text(encoding='utf-8'))['strings']
        self.assertGreaterEqual(len(strings),300)
        for text in strings:
            for code in (None,'en','EN','unknown'):
                with self.subTest(text=text,code=code):
                    self.assertEqual(T.ends_sentence(text,code,profile='tidy'),line_tidy._ends_sentence(text))
                    self.assertEqual(T.ends_sentence(text,code,profile='subtitle'),subs_align._ends_sentence(text))
                    self.assertEqual(T.legacy_norm(text),subs_align.norm(text))
                    self.assertEqual(T.legacy_surface_tokens(text),subs_align._surface_tokens(text))
                    self.assertEqual(T.clean_subtitle_line(text,code),subs_align._clean_line(text))
                    self.assertEqual(T.strip_subtitle_markup(text,code),subs_align._clean_line(text)[0])
                    self.assertEqual(T.normalise_for_match(text,code),' '.join(subs_align.norm(t) for t in text.split() if subs_align.norm(t)))
                    self.assertEqual(''.join(t.original for t in T.tokenize(text,code)),text)
                    self.assertEqual(T.starts_sentence(text,code),bool(text.lstrip()) and text.lstrip()[0].isalpha() and not text.lstrip()[0].islower())
                    self.assertEqual(T.looks_like(text,code)[0],'mismatch' if lang_check.non_latin_share(text)>0.5 else 'compatible')

    def test_pinned_profiles(self):
        cases=json.loads((FIXTURES/'endings.json').read_text(encoding='utf-8'))['cases']
        for c in cases:
            with self.subTest(text=c['text']):
                self.assertEqual(line_tidy._ends_sentence(c['text']),c['tidy'])
                self.assertEqual(subs_align._ends_sentence(c['text']),c['subtitle'])
                self.assertEqual(T.ends_sentence(c['text'],'en',profile='tidy'),c['tidy'])
                self.assertEqual(T.ends_sentence(c['text'],'en',profile='subtitle'),c['subtitle'])
        self.assertTrue(any(c['tidy']!=c['subtitle'] for c in cases))

    def test_regexes_abbreviations(self):
        self.assertEqual(T._TIDY_END.pattern,line_tidy.SENT_END.pattern)
        self.assertEqual(T._SUB_END.pattern,subs_align._HARD_END.pattern)
        self.assertEqual(T._EN_ABBR,frozenset(line_tidy.NOT_END))
        self.assertEqual(T._EN_ABBR,frozenset(subs_align._ABBREVIATIONS))

    def test_speaker_flags_current_cleaning(self):
        for lines in (['- Hello.','- Bye.'],['ALICE: Hello.','BOB: Bye.'],['[music]','— Hello.','— Bye.']):
            spoken=False
            expected=[]
            for i,line in enumerate(lines):
                clean,dash=subs_align._clean_line(line)
                if clean:
                    if dash and spoken:
                        expected.append(i)
                    spoken=True
            self.assertEqual(T.speaker_change_in_cue(lines,'en'),expected)

    def test_new_return_types_lossless_not_timed_row_replacement(self):
        text='I-If Mr. Vale waits... Then go. '
        for profile in ('tidy','subtitle'):
            self.assertEqual(''.join(T.split_sentences(text,'en',profile=profile)),text)
        # No old token-object or string-split return type exists. Preserve old
        # timed-row splitter branches; these invariants are not a parity claim.
        self.assertEqual(T.latin_share('English'),1.0)
        self.assertEqual(T.arabic_script_share('English'),0.0)
