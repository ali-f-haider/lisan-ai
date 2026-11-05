"""The guides (guides.py, Nov 2026): public articles in English and Arabic, listed in the sitemap and linked from the home page."""
import ast
import re
import unittest
from pathlib import Path

import guides

ROOT = Path(__file__).resolve().parents[1]
VENDORS = ("inworld", "gemini", "elevenlabs", "eleven labs", "openai", "whisper", "alibaba", "wan 3", "anthropic", "claude")


class Pages(unittest.TestCase):
    def test_every_guide_has_an_english_and_an_arabic_part_and_a_page(self):
        for g in guides.GUIDES:
            text = (guides.FOLDER / (g["slug"] + ".html")).read_text(encoding="utf-8")
            self.assertEqual(text.count('<div class="en">'), 1, g["slug"])
            self.assertEqual(text.count('<div class="ar">'), 1, g["slug"])
            self.assertRegex(text.split('<div class="ar">')[1], "[ء-ي]{20}|[ء-ي]+ [ء-ي]+")
            page = guides.guide_html(g["slug"])
            for needle in ('<link rel="canonical" href="https://lisanai.org/guides/' + g["slug"] + '">', '"@type": "Article"',
                           '<meta name="description"', '/site.js', g["en"]):
                self.assertIn(needle, page, g["slug"])
        self.assertIsNone(guides.guide_html("nope"))
        self.assertIsNone(guides.guide_html("../main"))

    def test_customer_pages_never_name_a_provider(self):
        for g in guides.GUIDES:
            low = guides.guide_html(g["slug"]).lower()
            for v in VENDORS:
                self.assertIsNone(re.search(r"\b" + re.escape(v) + r"\b", low), (g["slug"], v))
        low = guides.index_html().lower()
        for v in VENDORS:
            self.assertIsNone(re.search(r"\b" + re.escape(v) + r"\b", low), v)

    def test_the_list_links_every_guide(self):
        page = guides.index_html()
        for g in guides.GUIDES:
            self.assertIn('href="/guides/' + g["slug"] + '"', page)
        self.assertEqual(guides.paths()[0], "/guides")


class Wiring(unittest.TestCase):
    def test_public_in_the_sitemap_and_on_the_home_page(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('if path == "/guides" or path.startswith("/guides/"):', main)
        self.assertIn("+ guides.paths()", main)
        self.assertIn('@app.get("/guides/{slug}")', main)
        self.assertIn('href="/guides"', (ROOT / "landing.html").read_text(encoding="utf-8"))
        robots = re.search(r'def robots_txt\(\):(.*?)return PlainTextResponse', main, re.S).group(1)
        self.assertNotIn("/guides", robots)


if __name__ == "__main__":
    unittest.main()
