"""Short dub: automatic translation, Translate again, hand-over to Long dub, free-credits note (1.109.0)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class WiringTests(unittest.TestCase):
    def test_app(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        for key in ("window.translateButton = function", "Translate the new lines", "🔄 Translate again", "translateLines(unlocked, true)",
                    "window.lisanHandOffToLongDub", 'location.href = "/dub-long?from=short"', "longDub: tooLong",
                    "if (pick && pick.longDub) { lisanHandOffToLongDub(file); return; }", "You have used your free credits."):
            self.assertIn(key, app)
        idx = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="btnTranslate" data-i18n="none" onclick="translateButton()"', idx)

    def test_trim_dialog_and_long_dub(self):
        dlg = (ROOT / "dialogs.js").read_text(encoding="utf-8")
        self.assertIn("close({ longDub: true })", dlg)
        self.assertIn("دبلج الفيديو كاملاً بالدبلجة الطويلة", dlg)
        ld = (ROOT / "dub_long.html").read_text(encoding="utf-8")
        for key in ('indexedDB.open("lisan_handoff", 1)', "pickInitial = function () { return null; }", "onFilePicked(file);", "لقد استخدمت رصيدك المجاني"):
            self.assertIn(key, ld)


if __name__ == "__main__":
    unittest.main()
