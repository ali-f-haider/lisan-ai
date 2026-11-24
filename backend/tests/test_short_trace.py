"""Where people stop in the short dub (short_trace.py / short_trace.js, 1.104.0)."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import short_trace as st

ROOT = Path(__file__).resolve().parent.parent
UID = "0a1b2c3d-1111-2222-3333-444455556666"


class StoreTests(unittest.TestCase):
    def test_steps_messages_and_leaving_make_the_summary(self):
        with tempfile.TemporaryDirectory() as t, patch.object(st, "_FOLDER", Path(t)):
            self.assertIsNone(st.summary(UID))
            st.add(UID, [{"k": "open", "t": "/app"}, {"k": "step", "s": "step1Card"}, {"k": "step", "s": "editorSection"},
                         {"k": "msg", "s": "editorSection", "t": "Translation failed.", "l": "error", "j": "job1"},
                         {"k": "msg", "s": "editorSection", "t": "Saved.", "l": "success"},
                         {"k": "leave", "s": "editorSection", "d": 240}])
            s = st.summary(UID)
            self.assertEqual((s["opened"], s["last_step"], s["left_at"], s["seconds"]), (1, "editorSection", "editorSection", 240))
            self.assertEqual([m["t"] for m in s["messages"]], ["Translation failed."])
            st.forget(UID)
            self.assertIsNone(st.summary(UID))

    def test_junk_is_dropped_and_text_is_cut(self):
        with tempfile.TemporaryDirectory() as t, patch.object(st, "_FOLDER", Path(t)):
            self.assertEqual(st.add(UID, [{"k": "hack"}, "x", {"k": "msg", "t": "a" * 1000, "s": "<b>step</b>"}]), 1)
            ev = st.events(UID)[0]
            self.assertEqual(len(ev["t"]), 300)
            self.assertEqual(ev["s"], "bstepb")
            self.assertEqual(st.add("../../etc", [{"k": "open"}]), 0)
            self.assertEqual(st.add(UID, [{"k": "open"}] * 100), st.MAX_PER_CALL)

    def test_old_and_too_many_notes_are_removed(self):
        with tempfile.TemporaryDirectory() as t, patch.object(st, "_FOLDER", Path(t)):
            p = Path(t) / f"{UID}.jsonl"
            old = int(time.time()) - (st.KEEP_DAYS + 1) * 86400
            p.write_text(json.dumps({"at": old, "k": "open"}) + "\n")
            self.assertEqual(st.events(UID), [])
            for _ in range(20):
                st.add(UID, [{"k": "step", "s": "step1Card"}] * st.MAX_PER_CALL)
            self.assertLessEqual(len(st.events(UID)), st.MAX_EVENTS)


class WiringTests(unittest.TestCase):
    def test_the_page_server_and_admin_are_connected(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        for key in ('@app.post("/api/trace")', '@app.get("/api/admin/trace")', '@app.get("/short_trace.js")',
                    'short_trace.forget(uid)', 'u["short_trace"] = short_trace.summary(uid)'):
            self.assertIn(key, main)
        self.assertIn('<script src="/short_trace.js"></script>', (ROOT / "index.html").read_text(encoding="utf-8"))
        self.assertIn("window.lisanTrace.msg(type, text)", (ROOT / "app.js").read_text(encoding="utf-8"))
        admin = (ROOT / "admin.html").read_text(encoding="utf-8")
        self.assertIn("function showTrace(", admin)
        self.assertIn("u.short_trace", admin)
        js = (ROOT / "short_trace.js").read_text(encoding="utf-8")
        self.assertIn('"/api/trace"', js)
        for k in ("open", "step", "msg", "dialog", "jserror", "leave"):
            self.assertIn(k, st._KINDS)
        priv = (ROOT / "privacy.html").read_text(encoding="utf-8")
        self.assertIn("How you use the short dub", priv)
        self.assertIn("كيف تستخدم الدبلجة القصيرة", priv)


if __name__ == "__main__":
    unittest.main()
