"""The feedback question and the invite links (feedback.py, feedback.js, Nov 2026)."""
import ast
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import feedback as fb

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
DAY = 86400.0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(fb, "FILE", Path(self.tmp.name) / "feedback.json")
        self.p.start()
        self.r = mock.patch.object(fb, "_restore", lambda: None)
        self.r.start()
        self.gifts = []

    def tearDown(self):
        self.r.stop()
        self.p.stop()
        self.tmp.cleanup()

    def give(self, by, credits, op, reason):
        self.gifts.append((by, credits, op))
        return True


class TheQuestion(Base):
    def test_asked_once_and_again_after_seven_days_at_most_twice(self):
        t = 1_000_000.0
        self.assertTrue(fb.should_ask("u1", t))
        fb.note_asked("u1", t)
        self.assertFalse(fb.should_ask("u1", t + 6 * DAY))
        self.assertTrue(fb.should_ask("u1", t + 7 * DAY))
        fb.note_asked("u1", t + 7 * DAY)
        self.assertFalse(fb.should_ask("u1", t + 100 * DAY))
        self.assertFalse(fb.should_ask("", t))

    def test_an_answer_is_kept_once_and_never_asked_again(self):
        self.assertEqual(fb.answer("u1", 0)[0], False)
        self.assertEqual(fb.answer("u1", "6")[0], False)
        self.assertEqual(fb.answer("u1", True)[0], False)
        self.assertEqual(fb.answer("u1", 2, "x" * 2001)[0], False)
        self.assertEqual(fb.answer("u1", "4", "  More   voices  ", "text_voice"), (True, None))
        fb.answer("u1", 1, "changed my mind")
        u = json.loads(fb.FILE.read_text(encoding="utf-8"))["users"]["u1"]
        self.assertEqual((u["stars"], u["text"], u["page"]), (4, "More voices", "text_voice"))
        self.assertFalse(fb.should_ask("u1"))
        self.assertEqual(fb.for_admin()["answers"][0]["stars"], 4)


class InviteLinks(Base):
    def test_the_code_stays_the_same(self):
        code = fb.invite_code("u1")
        self.assertRegex(code, r"^[a-z0-9]{8}$")
        self.assertEqual(fb.invite_code("u1"), code)
        self.assertEqual(fb.invite_link("u1"), "https://lisanai.org/?ref=" + code)
        self.assertNotEqual(fb.invite_code("u2"), code)

    def test_a_new_friend_gives_the_thank_you_once(self):
        code, now = fb.invite_code("u1"), 2_000_000.0
        self.assertEqual(fb.claim("f1", code.upper(), now - 3600, self.give, now), "given")
        self.assertEqual(fb.claim("f1", code, now - 3600, self.give, now), "already")
        self.assertEqual(self.gifts, [("u1", 50, fb.gift_operation("f1"))])
        self.assertEqual(fb.gift_operation("f1"), fb.gift_operation("f1"))
        self.assertEqual(fb.invite_status("u1"), {"friends": 1, "gifts": 1, "left": 9, "credits": 50, "max": 10})

    def test_what_is_not_counted(self):
        code, now = fb.invite_code("u1"), 2_000_000.0
        self.assertEqual(fb.claim("u1", code, now, self.give, now), "self")
        self.assertEqual(fb.claim("f1", "nope!!", now, self.give, now), "bad_code")
        self.assertEqual(fb.claim("f1", "abcdef12", now, self.give, now), "bad_code")
        self.assertEqual(fb.claim("f2", code, now - 4 * DAY, self.give, now), "old_account")     # an account from before the link
        self.assertEqual(fb.claim("f3", code, None, self.give, now), "old_account")              # age unknown: not counted
        self.assertEqual(self.gifts, [])

    def test_at_most_ten_gifts(self):
        code, now = fb.invite_code("u1"), 2_000_000.0
        got = [fb.claim("f%d" % i, code, now, self.give, now) for i in range(12)]
        self.assertEqual(got, ["given"] * 10 + ["limit"] * 2)
        self.assertEqual(len(self.gifts), 10)
        self.assertEqual(fb.invite_status("u1")["left"], 0)

    def test_a_gift_that_failed_is_tried_again_with_the_same_operation(self):
        code, now = fb.invite_code("u1"), 2_000_000.0
        self.assertEqual(fb.claim("f1", code, now, lambda *a: False, now), "failed")
        self.assertEqual(fb.retry_gifts(self.give, now + 30), 0)          # not straight away
        self.assertEqual(fb.retry_gifts(self.give, now + 120), 1)
        self.assertEqual(fb.retry_gifts(self.give, now + 240), 0)
        self.assertEqual(self.gifts, [("u1", 50, fb.gift_operation("f1"))])

    def test_a_deleted_account_is_forgotten(self):
        code, now = fb.invite_code("u1"), 2_000_000.0
        fb.answer("u1", 5, "nice")
        fb.claim("f1", code, now, self.give, now)
        fb.forget("u1")
        data = json.loads(fb.FILE.read_text(encoding="utf-8"))
        self.assertNotIn("u1", data["users"])
        self.assertNotIn(code, data["codes"])
        self.assertEqual(data["invites"]["f1"]["by"], "deleted account")
        fb.forget("f1")
        self.assertNotIn("f1", json.loads(fb.FILE.read_text(encoding="utf-8"))["invites"])

    def test_a_damaged_file_is_kept_aside_not_overwritten(self):
        fb.FILE.write_text("{broken", encoding="utf-8")
        fb.answer("u1", 3)
        self.assertEqual(len(list(fb.FILE.parent.glob("feedback.json.damaged-*"))), 1)


