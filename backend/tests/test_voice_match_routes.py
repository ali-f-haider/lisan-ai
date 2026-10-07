"""The Auto-Assign route: guarded, limited, and answering with voice ids only."""
import os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_match_routes as routes
import voice_match_service as svc


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); os.environ["DATA_DIR"] = self.tmp.name
        svc.reset_cache()
        self.app = FastAPI()
        self.deny = False; self.limited = False; self.audio = "a.wav"; self.listen_calls = []
        self.voices = [dict(voice_id="m_old", gender="male", age="old"), dict(voice_id="m_young", gender="male", age="young")]
        routes.register(self.app, job_guard=lambda request, job_id, allow_empty=True: JSONResponse({"error": "not found"}, status_code=404) if self.deny else None,
                        rate_limited=lambda *a: self.limited, rate_message="slow down", resolve_audio=lambda j: self.audio,
                        fetch_voices=lambda: {"voices": self.voices}, gemini_key=lambda: "k",
                        listener=lambda job, mp3, key: self.listen_calls.append(key) or {"gender": "male", "age": "senior", "tone": [], "uncertain": False})
        self.c = TestClient(self.app)
        self.body = dict(job_id="j", speakers=[dict(name="A")], segments=[dict(speaker="A", start=0, end=4)])
        self.cut = patch.object(routes, "_cut", lambda src, s, d, out: self.write(out, d)); self.cut.start()

    def write(self, out, d):
        if str(out).endswith(".mp3"):
            Path(out).write_bytes(b"x"); return
        import numpy as np, wave
        t = np.arange(int(d * 16000)) / 16000
        x = sum((1.0 / h) * np.sin(2 * np.pi * 105 * h * t) for h in range(1, 12)); x = x / np.abs(x).max() * 0.4
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes((x * 32767).astype("<i2").tobytes())

    def tearDown(self):
        self.cut.stop(); os.environ.pop("DATA_DIR", None); self.tmp.cleanup(); svc.reset_cache()

    def test_a_match_returns_voice_ids_for_each_speaker(self):
        r = self.c.post("/api/voices/match", json=self.body)
        self.assertEqual(r.status_code, 200)
        a = r.json()["speakers"]["A"]
        self.assertEqual((a["best"], a["age"], a["gender"]), ("m_old", "senior", "male"))
        self.assertEqual(self.listen_calls, ["k"])
        self.assertNotIn("name", a["options"][0])                          # ids and reasons only; no voice names

    def test_someone_elses_job_is_refused_before_any_work(self):
        self.deny = True
        self.assertEqual(self.c.post("/api/voices/match", json=self.body).status_code, 404)
        self.assertEqual(self.listen_calls, [])

    def test_the_rate_limit_stops_a_flood_of_listening_calls(self):
        self.limited = True
        self.assertEqual(self.c.post("/api/voices/match", json=self.body).status_code, 429)
        self.assertEqual(self.listen_calls, [])

    def test_listening_can_be_switched_off_for_a_request_or_for_the_server(self):
        self.c.post("/api/voices/match", json=dict(self.body, listen=False))
        with patch.dict(os.environ, {"VOICE_MATCH_LISTEN": "off"}):
            r = self.c.post("/api/voices/match", json=self.body)
        self.assertEqual(self.listen_calls, [])
        self.assertEqual(r.json()["listener"], "off")

    def test_bad_sizes_missing_audio_and_an_empty_library(self):
        self.assertEqual(self.c.post("/api/voices/match", json=dict(self.body, speakers=[])).status_code, 400)
        self.assertEqual(self.c.post("/api/voices/match", json=dict(self.body, speakers=[dict(name=str(i)) for i in range(13)])).status_code, 400)
        self.audio = None
        self.assertEqual(self.c.post("/api/voices/match", json=self.body).status_code, 404)
        self.audio = "a.wav"; self.voices = []; svc.reset_cache()
        self.assertEqual(self.c.post("/api/voices/match", json=self.body).status_code, 503)

    def test_an_unexpected_error_gives_a_plain_message(self):
        with patch.object(svc, "match", side_effect=RuntimeError("secret internal detail")):
            r = self.c.post("/api/voices/match", json=self.body)
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("secret", r.text)


class WiringTests(unittest.TestCase):
    """main.py really registers the route, with names that exist before the call."""
    def test_main_registers_the_route_with_names_defined_earlier(self):
        import ast
        src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        call = next((n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "register" and getattr(n.func.value, "id", "") == "voice_match_routes"), None)
        self.assertIsNotNone(call, "main.py must call voice_match_routes.register")
        wanted = {n.id for k in call.keywords for n in ast.walk(k.value) if isinstance(n, ast.Name)}
        wanted.discard("app")
        bound = set()
        for node in tree.body:
            if getattr(node, "lineno", 0) >= call.lineno:
                break
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                bound.update((a.asname or a.name).split(".")[0] for a in node.names)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    bound.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name))
        self.assertEqual(sorted(wanted - bound - {"lambda"}), [])


if __name__ == "__main__":
    unittest.main()
