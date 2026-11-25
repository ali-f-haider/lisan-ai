"""Packs and plans say "about N minutes of long dub" (1.107.0)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PackMinutesTests(unittest.TestCase):
    def test_the_server_sends_the_rate_from_the_long_dub_estimate(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"longDubPerMin": _long_dub_per_min(),', main)
        self.assertIn("longdub_service.compute_estimate(LONG_DUB_EXAMPLE_SEC, _ld_pricing()", main)

    def test_every_page_shows_it_with_the_note(self):
        for name, key in (("pricing.html", "ldMinLine(credits)"), ("landing.html", "ldLi(ul, cr);"), ("app.js", "ldMin(p.credits)")):
            page = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("longDubPerMin", page, name)
            self.assertIn(key, page, name)
            self.assertIn("10-minute video with 2 speakers", page, name)
            self.assertIn("من الدبلجة الطويلة*", page, name)


if __name__ == "__main__":
    unittest.main()