class Wiring(unittest.TestCase):
    def test_routes_backup_and_account_deletion(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        for needle in ('@app.get("/api/feedback/ask")', '@app.post("/api/feedback")', '@app.post("/api/invite/claim")',
                       '@app.get("/api/invite")', '@app.get("/api/admin/feedback")', '@app.get("/feedback.js")',
                       "feedback.forget(uid)", "[voice_numbers.FILE, feedback.FILE]"):
            self.assertIn(needle, main)
        self.assertEqual(main.count("[voice_numbers.FILE, feedback.FILE]"), 2)
        ask = main.split("def feedback_ask(")[1].split("\n@app.")[0]
        self.assertLess(ask.index("feedback.should_ask(uid)"), ask.index("_engaged_uid(uid)"))   # the cheap check first
        gift = main.split("def _invite_gift(")[1].split("\ndef ")[0]
        self.assertIn("adjust(_sb_rpc", gift)

    def test_the_pages_load_the_script(self):
        for page in ("landing.html", "index.html", "text_voice.html", "dub_long.html", "dub_long_edit.html", "account.html"):
            self.assertIn('<script src="/feedback.js" defer></script>', (ROOT / page).read_text(encoding="utf-8"), page)
        js = (ROOT / "feedback.js").read_text(encoding="utf-8")
        for key in ("title", "improve", "send", "later", "thanks", "glad", "copy", "copied", "whatsapp", "close", "failed", "share"):
            self.assertEqual(len(re.findall(r"\b" + key + r":", js)), 2, key)
        self.assertIn('"/api/feedback/ask"', js)


@unittest.skipUnless(NODE, "node is needed")
class TheScript(unittest.TestCase):
    RUN = r"""
const vm = require('vm'), fs = require('fs');
function run(search, saved, status) {
  const store = Object.assign({}, saved ? { lisan_invite: JSON.stringify(saved) } : {}), calls = [], listeners = [];
  const ctx = { location: { search: search, pathname: '/app' }, Date: Date, JSON: JSON, setTimeout: () => 0,
    localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; } },
    fetch: (u, o) => { calls.push([u, o && o.body]); return Promise.resolve({ status: status, ok: status === 200, json: () => Promise.resolve({}) }); },
    document: { addEventListener: (t, f) => listeners.push(t) }, window: {} };
  vm.runInNewContext(fs.readFileSync('feedback.js', 'utf8'), ctx);
  return new Promise(r => setTimeout(() => r({ store, calls, listeners, re: ctx.window.LisanFeedback ? 1 : 0 }), 20));
}
(async () => {
  const out = {};
  out.landing = await run('?ref=AB12cd34', null, 200);
  out.loggedIn = await run('', { code: 'ab12cd34', at: Date.now() }, 200);
  out.loggedOut = await run('', { code: 'ab12cd34', at: Date.now() }, 401);
  out.old = await run('', { code: 'ab12cd34', at: Date.now() - 31 * 86400000 }, 200);
  const src = fs.readFileSync('feedback.js', 'utf8');
  const re = new RegExp(/var RESULT = (\/.*\/);/.exec(src)[1].slice(1, -1));
  out.matches = ['/api/text_voice/0123456789abcdef/audio?v=1', '/api/download/abc_final_dubbed.mp3?cache=1', '/api/longdub/x1/download',
                 '/api/my_jobs/j1/video', '/api/text_voice/0123456789abcdef', '/api/voices/preview/Luna', '/api/download/abc_audio.wav'].map(u => re.test(u));
  console.log(JSON.stringify(out));
})();
"""

    def test_the_link_is_remembered_and_sent_once_logged_in(self):
        got = json.loads(subprocess.run([NODE, "-e", self.RUN], cwd=ROOT, capture_output=True, text=True, check=True).stdout)
        self.assertEqual(json.loads(got["landing"]["store"]["lisan_invite"])["code"], "ab12cd34")
        self.assertEqual(got["landing"]["calls"], [])                                  # not sent on the page with the link
        self.assertEqual([c[0] for c in got["loggedIn"]["calls"]], ["/api/invite/claim"])
        self.assertNotIn("lisan_invite", got["loggedIn"]["store"])
        self.assertIn("lisan_invite", got["loggedOut"]["store"])                        # kept until the person is logged in
        self.assertEqual((got["old"]["calls"], got["old"]["store"]), ([], {}))
        self.assertEqual(got["matches"], [True, True, True, True, False, False, False])
        self.assertEqual(got["loggedIn"]["listeners"], ["click"])


if __name__ == "__main__":
    unittest.main()
