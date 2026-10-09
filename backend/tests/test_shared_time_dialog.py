"""Both long-dub pages use ONE time dialog (longdub_player.js); the edit page chooses film, file, then Open.

The browser test at the bottom plays real audio in Chromium. It runs only when LISAN_BROWSER_TESTS=1 and Playwright
is installed, so the normal suite stays fast and independent of a browser."""
import json
import math
import mimetypes
import os
import re
import shutil
import struct
import subprocess
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


class OneDialogTests(unittest.TestCase):
    def test_both_pages_load_the_shared_dialog_and_keep_no_copy_of_it(self):
        main_page, edit_page, edit_js = read("dub_long.html"), read("dub_long_edit.html"), read("dub_long_edit.js")
        self.assertIn('<script src="/longdub_player.js"></script>', main_page)
        self.assertIn('<script src="/longdub_player.js"></script>', edit_page)
        self.assertLess(edit_page.index("/longdub_player.js"), edit_page.index("/dub_long_edit.js"))
        for text in (main_page, edit_js):
            self.assertIn("LisanPlayer.open(", text)
            self.assertNotIn("ld-pl-overlay", text, "a second copy of the dialog crept back in")
            self.assertNotIn("Play this range", text)
        self.assertIn("ld-pl-overlay", read("longdub_player.js"))

    def test_the_player_script_is_served(self):
        src = read("main.py")
        self.assertRegex(src, r'@app\.get\("/longdub_player\.js"\)\s+def longdub_player_js\(\)')
        self.assertIn('BASE_DIR / "longdub_player.js"', src)

    def test_edit_page_has_film_file_and_open_in_one_row(self):
        page = read("dub_long_edit.html")
        row = page[page.index('class="ld-open-row"'):page.index('id="openNote"')]
        order = [row.index(i) for i in ('id="projects"', 'id="pickFile"', 'id="listenFile"', 'id="openProject"')]
        self.assertEqual(order, sorted(order), "film list, then Choose original file, then Open")
        js = read("dub_long_edit.js")
        self.assertIn("$('openProject').onclick", js)
        self.assertNotIn("open(this.value)", js, "choosing a film must not open it by itself")

    def test_range_play_stops_by_the_media_clock_and_not_only_by_animation_frames(self):
        js = read("longdub_player.js")
        self.assertIn("function checkStop(", js)
        self.assertRegex(js, r"addEventListener\('timeupdate', function \(\) \{ checkStop\(st\)")
        self.assertIn("'seeked'", js, "play from the start mark must wait for the jump to finish")

    def test_time_helpers(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        script = (
            "const vm=require('vm'),fs=require('fs');const c=vm.createContext({window:{},document:{}});"
            "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);const P=c.window.LisanPlayer;"
            "console.log(JSON.stringify([P.fmt(83.5),P.fmt(-3),P.parse('1:23.5'),P.parse('83,5'),P.parse('\\u0661:\\u0662\\u0663'),P.parse('x'),P.isOpen()]))"
        )
        out = subprocess.check_output([node, "-e", script, str(ROOT / "longdub_player.js")])
        self.assertEqual(json.loads(out), ["1:23.500", "0:00.000", 83.5, 83.5, 83, None, False])


def _tone(path, seconds=12):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 8000 * 440 * 6.2832))) for i in range(8000 * seconds)))


@unittest.skipUnless(os.environ.get("LISAN_BROWSER_TESTS") == "1", "set LISAN_BROWSER_TESTS=1 to run the browser test")
class BrowserTests(unittest.TestCase):
    def test_play_start_to_end_stops_at_the_end_mark_on_the_edit_page(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright is not installed")
        import tempfile
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp, True)
        _tone(tmp / "t.wav"); tone = (tmp / "t.wav").read_bytes(); size = len(tone)
        row = lambda i, a, b: {"segment_id": "g%d" % i, "start": a, "end": b, "speaker_id": "s1", "text": "Hi", "arabic_text": "x", "emotion": "neutral", "waqf": "auto", "overlaps": []}
        state = {"segments": [row(0, 2, 4)], "batches": [], "overlaps": [], "speaker_list": [{"id": "s1", "name": "Ali"}], "duration": 12, "can_play": True, "has_assets": True, "busy": False, "history": []}
        job = {"id": "j1", "name": "Movie", "status": "done", "size": size, "has_video": False, "duration": 12}

        def handler(route):
            req = route.request; path = req.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
            if path == "api/longdub":
                body = {"jobs": [job], "credits": 5}
            elif path == "api/longdub/j1/corrections/player":
                body = {"url": "/api/longdub/j1/media", "kind": "audio"}
            elif path == "api/longdub/j1/media":
                rng = req.headers.get("range")
                if rng:
                    a, b = rng.replace("bytes=", "").split("-"); a = int(a); b = int(b) if b else size - 1
                    route.fulfill(status=206, content_type="audio/wav", body=tone[a:b + 1], headers={"Accept-Ranges": "bytes", "Content-Range": "bytes %d-%d/%d" % (a, b, size)}); return
                route.fulfill(status=200, content_type="audio/wav", body=tone, headers={"Accept-Ranges": "bytes"}); return
            elif path.startswith("api/"):
                body = state
            else:
                fp = ROOT / ("dub_long_edit.html" if path in ("", "dub-long-edit") else path)
                if fp.is_file():
                    route.fulfill(status=200, content_type=mimetypes.guess_type(str(fp))[0] or "text/plain", body=fp.read_bytes())
                else:
                    route.fulfill(status=404, body="")
                return
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
            try:
                page = browser.new_page(); errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.route("**/*", handler); page.goto("http://x.test/dub-long-edit")
                page.select_option("#projects", "j1")
                self.assertTrue(page.is_hidden("#editor"), "choosing a film alone does not open it")
                page.click("#openProject"); page.wait_for_selector("#editor:not(.hidden)")
                page.click('.ld-seg button:has-text("Enter man.")'); page.wait_for_selector("#plMain:not(.hidden)", timeout=8000)
                page.click("#plRange"); page.wait_for_timeout(3200)
                self.assertTrue(page.evaluate("document.getElementById('plVideo').paused"), "Play start to end never stopped")
                self.assertAlmostEqual(page.evaluate("document.getElementById('plVideo').currentTime"), 4.0, delta=0.2)
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
