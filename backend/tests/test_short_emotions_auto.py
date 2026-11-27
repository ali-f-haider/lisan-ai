"""Short dub: the emotion check starts by itself after the automatic translation; small moving logo at the progress bars (1.110.1)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class WiringTests(unittest.TestCase):
    def test_auto_start_after_translation(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        for key in ("async function detectEmotions(auto)", "var started = await detectEmotions(true);",
                    "window._emotionsRun = true;", "🔄 Detect emotions again", "اكتشف المشاعر من جديد",
                    "clearInterval(emotionPollTimer); emotionPollTimer = null;", "Emotions matched to the original voices: "):
            self.assertIn(key, app)
        # the automatic start comes after the translation, and only when it worked
        i, j = app.index("var ok = await translateLines(unlocked, true);"), app.index("var started = await detectEmotions(true);")
        self.assertLess(i, j)
        self.assertIn("else if (currentJobId === job", app[i:j])
        # the last definition of detectEmotions is the one that takes "auto" (function declarations: the last one wins)
        self.assertGreater(app.rindex("async function detectEmotions(auto)"), app.rindex("async function detectEmotions()"))

    def test_page(self):
        idx = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="btnEmotions" data-i18n="none" onclick="detectEmotions()"><span id="btnEmotionsLabel" data-i18n="none">', idx)
        for fill in ('id="progressFill"', 'id="emotionProgressFill"', 'id="genProgressFill"'):
            start = idx.index(fill)
            self.assertIn('class="mini-logo"', idx[idx.rindex('<div class="mini-progress">', 0, start):start])
        self.assertIn(".mini-logo {", (ROOT / "styles.css").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
