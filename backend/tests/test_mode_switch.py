"""The Short clip | Full video switch under the top bar of the app and of Long Dub (mode_switch.js)."""
import json
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BACK = os.path.dirname(HERE)


def _read(name):
    with open(os.path.join(BACK, name), encoding="utf-8") as f:
        return f.read()


NODE = shutil.which("node")

# A tiny stand-in for the browser: enough DOM for the script, and a record of what it drew.
HARNESS = r"""
const fs = require('fs');
const [, jsPath, mode, lang, limitsJson, fetchFails] = process.argv;
const attrs = {};
const host = { _attrs: {}, innerHTML: '', className: '',
  getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; },
  setAttribute(k, v) { this._attrs[k] = String(v); } };
host._attrs['data-mode'] = mode;
let observer = null;
const html = { _a: lang ? { lang } : {}, getAttribute(k) { return k in this._a ? this._a[k] : null; } };
global.document = { documentElement: html, getElementById: (id) => id === 'modeSwitch' ? host : null };
global.localStorage = { getItem: () => null };
global.MutationObserver = function (cb) { this.observe = () => { observer = cb; }; };
global.fetch = () => fetchFails === '1' ? Promise.reject(new Error('x')) : Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(limitsJson)) });
eval(fs.readFileSync(jsPath, 'utf8'));
setTimeout(() => {
  const first = host.innerHTML, dir0 = host._attrs.dir, label0 = host._attrs['aria-label'];
  if (observer) { html._a.lang = html._a.lang === 'ar' ? 'en' : 'ar'; observer(); }
  console.log(JSON.stringify({ html: first, flipped: host.innerHTML, dir: dir0, label: label0 }));
}, 20);
"""


def _run(mode, lang, limits, fails=False):
    p = subprocess.run([NODE, "-e", HARNESS, os.path.join(BACK, "mode_switch.js"), mode, lang, json.dumps(limits), "1" if fails else "0"],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


LIMITS = {"short_max_sec": 30, "long_max_min": 10}


class Pages(unittest.TestCase):
    def test_app_page_has_switch_on_short(self):
        h = _read("index.html")
        self.assertEqual(len(re.findall(r'id="modeSwitch" data-mode="short"', h)), 1)
        self.assertIn('<script src="/mode_switch.js"></script>', h)
        # the switch sits right after the top bar, before the first card
        self.assertLess(h.index('id="userBar"'), h.index('id="modeSwitch"'))
        self.assertLess(h.index('id="modeSwitch"'), h.index('id="step1Card"'))

    def test_app_page_dropped_the_small_header_link(self):
        h = _read("index.html")
        self.assertNotIn('class="btn-logout"><span id="longEnPart"', h)
        # app.js still toggles those ids, and it must tolerate them being gone
        js = _read("app.js")
        self.assertIn('var longEn = document.getElementById("longEnPart");\n            if (longEn)', js.replace("\r\n", "\n"))
        self.assertIn('var longAr = document.getElementById("longArPart");\n            if (longAr)', js.replace("\r\n", "\n"))

    def test_long_dub_page_has_switch_on_full(self):
        h = _read("dub_long.html")
        self.assertEqual(len(re.findall(r'id="modeSwitch" data-mode="full"', h)), 1)
        self.assertIn('<script src="/mode_switch.js"></script>', h)
        self.assertLess(h.index('id="userBar"'), h.index('id="modeSwitch"'))
        self.assertLess(h.index('id="modeSwitch"'), h.index('class="ld-wrap"'))
        self.assertIn('id="backBtn"', h)      # applyStaticText still sets its text

    def test_script_is_served(self):
        m = _read("main.py")
        self.assertIn('@app.get("/mode_switch.js")', m)
        self.assertIn('BASE_DIR / "mode_switch.js"', m)
        self.assertTrue(os.path.exists(os.path.join(BACK, "mode_switch.js")))

    def test_styles_use_theme_variables_and_have_a_phone_rule(self):
        c = _read("styles.css")
        self.assertIn(".mode-switch .ms-tab.on", c)
        self.assertIn("body.dark.ui-new a.ms-tab", c)
        self.assertRegex(c, r"@media \(max-width: 520px\) \{\s*\.mode-switch")
        block = c[c.index("Short clip | Full video switch"):]
        self.assertNotRegex(block, r"#[0-9a-fA-F]{6}\b(?!.*#fff)", "colours must come from theme variables (only #fff text on the active tab)")


@unittest.skipUnless(NODE, "node not installed")
class Behaviour(unittest.TestCase):
    def test_short_mode_marks_short_active_and_links_to_full(self):
        r = _run("short", "en", LIMITS)
        h = r["html"]
        self.assertRegex(h, r'<div class="ms-tab on" data-mode="short" aria-current="page">')
        self.assertRegex(h, r'<a class="ms-tab" data-mode="full" href="/dub-long">')
        self.assertNotIn('href="/app"', h)

    def test_full_mode_marks_full_active_and_links_to_short(self):
        h = _run("full", "en", LIMITS)["html"]
        self.assertRegex(h, r'<div class="ms-tab on" data-mode="full" aria-current="page">')
        self.assertRegex(h, r'<a class="ms-tab" data-mode="short" href="/app">')
        self.assertNotIn('href="/dub-long"', h)

    def test_captions_quote_the_server_limits(self):
        h = _run("short", "en", {"short_max_sec": 45, "long_max_min": 25})["html"]
        self.assertIn("Up to 45 seconds", h)
        self.assertIn("Up to 25 minutes", h)
        self.assertNotIn("Up to 30 seconds", h)

    def test_captions_stay_generic_when_limits_cannot_be_read(self):
        h = _run("short", "en", LIMITS, fails=True)["html"]
        self.assertIn("Quick dubs", h)
        self.assertIn("Full-length dubs", h)
        self.assertNotRegex(h, r"Up to \d")

    def test_bad_numbers_do_not_print_garbage(self):
        h = _run("short", "en", {"short_max_sec": None, "long_max_min": "x"})["html"]
        self.assertIn("Quick dubs", h)
        self.assertIn("Full-length dubs", h)

    def test_arabic(self):
        r = _run("short", "ar", LIMITS)
        self.assertIn("مقطع قصير", r["html"])
        self.assertIn("فيديو كامل", r["html"])
        self.assertIn("حتى 30 ثانية", r["html"])
        self.assertIn("حتى 10 دقائق", r["html"])
        self.assertEqual(r["dir"], "rtl")

    def test_arabic_counted_nouns(self):
        h = _run("short", "ar", {"short_max_sec": 5, "long_max_min": 15})["html"]
        self.assertIn("حتى 5 ثوانٍ", h)
        self.assertIn("حتى 15 دقيقة", h)

    def test_redraws_when_the_page_flips_language(self):
        r = _run("short", "en", LIMITS)
        self.assertIn("Short clip", r["html"])
        self.assertIn("مقطع قصير", r["flipped"])      # the harness flips <html lang> to ar and fires the observer

    def test_page_text_is_escaped(self):
        js = _read("mode_switch.js")
        self.assertIn("function esc", js)
        self.assertEqual(js.count("esc("), 3)       # the definition, plus the title and the caption of a tab


if __name__ == "__main__":
    unittest.main()
