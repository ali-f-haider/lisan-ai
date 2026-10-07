"""Offline repository/media and customer-wording regression checks."""
import ast
import json
import os
import re
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from test_gemini_params import _is_backup_copy
from test_shortdub_upgrade import source_functions

ROOT = Path(__file__).resolve().parents[1]
# Customer-visible text only. Identifiers, URLs, model configuration, regexes,
# comments, admin pages and internal provider diagnostics are not UI labels.
VENDOR_WORDS = ("gemini", "elevenlabs", "inworld", "openai", "whisper", "demucs", "pyannote",
                "fal", "fal.ai", "stable audio", "stable-audio", "claude", "anthropic", "gpt",
                "hugging face", "hugging-face", "replicate", "deepseek", "minimax", "fish audio",
                "veed", "qwen", "dashscope", "alibaba", "wan", "wanx")
VENDOR = re.compile(r"\b(?:" + "|".join(map(re.escape, VENDOR_WORDS)) + r")\b", re.I)


def js_labels(source):
    # Consume comments and strings together so comment markers inside a URL do
    # not swallow subsequent labels. A model ID assignment is configuration.
    token = re.compile(r"""//[^\n]*|/\*[\s\S]*?\*/|(?<=[(=,:!?])\s*/(?:\\.|\[(?:\\.|[^\]\\])*\]|[^/\\\n])+/[a-z]*|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`""")
    for match in token.finditer(source):
        raw = match.group()
        if raw.lstrip().startswith('/'):
            continue
        value = raw[1:-1]
        if not VENDOR.search(value) or re.match(r'https?://', value):
            continue
        before = source[max(0, match.start()-180):match.start()]
        if re.search(r'(?:textContent|innerHTML|placeholder|title|notify|new Error|\btr)\s*(?:=|\(|\+=)[^;]*$', before) or re.search(r'\s', value):
            yield source.count('\n', 0, match.start()) + 1, value


