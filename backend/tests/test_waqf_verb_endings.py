"""Offline checks: stop-form copies must not erase person/gender on final ت."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from arabic_waqf import pausal
from test_shortdub_upgrade import request, segment, source_functions


class VerbEndingSpeechTests(unittest.TestCase):
    def test_reported_feminine_sentence_keeps_its_kasra(self):
        text = 'مَتَى شِئْتِ.'
        self.assertEqual(pausal(text), text)

    def test_three_person_forms_remain_distinct(self):
        forms = ['مَتَى شِئْتَ.', 'مَتَى شِئْتِ.', 'مَتَى شِئْتُ.']
        self.assertEqual([pausal(text) for text in forms], forms)
        self.assertEqual(len(set(pausal(text) for text in forms)), 3)

    def test_general_endings_across_verbs_prefixes_and_modes(self):
        stems = ['شِئْت', 'قُلْت', 'كَتَبْت', 'فَعَلْت', 'رَأَيْت', 'أَرَدْت',
                 'أَحْبَبْت', 'فَهِمْت', 'سَافَرْت', 'اِنْتَهَيْت', 'قَرَأْت',
                 'أَخَذْت', 'أَصْبَحْت', 'نِمْت', 'عَرَفْت']
        for stem in stems:
            for vowel in 'َُِ':
                for prefix in ['', 'وَ', 'فَ']:
                    for mode in ['auto', 'stop', 'join']:
                        text = prefix + stem + vowel + '.'
                        with self.subTest(text=text, mode=mode):
                            self.assertEqual(pausal(text, mode, 1), text)

    def test_punctuation_gaps_internal_stops_and_quotes(self):
        for ending in ['', '.', '؟', '!', '…', '،', '».', '\n']:
            text = 'مَتَى شِئْتِ' + ending
            for mode in ['auto', 'stop', 'join']:
                for gap in [None, 0.1, 1]:
                    with self.subTest(ending=ending, mode=mode, gap=gap):
                        self.assertEqual(pausal(text, mode, gap), text)
        self.assertEqual(pausal('قُلْتِ. كِتَابٌ.'), 'قُلْتِ. كِتَابْ.')

    def test_partial_marks_and_shadda_keep_the_written_vowel(self):
        for text in ['شئتِ.', 'قلتَ.', 'قلتُ.', 'بِتِّ.', 'بِتَّ.', 'بِتُّ.', 'شِئْـتِ.']:
            self.assertEqual(pausal(text), text)

    def test_ambiguous_nouns_keep_short_vowels_as_approved(self):
        for text in ['بِنْتِ.', 'بِنْتَ.', 'بِنْتُ.', 'الْبَيْتِ.', 'صَوْتُ.', 'وَقْتَ.']:
            self.assertEqual(pausal(text), text)

    def test_other_stopping_and_tanween_rules_are_unchanged(self):
        pairs = [('بَيْتٌ.', 'بَيْتْ.'), ('بِنْتٍ.', 'بِنْتْ.'),
                 ('بِنْتًا.', 'بِنْتْ.'), ('كِتَابِ.', 'كِتَابْ.'),
                 ('شُكْرًا.', 'شُكْرَا.'), ('هُوَ كَتَبَ.', 'هُوَ كَتَبْ.'),
                 ('هِيَ قَالَتْ.', 'هِيَ قَالَتْ.'), ('شئت.', 'شئت.')]
        for original, expected in pairs:
            self.assertEqual(pausal(original), expected)

    def test_endings_are_idempotent_and_no_vowel_is_invented(self):
        for text in ['شِئْتِ.', 'قُلْتَ.', 'فَهِمْتُ.', 'بِنْتِ.', 'شئت.', 'شِئْتْ.']:
            once = pausal(text)
            self.assertEqual(pausal(once), once)
        self.assertEqual(pausal('شئت.'), 'شئت.')

    def test_long_dub_sends_preserved_text_without_changing_the_row(self):
        synthesize = Mock(return_value=b'audio')
        env = source_functions('longdub_service.py', ['_voice_text', '_tts_with_retry'], {
            'inworld_service': NS(synthesize=synthesize),
            'INWORLD_API_KEY': 'offline-test',
        })
        row = {'arabic_text': 'مَتَى شِئْتِ.'}
        with patch.dict('sys.modules', {'inworld_service': NS(synthesize=synthesize)}):
            audio, error = env['_tts_with_retry']('voice', '[Calm]' + row['arabic_text'], 'auto', 1)
        self.assertEqual(error, '')
        self.assertEqual(audio, b'audio')
        synthesize.assert_called_once_with('voice', '[Calm]مَتَى شِئْتِ.', 'offline-test', language='ar')
        self.assertEqual(row, {'arabic_text': 'مَتَى شِئْتِ.'})

    def test_short_regeneration_sends_preserved_text_to_both_engines(self):
        for engine in ['inworld', 'elevenlabs']:
            with self.subTest(engine=engine):
                synthesize = Mock(return_value=b'audio')
                convert = Mock(return_value=b'audio')
                env = source_functions('eleven_service.py', ['regenerate_line'], {
                    'eleven_client': NS(text_to_speech=NS(convert=convert)),
                    'inworld_service': NS(instruction_tag=lambda _: '[Calm]', synthesize=synthesize),
                    'usage_bucket': lambda _: {'inworld_chars': 0, 'eleven_chars': 0},
                    '_emotion_tags': lambda _: '[Calm]',
                    'TTS_MODEL_ID': 'offline-test',
                    'friendly_error': str,
                    'OUTPUT_DIR': '',
                    'line_audio_path': Mock(side_effect=RuntimeError('Stop after captured request')),
                })
                line = segment(text='مَتَى شِئْتِ.', waqf='auto')
                req = request(segment=line, segments=[line], voice_engine=engine)
                env['regenerate_line'](req)
                if engine == 'inworld':
                    self.assertEqual(synthesize.call_args.args[1], '[Calm]مَتَى شِئْتِ.')
                    convert.assert_not_called()
                else:
                    self.assertEqual(convert.call_args.kwargs['text'], '[Calm] مَتَى شِئْتِ.')
                    synthesize.assert_not_called()
                self.assertEqual(line.arabic_text, 'مَتَى شِئْتِ.')


if __name__ == '__main__':
    unittest.main()
