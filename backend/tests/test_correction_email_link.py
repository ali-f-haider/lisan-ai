"""The 'corrections are ready' email opens the finished project on the correction page, at its downloads."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import longdub_edits  # noqa: E402


class EmailLinkTests(unittest.TestCase):
    def test_link_names_the_project(self):
        pid = "3f2a9c1e-7b64-4d0a-9a55-0c1d2e3f4a5b"
        self.assertEqual(longdub_edits.ready_link(pid), "https://lisanai.org/dub-long-edit?project=" + pid)
        self.assertIn("?project=" + pid, longdub_edits.ready_email_text(pid))

    def test_anything_that_is_not_an_id_gives_the_plain_page(self):
        plain = "https://lisanai.org/dub-long-edit"
        for bad in (None, "", "../x?y=1", "a b", "x" * 65, "<script>"):
            self.assertEqual(longdub_edits.ready_link(bad), plain, repr(bad))

    def test_the_finished_run_sends_that_text(self):
        src = (ROOT / "longdub_edits.py").read_text(encoding="utf-8")
        self.assertIn("ready_email_text(job.get('edit_of'))", src)

    def test_the_page_opens_the_project_from_the_link_and_scrolls_to_the_downloads(self):
        js = (ROOT / "dub_long_edit.js").read_text(encoding="utf-8")
        self.assertIn("get('project')", js)
        self.assertIn("scrollIntoView", js)
        self.assertIn("autoOpen();", js)


if __name__ == "__main__":
    unittest.main()