class PageLabels(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = []
        self.script = False
        self.labels = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden.append(tag)
            self.script = tag == 'script'
        for key, value in attrs:
            if key in ('title', 'alt', 'placeholder', 'aria-label') and value:
                self.labels.append((self.getpos()[0], value))

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()
            self.script = False

    def handle_data(self, value):
        if self.script:
            self.labels.extend((self.getpos()[0]+n-1, text) for n, text in js_labels(value))
        elif not self.hidden:
            self.labels.append((self.getpos()[0], value))


def python_labels(source):
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for literal in ast.walk(tree):
        if not isinstance(literal, ast.Constant) or not isinstance(literal.value, str) or not VENDOR.search(literal.value):
            continue
        chain, node = [], literal
        while node in parents:
            node = parents[node]; chain.append(node)
        function = next((n for n in chain if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
        if function is None:
            continue
        routes = [d.args[0].value for d in function.decorator_list if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)]
        if any('/admin' in str(p) or p == '/debug-keys' for p in routes):
            continue
        if any(isinstance(n, ast.Compare) for n in chain):
            continue  # engine IDs compared in code are not labels
        calls = [ast.unparse(n.func) for n in chain if isinstance(n, ast.Call)]
        if calls and calls[0] in ('print', '_ev', 'Hooks.log_event'):
            continue
        visible = ('JSONResponse', '_fail', 'ld._fail', '_mark', 'ld._mark', '_merge_say', 'Hooks.send_email', 'ld.Hooks.send_email')
        if any(c in visible for c in calls):
            yield literal.lineno, literal.value
        elif routes and any(isinstance(n, ast.Return) for n in chain):
            if not any(isinstance(n, ast.Dict) and literal in n.keys for n in chain):
                yield literal.lineno, literal.value


def customer_vendor_hits(root):
    hits = []
    excluded = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'tests', 'uploads', 'outputs', '.local_data'}
    files = []
    for folder, directories, names in os.walk(root):
        directories[:] = [d for d in directories if d.lower() not in excluded and 'backup' not in d.lower()]
        files.extend(Path(folder)/n for n in names)
    for path in sorted(files):
        rel = path.relative_to(root)
        if not path.is_file() or path.suffix.lower() not in ('.html', '.js', '.css', '.py'):
            continue
        if _is_backup_copy(path) or any(p.lower() in excluded or 'backup' in p.lower() for p in rel.parts[:-1]):
            continue
        if path.name.lower().startswith(('admin', 'test_', 'check_', 'patch_', 'fix_', 'add_', 'apply_', 'update_')):
            continue
        source = path.read_text(encoding='utf-8-sig', errors='replace')
        if path.suffix == '.html':
            parser = PageLabels(); parser.feed(source); labels = parser.labels
        elif path.suffix == '.js':
            labels = js_labels(source)
        elif path.suffix == '.css':
            clean = re.sub(r'/\*[\s\S]*?\*/', '', source)
            labels = [(clean.count('\n', 0, m.start())+1, m.group(1)) for m in re.finditer(r'content\s*:\s*["\'](.*?)["\']', clean)]
        else:
            labels = python_labels(source)
        hits.extend(f'{rel.as_posix()}:{n}: {text[:130]}' for n, text in labels if VENDOR.search(text))
    return hits


class BackupRulesTests(unittest.TestCase):
    def test_git_rules_match_the_scanner_and_leave_real_names_visible(self):
        names = ('app-1.js', 'app-123456.min.js', 'page_old.html', 'page_OLD.min.js', 'page.js.BAK',
                 'page.js.orig', 'app.js', 'app-1beta.js', 'app-1234567.js', 'v2.js', 'real_module.py')
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            subprocess.run(['git', 'init', '-q', str(cwd)], check=True, capture_output=True)
            (cwd/'.gitignore').write_bytes((ROOT/'.gitignore').read_bytes())
            result = subprocess.run(['git', 'check-ignore', '--no-index', '--stdin', '-z'], cwd=cwd,
                                    input=('\0'.join(names)+'\0').encode(), capture_output=True)
            self.assertIn(result.returncode, (0, 1), result.stderr)
            ignored = set(result.stdout.decode().strip('\0').split('\0'))
            self.assertEqual(ignored, {n for n in names if _is_backup_copy(Path(n))})
            self.assertNotIn('app-1beta.js', ignored)

    def test_no_currently_tracked_backend_file_matches_an_ignore_rule(self):
        git = ['git', '-c', 'safe.directory='+ROOT.parent.as_posix()]
        files = subprocess.check_output(git+['ls-files', '-z', '--', '.'], cwd=ROOT).decode().split('\0')
        result = subprocess.run(git+['check-ignore', '--no-index', '--stdin', '-z'], cwd=ROOT,
                                input='\0'.join(f for f in files if f)+'\0', text=True, capture_output=True)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        self.assertEqual(result.stdout, '', 'Ignore rules would hide tracked source files')


class MissingMediaTests(unittest.TestCase):
    def test_removed_demo_urls_and_missing_static_media_return_404(self):
        from fastapi import FastAPI
        from fastapi.staticfiles import StaticFiles
        from fastapi.testclient import TestClient
        app = FastAPI()
        source = (ROOT/'main.py').read_text(encoding='utf-8-sig')
        # Install any demo handlers still present, so a reintroduced broken
        # FileResponse fails through an actual ASGI request (not a string check).
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and any(isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant) and d.args[0].value in ('/demo_before.mp4', '/demo_after.mp4') for d in node.decorator_list):
                from fastapi.responses import FileResponse
                exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), {'app':app, 'BASE_DIR':ROOT, 'FileResponse':FileResponse})
        with tempfile.TemporaryDirectory() as directory:
            app.mount('/static', StaticFiles(directory=directory))
            client = TestClient(app, raise_server_exceptions=False)
            for url in ('/demo_before.mp4', '/demo_after.mp4', '/static/missing.mp4'):
                with self.subTest(url=url): self.assertEqual(client.get(url).status_code, 404)


