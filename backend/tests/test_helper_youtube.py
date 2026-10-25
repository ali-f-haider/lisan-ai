"""The AI helper and the Help page know about publishing a dub on YouTube, the cards of the finished page and the lines-to-check view."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import assistant_service as a            # noqa: E402

HELP = (ROOT / "help.html").read_text(encoding="utf-8")
PAGE = (ROOT / "dub_long.html").read_text(encoding="utf-8")


def english_help():
    old = a.BASE_DIR, dict(a._kb)
    a.BASE_DIR, a._kb["mtime"], a._kb["text"] = ROOT, None, ""
    try:
        return a.help_text()
    finally:
        a.BASE_DIR = old[0]
        a._kb.update(old[1])


class Helper(unittest.TestCase):
    def test_the_fact_list_explains_the_youtube_card_and_where_the_rest_happens(self):
        for needle in ("PUBLISH ON YOUTUBE", "Write my YouTube texts", "audio-only", "Advanced features", "automatic Arabic dub",
                       "does not post to YouTube", "Languages > the video > Add language > Arabic"):
            self.assertIn(needle, a.FACTS, needle)

    def test_the_fact_list_describes_the_finished_page_and_the_review_view(self):
        for needle in ("THE PAGE OF A FINISHED LONG DUB", "What to know about this dub", "More downloads", "What next?",
                       "Nothing is skipped"):
            self.assertIn(needle, a.FACTS, needle)

    def test_the_helper_reads_the_new_help_cards_in_the_part_that_is_not_cut(self):
        text = english_help()
        for needle in ("Publish your dub on YouTube", "The page of the finished dub", "Review step: lines that need a look are marked",
                       "Nothing is skipped: every line is dubbed"):
            self.assertIn(needle, text, needle)
        self.assertLessEqual(len(a._page_text("help.html", None, 10 ** 7)), 24000)       # the page still fits in what the helper reads

    def test_the_words_the_helper_quotes_are_the_words_on_the_page(self):
        # the names of buttons and cards in the facts must exist in the page the customer sees
        for needle in ("Publish on YouTube", "Write my YouTube texts", "What to know about this dub", "More downloads", "What next?",
                       "Redo this project", "Upload another video", "Show the lines",
                       "Check speaker", "Check the cut", "Check gender"):
            self.assertIn(needle, PAGE, needle)


class HelpPage(unittest.TestCase):
    def test_english_and_arabic_have_the_same_three_new_cards(self):
        en = ("Publish your dub on YouTube", "The page of the finished dub", "Review step: lines that need a look are marked")
        ar = ("انشر دبلجتك على يوتيوب", "صفحة الدبلجة الجاهزة", "خطوة المراجعة: الأسطر التي تحتاج إلى نظرة عليها علامة")
        sec_ar = HELP.index('id="secAr"')
        for t in en:
            self.assertEqual(HELP.count("<h3>" + t + "</h3>"), 1, t)
            self.assertLess(HELP.index("<h3>" + t + "</h3>"), sec_ar)
        for t in ar:
            self.assertEqual(HELP.count("<h3>" + t + "</h3>"), 1, t)
            self.assertGreater(HELP.index("<h3>" + t + "</h3>"), sec_ar)

    def test_the_arabic_cards_use_the_names_the_arabic_page_shows(self):
        for needle in ("انشر على يوتيوب", "اكتب نصوص يوتيوب", "ما يجب معرفته عن هذا الدبلاج", "تنزيلات إضافية", "ماذا بعد؟",
                       "راجع المتحدث", "راجع القطع", "راجع الجنس"):
            self.assertIn(needle, PAGE, needle)
            self.assertIn(needle, HELP, needle)

    def test_cards_are_well_formed(self):
        self.assertEqual(HELP.count("<div"), HELP.count("</div>"))
        for m in re.finditer(r'<div class="card"><h3>(Publish your dub on YouTube|The page of the finished dub|Review step[^<]*)</h3><p>.*?</p></div>', HELP, flags=re.S):
            self.assertNotIn("<div", m.group(0)[10:])


if __name__ == "__main__":
    unittest.main()
