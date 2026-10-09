"""Dub Long page: the line-ending choice for Arabic lines and the video tutorial. The page is read as text (no browser needed)."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PAGE = (ROOT / "dub_long.html").read_text(encoding="utf-8")


class LineEnding(unittest.TestCase):
    def test_every_row_gets_the_picker_next_to_the_other_arabic_line_tools(self):
        self.assertIn("function makeWaqfPicker(", PAGE)
        self.assertIn("footR.appendChild(makeWaqfPicker(s))", PAGE)

    def test_the_three_choices_are_the_ones_the_server_accepts(self):
        import arabic_waqf
        for mode in re.findall(r'\["(auto|stop|join)", T\.waqf', PAGE):
            self.assertIn(mode, arabic_waqf.MODES)
        self.assertEqual(sorted(set(re.findall(r'\["(\w+)", T\.waqf', PAGE))), sorted(arabic_waqf.MODES))

    def test_a_choice_is_saved_through_the_normal_line_save_and_the_save_queue_empties_afterwards(self):
        self.assertIn('queueSave(s.segment_id, "waqf", sel.value)', PAGE)
        # both places that list the saved fields must know "waqf", or the save would repeat forever
        self.assertIn('["text", "arabic_text", "speaker_id", "emotion", "waqf"].forEach', PAGE)
        self.assertIn('!("emotion" in d) && !("waqf" in d)', PAGE)
        self.assertIn('["text", "arabic_text", "speaker_id", "waqf"].forEach(function (f) { if (f in d) r[f] = d[f]; })', PAGE)

    def test_the_labels_exist_in_both_languages(self):
        for key in ("waqfLabel", "waqfAuto", "waqfStop", "waqfJoin", "waqfHint", "tutHeading", "tutNote"):
            self.assertEqual(len(re.findall(r"\b%s:" % key, PAGE)), 2, key)

    def test_the_server_page_rows_carry_the_choice_and_the_dub_uses_it(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        service = (ROOT / "longdub_service.py").read_text(encoding="utf-8")
        self.assertIn('d["waqf"] = r.get("waqf") or "auto"', main)
        self.assertIn('r.get("waqf"), gap_', service)


class Tutorial(unittest.TestCase):
    def test_the_file_is_chosen_by_page_language_and_the_card_starts_hidden(self):
        self.assertIn('"/static/Lisan-Ai-Long-Dub-Tutorial-" + (LANG === "ar" ? "Ar" : "En") + ".mp4"', PAGE)
        self.assertRegex(PAGE, r'<div class="card hidden" id="tutorialCard">')

    def test_it_is_shown_only_when_the_file_really_exists_on_the_server(self):
        body = PAGE[PAGE.index("function mountTutorial()"):PAGE.index("(function init()")]
        self.assertIn('method: "HEAD"', body)
        self.assertIn('indexOf("video") !== 0', body)
        self.assertLess(body.index("indexOf(\"video\") !== 0"), body.index('card.classList.remove("hidden")'))
        self.assertIn('preload = "none"', body)              # nothing is downloaded until Play

    def test_the_card_is_collapsible_and_collapsed_by_default(self):
        self.assertRegex(PAGE, r'<details class="ld-tutdetails" id="tutDetails">\s*<summary>')      # no "open" attribute
        self.assertNotIn('id="tutDetails" open', PAGE)
        self.assertNotRegex(PAGE, r'tutDetails["\)]*\.(open|setAttribute\("open")\s*=?')            # nothing opens it by itself
        self.assertIn('$("tutDetails").addEventListener("toggle"', PAGE)                               # closing it stops the video

    def test_it_starts_with_the_page(self):
        self.assertIn("applyStaticText();\n  mountTutorial();", PAGE.replace("\r\n", "\n"))


if __name__ == "__main__":
    unittest.main()