class CustomerWordingTests(unittest.TestCase):
    def test_customer_visible_sources_have_no_vendor_or_model_names(self):
        self.assertEqual(customer_vendor_hits(ROOT), [])

    def test_scanner_ignores_backups_comments_and_identifiers_but_catches_real_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'app-1.js').write_text('notify("error", "Gemini failed");')
            (root/'page_old.html').write_text('<p>Inworld unavailable</p>')
            (root/'app.js').write_text('// Gemini internal comment\nconst ENGINE = "inworld";')
            self.assertEqual(customer_vendor_hits(root), [])
            for filename, text in (('page.html', '<p>Gemini is unavailable</p>'),
                                   ('app.js', 'notify("error", "Inworld failed");'),
                                   ('main.py', '@app.get("/api/work")\ndef work():\n    return JSONResponse({"error":"OpenAI failed"})\n')):
                path = root/filename; path.write_text(text)
                self.assertTrue(any(h.startswith(filename+':') for h in customer_vendor_hits(root)), filename)
                path.unlink()

    def test_nested_service_diagnostics_are_hidden_and_customer_text_is_unchanged(self):
        from user_errors import customer_payload
        value = {'name':'My Gemini documentary', 'segments':[{'arabic_text':'Inworld', 'text':'OpenAI'}],
                 'history':[{'error':'Inworld error 500', 'warnings':['Gemini token quota exceeded'],
                             'result':{'music_note':'no fal.ai key set'}}],
                 'cloned_voices':{'Speaker 1':'ERROR: ElevenLabs voice failed', 'Speaker 2':'voice-id'}}
        encoded_before = json.dumps(value)
        safe = customer_payload(value)
        self.assertEqual(safe['name'], value['name'])
        self.assertEqual(safe['segments'], value['segments'])
        self.assertEqual(safe['cloned_voices']['Speaker 2'], 'voice-id')
        self.assertTrue(safe['cloned_voices']['Speaker 1'].startswith('ERROR: '))
        self.assertIsNone(VENDOR.search(json.dumps(safe['history'])))
        self.assertEqual(json.dumps(value), encoded_before)

    def test_short_progress_copies_and_sanitizes_diagnostics_without_paths(self):
        from user_errors import customer_payload
        env = {'customer_payload':customer_payload, '_PRIVATE_PROGRESS_KEYS':('error_trace','background_path','generation_id')}
        source_functions('main.py', ['_public_progress'], env)
        original = {'status':'error', 'error':'Gemini quota exceeded', 'error_trace':'secret',
                    'result':{'final_file':'private.mp4', 'warnings':['Inworld failed']}}
        result = env['_public_progress'](original)
        self.assertNotIn('error_trace', result)
        self.assertNotIn('final_file', result['result'])
        self.assertIsNone(VENDOR.search(json.dumps(result)))
        self.assertEqual(original['error'], 'Gemini quota exceeded')


    def test_correction_failure_email_has_only_customer_wording(self):
        import threading
        from types import SimpleNamespace as NS
        from unittest.mock import Mock
        # Use the exact raise statement that propagates the provider failure in
        # longdub_edits.run, then the actual protected failure-email function.
        tree = ast.parse((ROOT/'longdub_edits.py').read_text(encoding='utf-8-sig'))
        statement = next(n for n in ast.walk(tree) if isinstance(n, ast.Raise)
                         and 'A selected line could not be generated:' in ast.unparse(n))
        try:
            exec(compile(ast.Module(body=[statement], type_ignores=[]), 'longdub_edits.py', 'exec'),
                 {'err':'Inworld error 503: service unavailable'})
        except ValueError as ex:
            message = str(ex)
        hooks = NS(send_email=Mock(), refund=Mock(return_value=True))
        env = {'Hooks':hooks, '_lock_for':lambda _:threading.RLock(), '_save':Mock(),
               '_ev':Mock(), '_delete_pending_voices':Mock()}
        source_functions('longdub_service.py', ['_fail', '_ops', '_refunded_of', '_paid_key', '_sync_paid', '_remaining',
                                               '_music_slots', '_refund'], env)
        env['_fail']({'id':'job','uid':'user','filename':'clip.mp4','paid':{'dub':7}}, message, 'dub')
        self.assertIsNone(VENDOR.search(hooks.send_email.call_args.args[2]),
                          'Protected correction failures must sanitize their diagnostic before emailing it')

    def test_javascript_regexes_and_comments_are_not_customer_labels(self):
        source = 'const internal = /[`\'\"]Gemini/; // Inworld debug\nnotify("error", "OpenAI failed");'
        self.assertEqual([text for _,text in js_labels(source)], ['OpenAI failed'])

    def test_long_quote_diagnostics_are_sanitized_at_the_route_boundary(self):
        from types import SimpleNamespace as NS
        from user_errors import customer_message, customer_payload
        tree = ast.parse((ROOT/'main.py').read_text(encoding='utf-8-sig'))
        route = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and any(
            isinstance(d,ast.Call) and d.args and isinstance(d.args[0],ast.Constant)
            and d.args[0].value == '/api/longdub/{job_id}/preview' for d in n.decorator_list))
        env = {'customer_message':customer_message,'customer_payload':customer_payload,
               '_ld_job':lambda *args:('user',{'status':'editing'},None), 'get_credits':lambda _:100,
               'longdub_service':NS(ensure_tashkeel=lambda _:(0,None), dub_price=lambda _:{'due':7},
                                   music_quote=lambda _:{'note':'no fal.ai key set','repairs':0})}
        source_functions('main.py', [route.name], env)
        self.assertIsNone(VENDOR.search(json.dumps(env[route.name]('job',object()))))


if __name__ == '__main__':
    unittest.main()
