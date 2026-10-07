"""Pausal form: the last word before a pause ends with a sukoon (or nothing), never with a fatha, kasra or damma."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arabic_waqf import pausal


class PausalTests(unittest.TestCase):
    def test_short_vowel_becomes_sukoon(self):
        self.assertEqual(pausal("هَذَا كِتَابُهُ."), "هَذَا كِتَابُهْ.")
        self.assertEqual(pausal("ذَهَبَ مُحَمَّدٌ"), "ذَهَبَ مُحَمَّدْ")
        self.assertEqual(pausal("أَنَا فِي الْبَيْتِ"), "أَنَا فِي الْبَيْتْ")
        self.assertEqual(pausal("قَالَ لَكَ، تَعَالَ"), "قَالَ لَكْ، تَعَالْ")

    def test_only_the_last_word_before_each_pause(self):
        self.assertEqual(pausal("نَعَمُ يَا سَيِّدِي، لَقَدْ فَهِمْتُ. هَيَّا بِنَا!"),
                         "نَعَمُ يَا سَيِّدِي، لَقَدْ فَهِمْتْ. هَيَّا بِنَا!")
        self.assertEqual(pausal("كَيْفَ حَالُكَ؟"), "كَيْفَ حَالُكْ؟")

    def test_tanween(self):
        self.assertEqual(pausal("شُكْرًا."), "شُكْرًا.")
        self.assertEqual(pausal("جِدًّا"), "جِدًّا")
        self.assertEqual(pausal("رَحْمَةً"), "رَحْمَةً")
        self.assertEqual(pausal("فَتًى"), "فَتًى")
        self.assertEqual(pausal("كِتَابٍ"), "كِتَابْ")

    def test_shadda_stays_alone(self):
        self.assertEqual(pausal("هَذَا رَبِّ."), "هَذَا رَبّ.")

    def test_already_pausal_and_long_vowels_are_unchanged(self):
        for s in ("هُمْ", "مَعَهُمْ.", "ذَهَبُوا.", "لَهَا", "عَلَى"):
            self.assertEqual(pausal(s), s)
        self.assertEqual(pausal("أَنْتُمْ هُنَا."), "أَنْتُمْ هُنَا.")
        self.assertEqual(pausal("خَرَجُوا مِنْ هُنَاكَ، وَعَلَى مَهْلٍ"), "خَرَجُوا مِنْ هُنَاكْ، وَعَلَى مَهْلْ")
        self.assertEqual(pausal("نَحْنُ فِي"), "نَحْنُ فِي")

    def test_idempotent_and_unmarked_text_untouched(self):
        t = "قَالَ الرَّجُلُ لِي: ذَهَبَ مُحَمَّدٌ."
        once = pausal(t)
        self.assertEqual(pausal(once), once)
        self.assertEqual(pausal("ذهب محمد إلى البيت."), "ذهب محمد إلى البيت.")
        self.assertEqual(pausal(""), "")
        self.assertEqual(pausal(None), None)

    def test_closing_quote_and_latin_untouched(self):
        self.assertEqual(pausal("قَالَ: «نَعَمْ يَا أَبِي»."), "قَالْ: «نَعَمْ يَا أَبِي».")        # a colon is a pause too; a long ي stays
        self.assertEqual(pausal("[sad, softly] أَنَا هُنَا"), "[sad, softly] أَنَا هُنَا")

    def test_letters_never_change(self):
        import re
        t = "هَذَا كِتَابُهُ، وَرَحْمَةً بِنَا. شُكْرًا لَكَ"
        strip = lambda s: re.sub("[ً-ٰٕ]", "", s)
        self.assertEqual(strip(pausal(t)), strip(t))


class WiringTests(unittest.TestCase):
    """The translation and the Tashkeel answers of the AI are put in the pausal form before anybody sees them."""
    def setUp(self):
        import json
        import gemini_service as g
        self.g = g
        self.saved = (g.call_gemini, g.record_gemini)
        g.record_gemini = lambda *a, **k: None
        self.json = json

    def tearDown(self):
        self.g.call_gemini, self.g.record_gemini = self.saved

    def answer(self, items):
        text = self.json.dumps(items, ensure_ascii=False)
        self.g.call_gemini = lambda *a, **k: ({"candidates": [{"content": {"parts": [{"text": text}]}}]}, None)

    def test_translation_ends_with_a_sukoon(self):
        self.answer([{"segment_id": "a", "arabic_text": "شُكْرًا لَكَ يَا سَيِّدِي، هَذَا كِتَابُهُ.", "emotion": "happy, softly"}])

        class Seg:
            segment_id, start, end, text, speaker = "a", 0.0, 3.0, "x", "s"
        r = self.g.translate_segments("j", [Seg()], "key")
        self.assertEqual(r["translated_segments"][0]["arabic_text"], "شُكْرًا لَكَ يَا سَيِّدِي، هَذَا كِتَابُهْ.")

    def test_tashkeel_answer_ends_with_a_sukoon(self):
        self.answer([{"segment_id": "a", "arabic_text": "هَذَا كِتَابُهُ"}])
        self.assertEqual(self.g.add_tashkeel_lines("j", [{"segment_id": "a", "arabic_text": "هذا كتابه"}], "key"), {"a": "هَذَا كِتَابُهْ"})


if __name__ == "__main__":
    unittest.main()


class NoShortVowelAtTheEnd(unittest.TestCase):
    def test_no_fatha_kasra_damma_or_tanween_ends_a_phrase(self):
        import re
        text = "شُكْرًا. هَذَا كِتَابٌ جَدِيدٌ، وَقَدْ أَخَذْتُهُ مِنْهُ؟ نَعَمْ يَا سَيِّدِي! رَحْمَةً بِنَا… فَتًى"
        out = pausal(text)
        for m in re.finditer("([\u0621-\u064A][\u064B-\u0655\u0670]*)(?=[.،,؛;:!؟?…]|\\s*$)", out):
            self.assertFalse(re.search("[\u064C-\u0650]", m.group(1)), (m.group(1), out))      # a tanween fatha (ـًا) is the one exception
