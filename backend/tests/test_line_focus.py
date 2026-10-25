"""The editor puts the lines that need a look in front and folds the lines that look fine behind a "show" bar.

The pure part of the page script (between the "line focus (begin/end)" markers of dub_long.html) is run with node;
the rest checks that the page is wired to it in both places that draw the list, and that the style rules exist."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
HTML = (BASE / "dub_long.html").read_text(encoding="utf-8")
CSS = (BASE / "longdub_editor.css").read_text(encoding="utf-8")


def block():
    a = HTML.index("// ---- line focus (begin)")
    b = HTML.index("// ---- line focus (end)")
    return HTML[a:b]


def run_js(expression):
    code = block() + "\nconsole.log(JSON.stringify(" + expression + "));"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


def seg(i, **extra):
    r = {"segment_id": f"seg_{i}", "arabic_text": "نص", "speaker_confidence": "high", "overlaps": []}
    r.update(extra)
    return r


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class Logic(unittest.TestCase):
    def need(self, **extra):
        return run_js("needsLook(%s)" % json.dumps(seg(1, **extra)))

    def test_a_line_that_looks_fine_needs_no_look(self):
        self.assertFalse(self.need())

    def test_each_kind_of_doubt_needs_a_look(self):
        for name, extra in {"speaker": {"speaker_confidence": "low"}, "cut": {"cut_check": "end"}, "gender": {"gender_check": True},
                            "overlap": {"overlaps": ["seg_2"]}, "unheard": {"heard": False}, "no arabic": {"arabic_text": ""},
                            "blank arabic": {"arabic_text": "  "}, "arabic missing": {"arabic_text": None}}.items():
            self.assertTrue(self.need(**extra), name)

    def test_a_confirmed_or_empty_flag_needs_no_look(self):
        self.assertFalse(self.need(cut_check=None, gender_check=False, heard=True, speaker_confidence=None))

    def test_nothing_is_not_a_line(self):
        self.assertFalse(run_js("needsLook(null)"))

    def plan(self, rows, **kw):
        args = [json.dumps(rows), str(kw.get("from", 0)), str(kw.get("to", 10)), json.dumps(kw.get("all", False)), json.dumps(kw.get("shown")), json.dumps(kw.get("keep"))]
        return run_js("focusPlan(%s).map(function (x) { return x.row ? x.row.segment_id : x.gap; })" % ", ".join(args))

    def rows(self, n=10, flagged=()):
        return [seg(i, **({"cut_check": "start"} if i in flagged else {})) for i in range(n)]

    def test_fine_lines_between_flagged_ones_fold_into_bars(self):
        self.assertEqual(self.plan(self.rows(10, {3, 4, 9})),
                         [["seg_0", "seg_1", "seg_2"], "seg_3", "seg_4", ["seg_5", "seg_6", "seg_7", "seg_8"], "seg_9"])

    def test_every_line_is_either_shown_or_behind_a_bar(self):
        plan = self.plan(self.rows(10, {2, 5}))
        seen = [x for item in plan for x in (item if isinstance(item, list) else [item])]
        self.assertEqual(seen, [f"seg_{i}" for i in range(10)])

    def test_show_all_shows_every_line_and_no_bar(self):
        self.assertEqual(self.plan(self.rows(10, {3}), all=True), [f"seg_{i}" for i in range(10)])

    def test_opened_lines_and_the_line_just_worked_on_stay_visible(self):
        got = self.plan(self.rows(10, {0}), shown={"seg_4": True}, keep="seg_7")
        self.assertEqual(got, ["seg_0", ["seg_1", "seg_2", "seg_3"], "seg_4", ["seg_5", "seg_6"], "seg_7", ["seg_8", "seg_9"]])

    def test_a_page_of_only_fine_lines_is_one_bar(self):
        self.assertEqual(self.plan(self.rows(10)), [[f"seg_{i}" for i in range(10)]])

    def test_only_the_asked_page_is_planned(self):
        rows = self.rows(25, {12, 21})
        self.assertEqual(self.plan(rows, **{"from": 10, "to": 20}), [["seg_10", "seg_11"], "seg_12", [f"seg_{i}" for i in range(13, 20)]])
        self.assertEqual(self.plan(rows, **{"from": 20, "to": 30}), [["seg_20"], "seg_21", ["seg_22", "seg_23", "seg_24"]])

    def test_an_empty_page_is_an_empty_plan(self):
        self.assertEqual(self.plan([], **{"from": 0, "to": 10}), [])

    def test_count_of_lines_to_look_at(self):
        rows = json.dumps(self.rows(25, {1, 12, 21}))
        self.assertEqual(run_js("[lookCount(%s, 0, 10), lookCount(%s, 10, 20), lookCount(%s, 0, 25)]" % (rows, rows, rows)), [1, 1, 3])


class Wiring(unittest.TestCase):
    def test_both_places_that_draw_the_list_use_the_same_function(self):
        self.assertIn("fillSegList(list);", HTML)
        self.assertIn("if (list) fillSegList(list, focusId);", HTML)
        self.assertNotIn("segs.slice(reviewPage * 10, reviewPage * 10 + 10).forEach(function", HTML)

    def test_a_job_that_is_opened_starts_in_the_short_view(self):
        self.assertIn("reviewPage = 0; reviewedBatches = {}; focusAll = false; revealedLines = {};", HTML)

    def test_lines_shown_in_the_short_view_stay_shown_when_their_flag_is_cleared(self):
        self.assertIn("if (it.row) { if (!all) revealedLines[it.row.segment_id] = true;", HTML)

    def test_the_toolbar_and_the_page_flags_exist(self):
        self.assertIn("id='focusBar'", HTML)
        self.assertIn("ld-page-flag", HTML)
        self.assertIn("renderFocusBar();", HTML)

    def test_the_page_is_not_gated_on_the_view(self):
        # the confirm button and the "reviewed" ticks do not look at what is folded
        self.assertNotIn("focusAll", HTML[HTML.index("function renderConfirm"):HTML.index("function renderConfirm") + 6000])

    def test_style_rules_exist(self):
        for rule in (".ld-gap", ".ld-focus", ".ld-page-flag"):
            self.assertIn(rule, CSS)


if __name__ == "__main__":
    unittest.main()
