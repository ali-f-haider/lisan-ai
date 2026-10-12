"""The speech-only stop form must keep أنتَ / أنتِ distinguishable."""
import unittest
from arabic_waqf import pausal
from test_shortdub_upgrade import source_functions


class PronounSpeechTests(unittest.TestCase):
    def test_both_genders_and_conjunctions_survive_every_end_mode(self):
        for prefix in ['', 'وَ', 'فَ']:
            for pronoun in ['أَنْتِ', 'أَنْتَ']:
                for ending in ['', '.', '?', '؟', '!', '،']:
                    for mode in ['auto', 'stop', 'join']:
                        text = prefix + pronoun + ending
                        with self.subTest(text=text, mode=mode):
                            self.assertEqual(pausal(text, mode, 1), text)

    def test_feminine_and_masculine_speech_copies_remain_different(self):
        self.assertNotEqual(pausal('وَأَنْتِ؟'), pausal('وَأَنْتَ؟'))

    def test_pronoun_at_internal_sentence_stop_keeps_its_vowel(self):
        for pronoun in ['أَنْتِ', 'أَنْتَ']:
            text = 'هَلْ ' + pronoun + '؟ كِتَابُهُ.'
            self.assertEqual(pausal(text), 'هَلْ ' + pronoun + '؟ كِتَابُهْ.')

    def test_ordinary_words_keep_the_existing_stop_form(self):
        self.assertEqual(pausal('كِتَابِ.'), 'كِتَابْ.')
        self.assertEqual(pausal('كِتَابٌ.'), 'كِتَابْ.')
        self.assertEqual(pausal('شُكْرًا.'), 'شُكْرَا.')

    def test_missing_gender_vowels_are_not_invented(self):
        self.assertEqual(pausal('وأنت؟'), 'وأنت؟')

    def test_preservation_is_idempotent(self):
        for text in ['أَنْتِ.', 'وَأَنْتَ؟', 'فَأَنْتِ']:
            self.assertEqual(pausal(pausal(text, 'stop', 1), 'stop', 1), text)

    def test_actual_long_dub_speech_wrapper_keeps_editor_text_and_gender(self):
        env = source_functions('longdub_service.py', ['_voice_text'], {})
        row = {'arabic_text': 'وَأَنْتِ؟'}
        self.assertEqual(env['_voice_text'](row['arabic_text'], 'auto', 1), 'وَأَنْتِ؟')
        self.assertEqual(row, {'arabic_text': 'وَأَنْتِ؟'})


if __name__ == '__main__': unittest.main()
