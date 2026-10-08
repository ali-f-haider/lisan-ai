"""The long-dub pages show short facts; explanations and the cost breakdown sit behind a "?" bubble."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


class LongDubHelpBubbleTests(unittest.TestCase):
    def setUp(self):
        self.page = read("dub_long.html")
        self.dialogs = read("dialogs.js")
        self.edit = read("dub_long_edit.html")

    def _function(self, name):
        start = self.page.index("function " + name + "(")
        nxt = re.search(r"\r?\nfunction \w+\(", self.page[start + 10:])
        return self.page[start:start + 10 + nxt.start()]

    def test_the_bubble_helper_accepts_ready_made_buttons(self):
        self.assertIn("button[data-tip-html]", self.dialogs)
        self.assertIn('data-tip-html="', self.page)

    def test_the_estimate_shows_one_total_and_no_cost_table(self):
        body = self._function("renderEstimate")
        self.assertIn("ld-totalline", body)
        self.assertNotIn('<table class="ld-table">', body)
        self.assertNotIn("estNow", body.split("var tip")[0])
        self.assertIn("q(tip)", body)

    def test_the_confirm_step_shows_only_the_amount_due(self):
        body = self._function("renderConfirm")
        self.assertIn("ld-totalline", body)
        self.assertNotIn('<table class="ld-table">', body)
        self.assertIn("q(tip)", body)

    def test_upload_card_notes_and_edit_page_notes_are_behind_a_question_mark(self):
        for ident in ("lipAskNote", "lipResNote"):
            self.assertIn('hiddenHelp("%s"' % ident, self.page)
        self.assertIn('id="recoveryText" class="ld-plain-note" data-tip-src', self.edit)
        self.assertIn('id="trackHelp" data-tip-src', self.edit)

    def test_the_conditions_stay_reachable_and_the_agree_box_stays_visible(self):
        body = self._function("renderEstimate")
        self.assertIn("<details", body)
        self.assertIn('id="agreeBox"', body)


if __name__ == "__main__":
    unittest.main()
