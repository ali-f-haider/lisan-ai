"""The finished screen is several cards, each with one job: the file, what to know about it, more downloads, YouTube, what next."""
import re
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
HTML = (BASE / "dub_long.html").read_text(encoding="utf-8")
CSS = (BASE / "longdub_editor.css").read_text(encoding="utf-8")


def function(name):
    a = HTML.index("function " + name + "(")
    b = HTML.index("\nfunction ", a + 10)
    return HTML[a:b]


class Cards(unittest.TestCase):
    def test_there_is_a_place_for_the_extra_cards_right_after_the_job_card(self):
        self.assertRegex(HTML, r'<div id="jobBody"></div>\s*</div>\s*<div id="doneCards"></div>')

    def test_the_cards_come_in_the_order_notes_downloads_youtube_next(self):
        body = function("renderJob")
        order = [body.index(x) for x in ("doneNotesCard(j, r)", 'doneCard("doneMore"', 'id="packCard"', 'doneCard("doneNext"')]
        self.assertEqual(order, sorted(order))

    def test_each_card_has_its_own_heading_in_both_languages(self):
        for key in ("doneNotesHeading", "moreDownloadsHeading", "nextHeading"):
            self.assertEqual(len(re.findall(key + ":", HTML)), 2, key)

    def test_the_main_card_keeps_the_file_and_the_things_that_decide_if_it_is_usable(self):
        body = function("renderJob")
        main = body[body.index("// The finished screen is several cards"):body.index("doneCardsHtml = doneNotesCard")]
        for needle in ("T.doneText", "T.doneWarn", "T.wmNote", "T.doneLip", "/download"):
            self.assertIn(needle, main)
        self.assertIn("if (r.failed_lines) h += ", main)
        self.assertIn('if (r.watermark && r.kind === "video") h += ', main)
        for needle in ("btnRedo", "btnShowLines", "packBox", "T.subsHeading"):
            self.assertNotIn(needle, main)

    def test_what_we_changed_lives_in_the_notes_card(self):
        notes = function("doneNotesCard")
        for needle in ("T.doneRephrased", "T.showChanges", "T.doneBg", "T.doneCut", "T.doneFast", "LisanBackground"):
            self.assertIn(needle, notes)
        self.assertIn("return doneCard(", notes)

    def test_a_card_without_content_is_not_drawn(self):
        self.assertIn("return inner ? ", function("doneCard"))

    def test_downloads_card_holds_tracks_and_subtitles_only(self):
        d = function("doneDownloadsHtml")
        self.assertIn("T.tracksHeading", d)
        self.assertIn("T.subsHeading", d)
        self.assertNotIn("packBox", d)

    def test_youtube_has_its_own_card_and_its_heading_is_the_card_heading(self):
        self.assertIn("'<div class=\"card\" id=\"packCard\"><h3>' + esc(T.packHeading) + q(esc(T.packNote))", HTML)
        self.assertNotIn("<strong>\" + esc(T.packHeading)", HTML)
        self.assertIn('id="packBox">\' + packInner(j)', HTML)

    def test_the_other_job_views_leave_no_extra_cards_behind(self):
        self.assertIn('if (st !== "done") $("doneCards").innerHTML = "";', HTML)
        self.assertIn('$("doneCards").innerHTML = doneCardsHtml;', HTML)

    def test_a_job_whose_file_is_gone_and_a_failed_job_keep_the_old_single_card(self):
        body = function("renderJob")
        self.assertIn('(j.status === "done" && !j.file_available)) h += showLinesHtml(j);', body)

    def test_style_rules_exist(self):
        self.assertIn(".pk-results", CSS)
        self.assertIn("#packState:empty", CSS)


if __name__ == "__main__":
    unittest.main()
