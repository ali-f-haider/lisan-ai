"""The three publishing-pack routes in main.py, run for real against a temporary project with the outside world faked."""
import ast
import json
import shutil
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import publish_pack as real_pp

MAIN = (BACKEND / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(MAIN)


def source_of(name):
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return ast.get_source_segment(MAIN, node)
    raise AssertionError(f"{name} not found in main.py")


class Resp:
    def __init__(self, body, status_code=200):
        self.body, self.status_code = body, status_code


class FileResp:
    def __init__(self, path, media_type=None, filename=None, background=None):
        self.path, self.media_type, self.filename, self.background = path, media_type, filename, background
        self.status_code = 200


class App:
    def get(self, *a, **k):
        return lambda f: f

    def post(self, *a, **k):
        return lambda f: f


class BaseModel:
    pass


ROWS = [{"start": i * 40, "end": i * 40 + 30, "arabic_text": f"سطر عربي رقم {i} للاختبار", "text": f"line {i}"} for i in range(30)]
GOOD = json.dumps({"title": "عنوان", "description": "وصف قصير.", "tags": ["علوم"], "hashtags": ["علوم", "تقنية", "فيديو"],
                   "chapters": [{"t": 0, "title": "أ"}, {"t": 200, "title": "ب"}, {"t": 600, "title": "ج"}]}, ensure_ascii=False)


class World:
    def __init__(self, status="done", with_result=True, limited=False, uid_ok=True, answer=GOOD):
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / "proj"
        self.project.mkdir()
        self.result = self.tmp / "dub.m4a"
        if with_result:
            self.result.write_bytes(b"a" * 4000)
        self.job = {"id": "job-1", "uid": "u1", "status": status, "filename": "My Video ö.mp4", "source_language": "en", "duration": 1200}
        self.charged = []
        self.calls = []
        self.limited = limited
        self.uid_ok = uid_ok
        self.answer = answer
        world = self

        class LS:
            @staticmethod
            def job_dir(_id): return world.project
            @staticmethod
            def read_segments(job): return ROWS
            @staticmethod
            def result_file(job): return world.result if world.result.exists() else None
            @staticmethod
            def _total_secs(job): return 1200.0

        def fake_call(key, payload, timeout=0):
            world.calls.append(payload)
            if world.answer is None:
                return None, "down"
            return {"candidates": [{"content": {"parts": [{"text": world.answer}]}}]}, None

        pp = types.SimpleNamespace(**{k: getattr(real_pp, k) for k in dir(real_pp) if not k.startswith("__")})
        pp.generate = lambda *a, **k: real_pp.generate(*a, call=fake_call, record=lambda *x: None, **k)

        def fake_prepare(result, work, run):
            return real_pp.prepare_audio(result, work, lambda cmd: (_ for _ in ()).throw(Exception("no ffmpeg")))

        def ld_job(request, job_id):
            if not world.uid_ok:
                return None, None, Resp({"error": "login"}, 401)
            if job_id != "job-1":
                return "u1", None, Resp({"error": "nf"}, 404)
            return "u1", world.job, None

        self.ns = {
            "app": App(), "BaseModel": BaseModel, "Request": object, "Path": Path, "shutil": shutil,
            "JSONResponse": Resp, "FileResponse": FileResp, "customer_message": lambda m: m, "_RATE_LIMIT_MSG": "slow",
            "_rate_limited": lambda *a, **k: world.limited, "_ld_job": ld_job, "longdub_service": LS,
            "publish_pack": pp, "languages": types.SimpleNamespace(get=lambda c: types.SimpleNamespace(english_name="English")),
            "GEMINI_API_KEY": "key", "_time": __import__("time"), "subs_export": __import__("types").SimpleNamespace(
                export=lambda rows, lang, fmt: ("1\n00:00:00,000 --> 00:00:01,000\nx\n" if fmt == "srt" else "WEBVTT\n", len(rows))),
            "ffmpeg_utils": types.SimpleNamespace(run_ffmpeg=lambda cmd: (_ for _ in ()).throw(Exception("no ffmpeg"))),
            "_ld_charge": lambda *a, **k: world.charged.append(a),
        }
        for name in ("LongDubPublishPack", "_publish_pack_view", "longdub_publish_pack_get", "longdub_publish_pack_make", "longdub_publish_pack_zip"):
            exec(source_of(name), self.ns)

    def body(self, title="Original", description="", keywords=""):
        return types.SimpleNamespace(title=title, description=description, keywords=keywords)

    def done(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class Routes(unittest.TestCase):
    def test_get_before_and_after(self):
        w = World()
        self.assertEqual(w.ns["longdub_publish_pack_get"]("job-1", None), {"saved": False})
        w.ns["longdub_publish_pack_make"]("job-1", w.body(), None)
        r = w.ns["longdub_publish_pack_get"]("job-1", None)
        self.assertTrue(r["saved"])
        self.assertEqual(r["input"]["title"], "Original")
        self.assertEqual(r["pack"]["title"], "عنوان")
        w.done()

    def test_make_is_free_and_saves(self):
        w = World()
        r = w.ns["longdub_publish_pack_make"]("job-1", w.body("Orig", "d", "k"), None)
        self.assertTrue(r["saved"])
        self.assertEqual(w.charged, [])
        self.assertTrue((w.project / "publish_pack.json").exists())
        self.assertEqual(len(r["pack"]["hashtags"]), 3)
        self.assertIn("ORIGINAL TITLE: <<<Orig>>>", w.calls[0]["contents"][0]["parts"][0]["text"])
        w.done()

    def test_make_never_charges_in_source(self):
        src = source_of("longdub_publish_pack_make") + source_of("longdub_publish_pack_zip")
        for word in ("_ld_charge", "spend_credits", "deduct", "charge("):
            self.assertNotIn(word, src)

    def test_guards(self):
        w = World(status="editing")
        self.assertEqual(w.ns["longdub_publish_pack_make"]("job-1", w.body(), None).status_code, 409)
        w.done()
        w = World()
        self.assertEqual(w.ns["longdub_publish_pack_make"]("job-1", w.body("  "), None).status_code, 400)
        self.assertEqual(w.ns["longdub_publish_pack_make"]("other", w.body(), None).status_code, 404)
        w.done()
        w = World(uid_ok=False)
        self.assertEqual(w.ns["longdub_publish_pack_make"]("job-1", w.body(), None).status_code, 401)
        self.assertEqual(w.ns["longdub_publish_pack_get"]("job-1", None).status_code, 401)
        self.assertEqual(w.ns["longdub_publish_pack_zip"]("job-1", None).status_code, 401)
        w.done()
        w = World(limited=True)
        self.assertEqual(w.ns["longdub_publish_pack_make"]("job-1", w.body(), None).status_code, 429)
        self.assertEqual(w.ns["longdub_publish_pack_zip"]("job-1", None).status_code, 429)
        w.done()

    def test_ai_failures(self):
        w = World(answer=None)
        r = w.ns["longdub_publish_pack_make"]("job-1", w.body(), None)
        self.assertEqual(r.status_code, 503)
        self.assertFalse((w.project / "publish_pack.json").exists())
        w.done()
        w = World(answer="nonsense")
        self.assertEqual(w.ns["longdub_publish_pack_make"]("job-1", w.body(), None).status_code, 503)
        w.done()

    def test_zip_needs_texts_first(self):
        w = World()
        self.assertEqual(w.ns["longdub_publish_pack_zip"]("job-1", None).status_code, 404)
        w.done()

    def test_zip_audio_result(self):
        w = World()
        w.ns["longdub_publish_pack_make"]("job-1", w.body(), None)
        r = w.ns["longdub_publish_pack_zip"]("job-1", None)
        self.assertEqual(r.media_type, "application/zip")
        self.assertTrue(r.filename.endswith("_youtube_pack.zip"))
        self.assertTrue(r.filename.isascii())
        with zipfile.ZipFile(r.path) as z:
            self.assertEqual(sorted(z.namelist()), ["dubbed_audio_ar.m4a", "publishing_pack.txt", "subtitles_ar.srt", "subtitles_ar.vtt"])
        work = Path(r.path).parent
        self.assertTrue(work.exists())
        r.background.func(*r.background.args, **r.background.kwargs)
        self.assertFalse(work.exists())
        w.done()

    def test_zip_without_result_file(self):
        w = World(with_result=False)
        w.ns["longdub_publish_pack_make"]("job-1", w.body(), None)
        r = w.ns["longdub_publish_pack_zip"]("job-1", None)
        with zipfile.ZipFile(r.path) as z:
            self.assertEqual(sorted(z.namelist()), ["publishing_pack.txt", "subtitles_ar.srt", "subtitles_ar.vtt"])
            self.assertIn("expired", z.read("publishing_pack.txt").decode("utf-8-sig"))
        shutil.rmtree(Path(r.path).parent, ignore_errors=True)
        w.done()

    def test_routes_registered_and_owner_guarded(self):
        for route in ('"/api/longdub/{job_id}/publish_pack"', '"/api/longdub/{job_id}/publish_pack.zip"'):
            self.assertIn(route, MAIN)
        for fn in ("longdub_publish_pack_get", "longdub_publish_pack_make", "longdub_publish_pack_zip"):
            self.assertIn("_ld_job(request, job_id)", source_of(fn))
        self.assertIn("import publish_pack", MAIN)


if __name__ == "__main__":
    unittest.main()
