"""Formatting tags never reach the English lines, whatever shape the subtitle file wrote them in. Invented lines only."""
import unittest

import lang_text
import subs_align


class Markup(unittest.TestCase):
    def clean(self, line):
        return subs_align._clean_line(line)[0]

    def test_normal_tags(self):
        self.assertEqual(self.clean('<font color="#ffff00">Hello there.</font>'), "Hello there.")
        self.assertEqual(self.clean("<i>Hello</i> <b>there</b>."), "Hello there.")

    def test_tags_that_were_escaped_once_or_twice(self):
        self.assertEqual(self.clean('&lt;font color=&quot;#ffff00&quot;&gt;Hello.&lt;/font&gt;'), "Hello.")
        self.assertEqual(self.clean('&amp;lt;font color=&amp;quot;#ffff00&amp;quot;&amp;gt;Hello.&amp;lt;/font&amp;gt;'), "Hello.")

    def test_a_tag_cut_off_before_its_closing_bracket(self):
        self.assertEqual(self.clean('Hello there. <font color="'), "Hello there.")
        self.assertEqual(self.clean('<font color="#ffff00"'), "")
        self.assertEqual(self.clean('<FONT COLOR="#fff">Hi.</FONT>'), "Hi.")

    def test_single_quotes_and_words_after_a_cut_off_tag(self):
        self.assertEqual(self.clean("<font color='#ffff00'>Hello.</font>"), "Hello.")
        self.assertEqual(self.clean("<font color='"), "")
        self.assertEqual(self.clean("<font color=' Oh, come on."), "Oh, come on.")
        self.assertEqual(self.clean("#ffff00'>Hello.</font>"), "Hello.")

    def test_the_rest_of_an_attribute_that_began_on_the_line_before(self):
        self.assertEqual(self.clean('#ffff00">Hello.</font>'), "Hello.")

    def test_text_that_only_looks_like_markup_is_kept(self):
        self.assertEqual(self.clean("I <3 you, he said."), "I <3 you, he said.")
        self.assertEqual(self.clean("Plan B is the best."), "Plan B is the best.")
        self.assertEqual(self.clean("Rock & roll."), "Rock & roll.")

    def test_the_other_languages_use_the_same_cleaner(self):
        self.assertEqual(lang_text.clean_subtitle_line('<font color="#ffff00">Hallo.</font>', "de")[0], "Hallo.")
        self.assertEqual(lang_text.clean_subtitle_line('&amp;lt;i&amp;gt;Hallo.&amp;lt;/i&amp;gt;', "de")[0], "Hallo.")

    def test_a_whole_cue_with_a_broken_tag_gives_clean_lines(self):
        cues = subs_align.parse_subtitles('1\n00:00:01,000 --> 00:00:02,000\n<font color="#ffff00">Come out.</font>\n<font color="\n\n2\n00:00:03,000 --> 00:00:04,000\nOkay.\n')
        self.assertEqual([[t for t, _ in c["lines"]] for c in cues], [["Come out."], ["Okay."]])


if __name__ == "__main__":
    unittest.main()
