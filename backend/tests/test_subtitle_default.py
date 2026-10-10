"""An English subtitle file is always used the "Smart" way, without asking (no choice box) and with the lines the AI did not hear added, on the long and the short dub page."""
import re, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


class SubtitleIsUsedWithoutAskingTests(unittest.TestCase):
    def test_neither_page_opens_the_choice_box(self):
        for name in ("dub_long.html", "app.js"):
            self.assertNotIn("LisanDialog.subtitle(", read(name), name)

    def test_both_pages_send_the_smart_mode_and_add_the_lines_the_ai_did_not_hear(self):
        self.assertIn('{ mode: "auto", add_missed: true }', read("dub_long.html"))
        self.assertIn('const choice = { mode: "auto", add_missed: true };', read("app.js"))

    def test_the_short_dub_message_no_longer_points_to_a_tick_box_that_does_not_exist(self):
        self.assertNotIn("tick", re.sub(r"\s+", " ", read("app.js").split("r.missed && !r.added", 1)[1][:400]))

    def test_the_assistant_does_not_describe_a_choice_the_user_cannot_make(self):
        text = read("assistant_service.py")
        self.assertNotIn('then choose how the lines are written', text)
        self.assertNotIn('the user chooses in the box', text)
        self.assertIn('the user is not asked', text)

    def test_the_server_default_is_still_smart(self):
        import inspect, subs_align
        self.assertEqual(inspect.signature(subs_align.correct_rows).parameters["mode"].default, "auto")


if __name__ == "__main__":
    unittest.main()
