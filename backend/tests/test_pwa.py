"""Lisan AI as a computer app (installable web app, 1.87.0): installable only on a computer-sized screen with a mouse, the
service worker keeps no copies of anything, and the manifest can be read before anyone is logged in."""
import ast
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
NODE = shutil.which("node")


def node(script):
    return json.loads(subprocess.check_output([NODE, "-e", script], cwd=ROOT, timeout=60))


class Manifest(unittest.TestCase):
    def test_the_manifest_makes_an_app_window_that_opens_on_long_dub(self):
        m = json.loads((ROOT / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual((m["name"], m["start_url"], m["scope"], m["display"]), ("Lisan AI", "/dub-long", "/", "standalone"))
        sizes = {i["sizes"]: i["src"] for i in m["icons"]}
        self.assertEqual(set(sizes), {"192x192", "512x512"})
        from PIL import Image
        for size, src in sizes.items():
            with Image.open(ROOT / src.lstrip("/")) as im:
                self.assertEqual("%dx%d" % im.size, size)

    def test_the_server_serves_the_files_and_the_manifest_is_public(self):
        tree = ast.parse(MAIN)
        routes = {d.args[0].value for n in tree.body if isinstance(n, ast.FunctionDef) for d in n.decorator_list
                  if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "get" and d.args and isinstance(d.args[0], ast.Constant)}
        for path in ("/manifest.webmanifest", "/sw.js", "/pwa.js", "/app_icon_192.png", "/app_icon_512.png"):
            self.assertIn(path, routes)
            self.assertTrue((ROOT / path.lstrip("/")).exists(), path)
        public = MAIN.split("PUBLIC_PATHS = frozenset([", 1)[1].split("])", 1)[0]
        self.assertIn('"/manifest.webmanifest"', public)
        self.assertIn('"Service-Worker-Allowed": "/"', MAIN)

    def test_git_keeps_every_app_file(self):
        # .gitignore drops backup names like "name-123.*": an icon called app-icon-192.png would never reach the server
        import fnmatch
        rules = [l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
        for name in ("manifest.webmanifest", "sw.js", "pwa.js", "app_icon_192.png", "app_icon_512.png"):
            self.assertEqual([r for r in rules if fnmatch.fnmatch(name, r)], [], name)

    def test_the_main_pages_load_the_script(self):
        for page in ("landing.html", "index.html", "dub_long.html", "account.html", "pricing.html", "help.html", "dub_long_edit.html"):
            self.assertIn('<script src="/pwa.js" defer></script>', (ROOT / page).read_text(encoding="utf-8"), page)


@unittest.skipUnless(NODE, "node is needed")
class InstallOnlyOnAComputer(unittest.TestCase):
    RUN = r"""
const vm = require('vm'), fs = require('fs');
function run(wide) {
  const added = [], registered = [];
  const ctx = { window: { matchMedia: q => ({ matches: wide }) },
    document: { querySelector: () => null, createElement: () => ({}), head: { appendChild: e => added.push(e) } },
    navigator: { serviceWorker: { register: (u, o) => { registered.push([u, o.scope]); return Promise.resolve(); } } } };
  vm.runInNewContext(fs.readFileSync('pwa.js', 'utf8'), ctx);
  return { added: added.map(e => [e.rel, e.href, e.crossOrigin]), registered };
}
console.log(JSON.stringify({ computer: run(true), phone: run(false) }));
"""

    def test_a_computer_gets_the_manifest_and_the_service_worker_a_phone_gets_nothing(self):
        got = node(self.RUN)
        self.assertEqual(got["computer"]["added"], [["manifest", "/manifest.webmanifest", "use-credentials"]])
        self.assertEqual(got["computer"]["registered"], [["/sw.js", "/"]])
        self.assertEqual(got["phone"], {"added": [], "registered": []})
        self.assertIn("(min-width: 900px) and (pointer: fine)", (ROOT / "pwa.js").read_text(encoding="utf-8"))


@unittest.skipUnless(NODE, "node is needed")
class TheServiceWorkerKeepsNothing(unittest.TestCase):
    RUN = r"""
const vm = require('vm'), fs = require('fs');
const handlers = {};
class Resp { constructor(body, init) { this.body = body; this.status = init.status; } }
async function main() {
  const out = {};
  for (const [name, mode, online] of [['page_online', 'navigate', true], ['page_offline', 'navigate', false], ['api', 'cors', false], ['video', 'no-cors', true]]) {
    let responded = null, fetched = 0;
    const ctx = { self: { addEventListener: (t, f) => { handlers[t] = f; }, skipWaiting() {}, clients: { claim: () => Promise.resolve() } },
      fetch: () => { fetched++; return online ? Promise.resolve({ status: 200, body: 'server' }) : Promise.reject(new Error('offline')); },
      Response: Resp };
    vm.runInNewContext(fs.readFileSync('sw.js', 'utf8'), ctx);
    handlers.fetch({ request: { mode }, respondWith: p => { responded = p; } });
    const r = responded ? await responded : null;
    out[name] = r ? { status: r.status, offline_note: String(r.body).includes('You are offline'), fetched } : null;
  }
  console.log(JSON.stringify(out));
}
main();
"""

    def test_pages_come_from_the_server_and_only_a_missing_connection_shows_the_note(self):
        got = node(self.RUN)
        self.assertEqual(got["page_online"], {"status": 200, "offline_note": False, "fetched": 1})
        self.assertEqual(got["page_offline"]["status"], 503)
        self.assertTrue(got["page_offline"]["offline_note"])
        self.assertIsNone(got["api"])           # uploads, API calls and videos are not touched at all
        self.assertIsNone(got["video"])
        src = (ROOT / "sw.js").read_text(encoding="utf-8")
        self.assertNotIn("caches.", src)        # no stored copies: an update is always seen at once


if __name__ == "__main__":
    unittest.main()
