"""The pop-up message list (the small tab under Log Out) must be empty after every logout and every new login.

The list lives in sessionStorage, which survives page changes inside one tab. So every page that can log a person
out, and every page a new login passes through, must empty it. Static check, no browser needed.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKERS = ("lisan_notify_log", "NOTIFY_LOG_KEY")


def served_sources():
    for path in sorted(ROOT.glob("*.html")) + sorted(ROOT.glob("*.js")):
        if re.search(r"(-\d+|\d|\.bak|\.orig|_old|_backup)\.(html|js)$", path.name):
            continue  # numbered or backup copies are not served pages
        yield path


def clears_log(text):
    return any(marker in text for marker in MARKERS)


class NotifyLogClearingTests(unittest.TestCase):
    def test_every_logout_path_empties_the_message_list(self):
        found = 0
        for path in served_sources():
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "/api/logout" not in text:
                continue
            found += 1
            with self.subTest(file=path.name):
                self.assertTrue(clears_log(text), f"{path.name} logs a person out without emptying the message list")
        self.assertGreater(found, 0)

    def test_every_page_a_new_login_passes_through_starts_with_an_empty_list(self):
        for name in ("login.html", "auth_callback.html"):
            with self.subTest(file=name):
                text = (ROOT / name).read_text(encoding="utf-8", errors="ignore")
                self.assertIn("removeItem", text)
                self.assertTrue(clears_log(text), f"{name} must empty the message list")


class ServerLoginIdTests(unittest.TestCase):
    def test_the_user_info_answer_carries_an_id_of_this_login_but_never_the_cookie(self):
        text = (ROOT / "main.py").read_text(encoding="utf-8", errors="ignore")
        start = text.index('@app.get("/api/user/info")')
        block = text[start:start + 9000]
        self.assertIn('"login_id"', block)
        self.assertIn("sha256", block)
        self.assertNotIn('"login_id": cookie', block)


if __name__ == "__main__":
    unittest.main()
