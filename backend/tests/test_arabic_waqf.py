"""Pausal form for the voice: the last word before a real stop is spoken with a sukoon / the alef of the tanween fatha; the written text never changes."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arabic_waqf import pausal


class PausalTests(unittest.TestCase):
    def test_short_vowel_becomes_sukoon(self):
        self.assertEqual(pausal("هَذَا كِتَابُهُ."), "هَذَا كِتَابُهْ.")
        self.assertEqual(pausal("ذَهَبَ مُحَمَّدٌ."), "ذَهَبَ مُحَمَّدْ.")
        self.assertEqual(pausal("أَنَا فِي الْبَيْتِ."), "أَنَا فِي الْبَيْتْ.")
        self.assertEqual(pausal("لَمْ يَصِلِ."), "لَمْ يَصِلْ.")
        self.assertEqual(pausal("هُوَ كَتَبَ."), "هُوَ كَتَبْ.")

    def test_tanween_damma_and_kasra(self):
        self.assertEqual(pausal("هَذَا كِتَابٌ."), "هَذَا كِتَابْ.")
        self.assertEqual(pausal("مَرَرْتُ بِصَدِيقٍ."), "مَرَرْتُ بِصَدِيقْ.")

    def test_tanween_fatha_of_special_words_keeps_the_spoken_alef(self):
        self.assertEqual(pausal("شُكْرًا."), "شُكْرَا.")
        self.assertEqual(pausal("شُكْراً."), "شُكْرَا.")
        self.assertEqual(pausal("أَهْلًا."), "أَهْلَا.")
        self.assertEqual(pausal("جِدًّا."), "\u062C\u0650\u062F\u0651\u064E\u0627.")        # shadda, then fatha
        self.assertFalse(pausal("\u0634\u064F\u0643\u0652\u0631\u064B\u0627.").endswith("\u0631\u0652."))                     # never "\u0634\u064F\u0643\u0652\u0631\u0652"
        self.assertEqual(pausal("مَسَاءً."), "مَسَاءَا.")                   # special word, no alef is written, it is still spoken

    def test_tanween_fatha_of_any_other_word_is_a_sukoon_without_alef(self):
        self.assertEqual(pausal("رَأَيْتُ كِتَابًا."), "رَأَيْتُ كِتَابْ.")
        self.assertEqual(pausal("رَأَيْتُ كِتَاباً."), "رَأَيْتُ كِتَابْ.")
        self.assertEqual(pausal("شَرِبْتُ مَاءً."), "شَرِبْتُ مَاءْ.")
        self.assertEqual(pausal("ظَنًّا."), "ظَنّ.")                         # a shadda stays alone
        self.assertEqual(pausal("كِتَابًا", gap=0.1), "كِتَابًا")           # not at a stop: nothing changes

    def test_alef_maqsura_words_keep_their_long_vowel(self):
        self.assertEqual(pausal("فَتًى."), "فَتَى.")
        self.assertEqual(pausal("هُدًى."), "هُدَى.")

    def test_ta_marbuta_is_a_silent_ha(self):
        self.assertEqual(pausal("مَدْرَسَةٌ."), "مَدْرَسَهْ.")
        self.assertEqual(pausal("مَدْرَسَةً."), "مَدْرَسَهْ.")
        self.assertEqual(pausal("جَمِيلَةٍ."), "جَمِيلَهْ.")
        self.assertEqual(pausal("رَحْمَةً."), "رَحْمَهْ.")

    def test_open_ta_stays_a_ta(self):
        self.assertEqual(pausal("بَيْتٌ."), "بَيْتْ.")

    def test_shadda_is_kept(self):
        self.assertEqual(pausal("هَذَا حَقٌّ."), "هَذَا حَقّ.")
        self.assertEqual(pausal("هَذَا رَبِّ."), "هَذَا رَبّ.")

    def test_long_vowels_and_defective_nouns_are_not_touched(self):
        for s in ("هُمْ.", "مَعَهُمْ.", "ذَهَبُوا.", "لَهَا.", "عَلَى.", "هُنَا.", "فِي.", "يَدْعُو."):
            self.assertEqual(pausal(s), s)
        self.assertEqual(pausal("هَذَا قَاضٍ."), "هَذَا قَاضٍ.")
        self.assertEqual(pausal("هَذَا دَاعٍ."), "هَذَا دَاعٍ.")
        self.assertEqual(pausal("مُعَلِّمُونَ."), "مُعَلِّمُونْ.")          # a real nun is never removed

    def test_only_the_last_word_before_each_stop(self):
        self.assertEqual(pausal("نَعَمُ يَا سَيِّدِي، لَقَدْ فَهِمْتُ. هَيَّا بِنَا!"), "نَعَمُ يَا سَيِّدِي، لَقَدْ فَهِمْتْ. هَيَّا بِنَا!")
        self.assertEqual(pausal("كَيْفَ حَالُكَ؟"), "كَيْفَ حَالُكْ؟")

    def test_idempotent_and_unmarked_text_untouched(self):
        for t in ("قَالَ الرَّجُلُ لِي. ذَهَبَ مُحَمَّدٌ.", "شُكْرًا. مَاءً. مَدْرَسَةً. فَتًى. جِدًّا. كِتَابًا. رَأَيْتُ امْرَأَةً."):
            once = pausal(t)
            self.assertEqual(pausal(once), once)
        self.assertEqual(pausal("ذهب محمد إلى البيت."), "ذهب محمد إلى البيت.")
        self.assertEqual(pausal(""), "")
        self.assertEqual(pausal(None), None)

    def test_closing_quote_and_latin_untouched(self):
        self.assertEqual(pausal("قَالَ: «نَعَمْ يَا أَبِي»."), "قَالَ: «نَعَمْ يَا أَبِي».")
        self.assertEqual(pausal("[sad, softly] أَنَا هُنَا."), "[sad, softly] أَنَا هُنَا.")
        self.assertEqual(pausal("قَالَ «شُكْرًا»."), "قَالَ «شُكْرَا».")


class WhereTheStopIs(unittest.TestCase):
    def test_comma_colon_semicolon_are_not_stops_in_auto(self):
        self.assertEqual(pausal("قَالَ لَكَ، تَعَالَ."), "قَالَ لَكَ، تَعَالْ.")
        self.assertEqual(pausal("قَالَ: نَعَمُ؛ ذَهَبَ."), "قَالَ: نَعَمُ؛ ذَهَبْ.")

    def test_stop_mode_makes_every_mark_a_stop(self):
        self.assertEqual(pausal("قَالَ لَكَ، تَعَالَ", mode="stop"), "قَالَ لَكْ، تَعَالْ")
        self.assertEqual(pausal("قَالَ: «نَعَمْ يَا أَبِي»", mode="stop"), "قَالْ: «نَعَمْ يَا أَبِي»")

    def test_end_of_line_without_punctuation_is_joined_unless_the_speaker_paused(self):
        self.assertEqual(pausal("ذَهَبَ مُحَمَّدٌ"), "ذَهَبَ مُحَمَّدٌ")                       # the sentence goes on in the next line
        self.assertEqual(pausal("ذَهَبَ مُحَمَّدٌ", gap=0.2), "ذَهَبَ مُحَمَّدٌ")
        self.assertEqual(pausal("ذَهَبَ مُحَمَّدٌ", gap=0.8), "ذَهَبَ مُحَمَّدْ")
        self.assertEqual(pausal("ذَهَبَ مُحَمَّدٌ", mode="stop"), "ذَهَبَ مُحَمَّدْ")

    def test_join_mode_keeps_the_end_of_the_line(self):
        self.assertEqual(pausal("ذَهَبَ مُحَمَّدٌ.", mode="join"), "ذَهَبَ مُحَمَّدٌ.")
        self.assertEqual(pausal("ذَهَبَ مُحَمَّدٌ", mode="join", gap=5), "ذَهَبَ مُحَمَّدٌ")
        self.assertEqual(pausal("قَالَ لَهُ. ذَهَبَ مُحَمَّدٌ.", mode="join"), "قَالَ لَهْ. ذَهَبَ مُحَمَّدٌ.")      # inside the line a full stop is still a stop

    def test_a_dot_inside_a_link_or_an_abbreviation_is_not_a_stop(self):
        self.assertEqual(pausal("زُرْ مَوْقِعَنَا www.example.com الْآنَ"), "زُرْ مَوْقِعَنَا www.example.com الْآنَ")
        self.assertEqual(pausal("قَالَ الدُّكْتُورُ. مُحَمَّدٌ"), "قَالَ الدُّكْتُورْ. مُحَمَّدٌ")

    def test_unknown_mode_is_auto(self):
        self.assertEqual(pausal("ذَهَبَ مُحَمَّدٌ.", mode="nonsense"), "ذَهَبَ مُحَمَّدْ.")


class TheTextIsNeverChanged(unittest.TestCase):
    def test_letters_change_only_by_ta_marbuta_and_added_alef(self):
        strip = lambda s: re.sub("[ً-ٰٕ]", "", s)
        t = "هَذَا كِتَابُهُ. شُكْرًا لَكَ. مَسَاءً. فَتًى. جِدًّا."
        self.assertEqual(strip(pausal(t)).replace("ا.", "."), strip(t).replace("ا.", "."))
        # a word that loses its alef (كِتَابًا -> كِتَابْ) and the two silent-ة words are the only other letter changes
        self.assertEqual(strip(pausal("رَأَيْتُ كِتَابًا.")), "رأيت كتاب.")
        self.assertEqual(strip(pausal("هَذِهِ امْرَأَةٌ.")), "هذه امرأه.")

    def test_the_stored_text_stays_as_the_ai_wrote_it(self):
        import json
        import gemini_service as g
        saved = (g.call_gemini, g.record_gemini)
        g.record_gemini = lambda *a, **k: None
        try:
            items = [{"segment_id": "a", "arabic_text": "شُكْرًا لَكَ يَا سَيِّدِي، هَذَا كِتَابُهُ.", "emotion": "happy, softly"}]
            g.call_gemini = lambda *a, **k: ({"candidates": [{"content": {"parts": [{"text": json.dumps(items, ensure_ascii=False)}]}}]}, None)

            class Seg:
                segment_id, start, end, text, speaker = "a", 0.0, 3.0, "x", "s"
            r = g.translate_segments("j", [Seg()], "key")
            self.assertEqual(r["translated_segments"][0]["arabic_text"], items[0]["arabic_text"])
            items2 = [{"segment_id": "a", "arabic_text": "هَذَا كِتَابُهُ"}]
            g.call_gemini = lambda *a, **k: ({"candidates": [{"content": {"parts": [{"text": json.dumps(items2, ensure_ascii=False)}]}}]}, None)
            self.assertEqual(g.add_tashkeel_lines("j", [{"segment_id": "a", "arabic_text": "هذا كتابه"}], "key"), {"a": "هَذَا كِتَابُهُ"})
        finally:
            g.call_gemini, g.record_gemini = saved


class TheVoiceGetsTheStopForm(unittest.TestCase):
    def test_long_dub_voice_call(self):
        import inworld_service
        import longdub_service as ld
        sent = []
        saved = inworld_service.synthesize
        inworld_service.synthesize = lambda voice, text, key, language="ar": sent.append(text) or b"audio"
        try:
            ld._tts_with_retry("v", "[Happy]شُكْرًا لَكَ، هَذَا كِتَابُهُ.")
            ld._tts_with_retry("v", "هَذَا كِتَابُهُ", "auto", 0.1)
            ld._tts_with_retry("v", "هَذَا كِتَابُهُ", "auto", 0.9)
            ld._tts_with_retry("v", "هَذَا كِتَابُهُ.", "join", 0.9)
        finally:
            inworld_service.synthesize = saved
        self.assertEqual(sent, ["[Happy]شُكْرًا لَكَ، هَذَا كِتَابُهْ.", "هَذَا كِتَابُهُ", "هَذَا كِتَابُهْ", "هَذَا كِتَابُهُ."])

    def test_edit_option_is_cleaned(self):
        import arabic_waqf
        self.assertEqual([arabic_waqf.clean_mode(x) for x in ("stop", "JOIN", " auto ", "x", None)], ["stop", "join", "auto", "auto", "auto"])
        self.assertAlmostEqual(arabic_waqf.gap_after(5.0, 5.4), 0.4)
        self.assertEqual(arabic_waqf.gap_after(5.0, None), 99.0)


# Words and expressions that end in a tanween fatha (the user's protection list, by use). The written tanween stays in the stored text; the voice copy
# says the alef at a stop ("شُكْرَا") and never a sukoon ("شُكْرْ"). Entries are written without marks; the test puts the marks on.
PROTECTED = """
شكرا أهلا سهلا مرحبا عفوا عذرا وداعا حسنا مهلا صبرا رفقا عجبا تبا
جدا كثيرا قليلا جزيلا تماما كليا جزئيا تقريبا نسبيا
حقا فعلا صدقا يقينا قطعا حتما طبعا
دائما أبدا أحيانا غالبا نادرا مرارا تكرارا مجددا
غدا صباحا ليلا نهارا قريبا لاحقا سابقا حاليا حديثا قديما أخيرا أولا ثانيا ثالثا رابعا
سريعا بطيئا فورا عاجلا تدريجيا تلقائيا عمدا سهوا سرا جهرا علنا
معا جميعا سويا منفردا منفصلا متصلا تباعا
أيضا بعيدا يمينا يسارا أماما خلفا جانبا
عموما خصوصا تحديدا أساسا أصلا إجمالا
عمليا نظريا علميا منطقيا واقعيا رسميا شخصيا قانونيا اجتماعيا اقتصاديا سياسيا تقنيا لغويا
شيئا يوما
""".split()
SHADDA_WORDS = set("جدا تبا حقا كليا جزئيا نسبيا سويا تدريجيا تلقائيا عمليا نظريا علميا منطقيا واقعيا رسميا شخصيا قانونيا اجتماعيا اقتصاديا سياسيا تقنيا لغويا حاليا".split())
TA_MARBUTA = "عادة فجأة صراحة حقيقة مباشرة خاصة".split()
TAN, FATHA, SHADDA, SUKOON = "\u064B", "\u064E", "\u0651", "\u0652"


def with_tanween(bare, style):
    """The word as an AI writes it: the tanween on the letter before the alef ("شُكْرًا", style 'before') or on the alef itself ("شُكْراً", style 'on')."""
    body, alef = bare[:-1], bare[-1]
    assert alef == "ا"
    mark = (SHADDA + TAN) if bare in SHADDA_WORDS else TAN
    if bare in SHADDA_WORDS and bare.endswith("يا"):
        body = body                                       # the ي carries both marks
    return body + mark + alef if style == "before" else body + (SHADDA if bare in SHADDA_WORDS else "") + alef + TAN


class ProtectedWordsTests(unittest.TestCase):
    def test_every_listed_word_ends_with_the_spoken_alef_in_both_spellings(self):
        for bare in PROTECTED:
            for style in ("before", "on"):
                word = with_tanween(bare, style)
                for text in (word + ".", "قَالَ لَهُ " + word + "؟"):
                    out = pausal(text)
                    core = out.rstrip(".؟")
                    self.assertNotIn(TAN, core, (bare, style, out))
                    self.assertTrue(core.endswith(FATHA + "ا"), (bare, style, out))
                    self.assertNotIn(SUKOON + "ا", core, (bare, style, out))
                    self.assertFalse(core.endswith(SUKOON), (bare, style, out))
                    strip = lambda t: re.sub("[ً-ٰٕ]", "", t)
                    self.assertEqual(strip(out), strip(text), (bare, style))      # no letter was added or lost
                    self.assertEqual(pausal(out), out, (bare, style))             # idempotent

    def test_ta_marbuta_adverbs_end_with_a_silent_ha(self):
        for bare in TA_MARBUTA:
            word = bare[:-1] + "\u064E" + "ة" + TAN
            out = pausal(word + ".")
            self.assertEqual(out, bare[:-1] + "\u064E" + "ه" + SUKOON + ".", bare)

    def test_the_hamza_noon_word_gets_its_alef(self):
        self.assertEqual(pausal("مَسَاءً."), "مَسَاءَا.")

    def test_fixed_expressions_join_their_words(self):
        # only the last word of the expression is at the stop; the words inside flow into each other
        self.assertEqual(pausal("شُكْرًا جَزِيلًا."), "شُكْرًا جَزِيلَا.")
        self.assertEqual(pausal("وَشُكْرًا."), "وَشُكْرَا.")                       # a leading و is ignored
        self.assertEqual(pausal("رَسْمِيًّا."), "\u0631\u064E\u0633\u0652\u0645\u0650\u064A\u0651\u064E\u0627.")   # adverbs in -يًّا are special too
        self.assertEqual(pausal("أَهْلًا وَسَهْلًا."), "أَهْلًا وَسَهْلَا.")
        self.assertEqual(pausal("قَرِيبًا جِدًّا."), "قَرِيبًا \u062C\u0650\u062F\u0651\u064E\u0627.")
        self.assertEqual(pausal("أَهْلًا وَسَهْلًا", gap=0.1), "أَهْلًا وَسَهْلًا")          # no stop, no change
        self.assertEqual(pausal("ثَانِيًا، سَأَعُودُ غَدًا."), "ثَانِيًا، سَأَعُودُ غَدَا.")  # a comma is no stop in auto

    def test_words_without_a_tanween_are_never_given_one(self):
        for s in ("بِالتَّأْكِيدِ.", "كَذَلِكَ.", "لِلْأَسَفِ."):
            self.assertNotIn(TAN, pausal(s))
        self.assertEqual(pausal("شكرا."), "شكرا.")                                          # unmarked text stays as it is
        self.assertEqual(pausal("هَذَا كَثِيرٌ."), "هَذَا كَثِيرْ.")                        # the same word with a damma is not the adverb


class TheTwoSilentTaWords(unittest.TestCase):
    """امرأة and لافتة: the last ة is not spoken, wherever the word is in the line; the stored text keeps it."""

    def test_marked_and_unmarked_text(self):
        self.assertEqual(pausal("هَذِهِ امْرَأَةٌ جَمِيلَةٌ"), "هَذِهِ امْرَأَهْ جَمِيلَةٌ")
        self.assertEqual(pausal("رَأَيْتُ لَافِتَةً كَبِيرَةً"), "رَأَيْتُ لَافِتَهْ كَبِيرَةً")
        self.assertEqual(pausal("هذه امرأة جميلة"), "هذه امرأه جميلة")
        self.assertEqual(pausal("لافتة"), "لافته")
        self.assertEqual(pausal("امرأة."), "امرأه.")

    def test_prefixes_and_the_article(self):
        self.assertEqual(pausal("قالت المرأة."), "قالت المرأه.")
        self.assertEqual(pausal("بِالمَرْأَةِ."), "بِالمَرْأَهْ.")
        self.assertEqual(pausal("للمرأة"), "للمرأه")
        self.assertEqual(pausal("وَلَافِتَةٌ كَبِيرَةٌ"), "وَلَافِتَهْ كَبِيرَةٌ")

    def test_the_same_in_every_mode_and_with_a_gap(self):
        for mode in ("auto", "stop", "join"):
            self.assertEqual(pausal("هذه امرأة جميلة", mode=mode, gap=0.0), "هذه امرأه جميلة")

    def test_idafa_keeps_the_ta(self):
        self.assertEqual(pausal("هذه امرأة الرجل"), "هذه امرأة الرجل")
        self.assertEqual(pausal("لَافِتَةُ الطَّرِيقِ"), "لَافِتَةُ الطَّرِيقِ")

    def test_other_words_and_forms_are_not_touched(self):
        self.assertEqual(pausal("امرأتي هنا"), "امرأتي هنا")                      # ة is a ت inside the word
        self.assertEqual(pausal("شجرة كبيرة"), "شجرة كبيرة")                     # only the two words
        self.assertEqual(pausal("المرأتان"), "المرأتان")

    def test_idempotent(self):
        for t in ("هذه امرأة جميلة", "رَأَيْتُ لَافِتَةً.", "للمرأة"):
            self.assertEqual(pausal(pausal(t)), pausal(t))


class TheSpecialWordList(unittest.TestCase):
    def test_extra_words_file_and_the_switch(self):
        import arabic_waqf as aw
        self.assertEqual(pausal("رَأَيْتُ كِتَابًا."), "رَأَيْتُ كِتَابْ.")
        aw.ORDINARY_TANWEEN_FATHA = "alef"
        try:
            self.assertEqual(pausal("رَأَيْتُ كِتَابًا."), "رَأَيْتُ كِتَابَا.")
            self.assertEqual(pausal("مَاءً."), "مَاءَا.")
        finally:
            aw.ORDINARY_TANWEEN_FATHA = "sukoon"
        saved = list(aw._special_cache)
        aw._special_cache[:] = [set(aw._special_words()) | {aw._norm("كتابا")}]
        try:
            self.assertEqual(pausal("رَأَيْتُ كِتَابًا."), "رَأَيْتُ كِتَابَا.")
        finally:
            aw._special_cache[:] = saved


if __name__ == "__main__":
    unittest.main()
