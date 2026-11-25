"""Getting-started emails (onboarding.py, 1.108.0)."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import onboarding as ob

ROOT = Path(__file__).resolve().parent.parent
UID = "0a1b2c3d-1111-2222-3333-444455556666"
DAY = 86400


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.p = patch.object(ob, "_FOLDER", Path(self.t.name))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.t.cleanup()


class FlowTests(Base):
    def _run(self, now, made=False):
        sent = []
        c = ob.run(lambda to, s, h, t, u: sent.append((to, s, u)) or True, lambda uid: made, lambda uid: "a@x.org", lambda uid: 95, now)
        return c, sent

    def test_nothing_is_sent_while_the_admin_switch_is_off(self):
        ob.opt_in(UID, time.time() - 1.2 * DAY, "ar")
        self.assertEqual(self._run(time.time())[1], [])

    def test_three_emails_in_order_then_nothing(self):
        ob.set_enabled(True)
        t0 = time.time() - 0.5 * DAY
        ob.opt_in(UID, t0, "ar")
        self.assertEqual(self._run(t0 + 0.5 * DAY)[1], [])                 # not a day old yet
        _c, s1 = self._run(t0 + 1.1 * DAY)
        self.assertEqual(len(s1), 1)
        self.assertIn("3 خطوات", s1[0][1])
        self.assertEqual(self._run(t0 + 1.2 * DAY)[1], [])                 # never twice
        self.assertEqual(len(self._run(t0 + 3.2 * DAY)[1]), 1)
        self.assertEqual(len(self._run(t0 + 7.2 * DAY)[1]), 1)
        self.assertEqual(self._run(t0 + 9 * DAY)[1], [])
        self.assertEqual(ob.stats()["sent"], {"1": 1, "3": 1, "7": 1})

    def test_making_something_or_unsubscribing_stops_them(self):
        ob.set_enabled(True)
        t0 = time.time()
        ob.opt_in(UID, t0, "en")
        self.assertEqual(self._run(t0 + 1.1 * DAY, made=True)[1], [])
        self.assertEqual(self._run(t0 + 3.1 * DAY)[1], [])                 # done stays done
        other = "0a1b2c3d-9999-2222-3333-444455556666"
        ob.opt_in(other, t0, "en")
        ob.opt_out(other)
        self.assertEqual(self._run(t0 + 1.1 * DAY)[1], [])
        self.assertFalse(ob.wants(other))

    def test_late_emails_are_skipped_and_old_accounts_ignored(self):
        ob.set_enabled(True)
        t0 = time.time()
        ob.opt_in(UID, t0, "en")
        self.assertEqual(self._run(t0 + 6 * DAY)[1], [])                   # day-1 email is too late on day 6
        ob.opt_in(UID, t0 - 30 * DAY, "en")
        self.assertEqual(self._run(t0)[1], [])

    def test_unknown_check_waits_for_the_next_round(self):
        ob.set_enabled(True)
        t0 = time.time()
        ob.opt_in(UID, t0, "en")
        c = ob.run(lambda *a: True, lambda uid: None, lambda uid: "a@x.org", None, t0 + 1.1 * DAY)
        self.assertEqual((c["sent"], c["skipped"]), (0, 1))


class EmailTests(Base):
    def test_every_email_has_both_languages_logo_and_unsubscribe(self):
        for step in ob.STEPS:
            for lang in ("ar", "en"):
                subject, body, text = ob.render(step, lang, UID, 100)
                self.assertTrue(subject)
                self.assertIn(ob.LOGO, body)
                self.assertIn(ob.unsubscribe_url(UID).replace("&", "&amp;") if "&amp;" in body else ob.unsubscribe_url(UID), body)
                self.assertIn("dir=\"rtl\"", body)
                self.assertIn("/imprint", body)
                self.assertIn(ob.unsubscribe_url(UID), text)

    def test_tokens(self):
        tok = ob.token(UID)
        self.assertTrue(ob.check_token(UID, tok))
        self.assertFalse(ob.check_token(UID, "0" * 32))
        self.assertFalse(ob.check_token("../x", tok))


class WiringTests(unittest.TestCase):
    def test_login_account_admin_privacy_and_server(self):
        login = (ROOT / "login.html").read_text(encoding="utf-8")
        self.assertIn('id="wantTips"', login)
        self.assertIn("tips: tips, lang: lang", login)
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        for key in ('@app.get("/unsubscribe")', '@app.post("/unsubscribe")', '"/unsubscribe"', "onboarding.forget(uid)",
                    "tips: Optional[bool] = None", "_onboarding_opt_in", '@app.get("/api/admin/onboarding/preview")', '"List-Unsubscribe-Post"'):
            self.assertIn(key, main)
        self.assertIn("loadTips();", (ROOT / "account.html").read_text(encoding="utf-8"))
        self.assertIn("function obPreview(", (ROOT / "admin.html").read_text(encoding="utf-8"))
        priv = (ROOT / "privacy.html").read_text(encoding="utf-8")
        self.assertIn("tips for getting started", priv)
        self.assertIn("نصائح للبدء", priv)


if __name__ == "__main__":
    unittest.main()
