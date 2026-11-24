"""EU "AI MODIFIED" label and the hidden AI note (ai_label.py, watermark.finish_video, 1.103.0)."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_label
import watermark

ROOT = Path(__file__).resolve().parent.parent
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
UID = "0a1b2c3d-1111-2222-3333-444455556666"


class StoreTests(unittest.TestCase):
    def test_off_by_default_then_saved_then_forgotten(self):
        with tempfile.TemporaryDirectory() as t, patch.object(ai_label, "_FOLDER", Path(t)):
            self.assertEqual(ai_label.get(UID), "")
            self.assertEqual(ai_label.set_(UID, "tr"), "tr")
            self.assertEqual(ai_label.get(UID), "tr")
            self.assertEqual(ai_label.set_(UID, ""), "")
            self.assertEqual(ai_label.get(UID), "")
            ai_label.set_(UID, "bl")
            ai_label.forget(UID)
            self.assertEqual(ai_label.get(UID), "")

    def test_bad_values_are_never_stored(self):
        with tempfile.TemporaryDirectory() as t, patch.object(ai_label, "_FOLDER", Path(t)):
            self.assertEqual(ai_label.set_(UID, "middle"), "")
            self.assertIsNone(ai_label.set_("../../etc", "tl"))
            self.assertEqual(ai_label.get("../../etc"), "")
            (Path(t) / f"{UID}.json").write_text(json.dumps({"corner": "x"}))
            self.assertEqual(ai_label.get(UID), "")


def _tags(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
                       capture_output=True, text=True)
    return {k.lower(): v for k, v in (json.loads(r.stdout or "{}").get("format", {}).get("tags") or {}).items()}


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class FinishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = tempfile.TemporaryDirectory()
        d = Path(cls.t.name)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-f", "lavfi",
                        "-i", "sine=f=440", "-t", "2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(d / "v.mp4")], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine", "-t", "2", str(d / "a.mp3")], check=True)
        cls.logo = d / "logo.png"
        shutil.copy(ROOT / "static" / "eu_ai_modified.png", cls.logo)     # any RGBA PNG does as a logo

    @classmethod
    def tearDownClass(cls):
        cls.t.cleanup()

    def _copy(self, name):
        dst = Path(self.t.name) / f"run_{self._testMethodName}{Path(name).suffix}"
        shutil.copy(Path(self.t.name) / name, dst)
        return dst

    def test_icon_file_is_shipped(self):
        self.assertTrue(watermark.LABEL.is_file())
        self.assertEqual(watermark._png_size(watermark.LABEL), (640, 139))

    def test_nothing_asked_still_adds_the_hidden_note_without_re_encoding(self):
        f = self._copy("v.mp4")
        r = watermark.finish_video(f)
        self.assertEqual((r["ok"], r["logo"], r["label"]), (True, False, False))
        self.assertIn("AI-modified", _tags(f).get("comment", ""))

    def test_label_and_logo_together(self):
        f = self._copy("v.mp4")
        with patch.object(watermark, "LOGO", self.logo):
            r = watermark.finish_video(f, logo=True, label="br")
        self.assertEqual((r["ok"], r["logo"], r["label"]), (True, True, True), r)
        self.assertIn("AI-modified", _tags(f).get("comment", ""))

    def test_each_corner(self):
        for c in ai_label.CORNERS:
            f = self._copy("v.mp4")
            r = watermark.finish_video(f, label=c)
            self.assertTrue(r["ok"] and r["label"] and not r["logo"], (c, r))

    def test_audio_only_gets_the_note(self):
        f = self._copy("a.mp3")
        r = watermark.finish_video(f, logo=True, label="tl")
        self.assertEqual((r["ok"], r["logo"], r["label"]), (True, False, False))
        self.assertIn("AI-modified", _tags(f).get("comment", ""))

    def test_failure_leaves_the_file_alone(self):
        f = Path(self.t.name) / "broken.mp4"
        f.write_bytes(b"not a video")
        r = watermark.finish_video(f, label="tl")
        self.assertFalse(r["ok"])
        self.assertEqual(f.read_bytes(), b"not a video")


class WiringTests(unittest.TestCase):
    def test_every_video_path_uses_the_one_finishing_step(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("watermark.finish_video(final, logo=_wm_logo, label=_wm_label)", main)
        self.assertIn("ai_label=label)", main)
        self.assertIn("ai_label=ai_label.get", main)
        self.assertIn("ai_label.forget(uid)", main)
        self.assertIn('@app.post("/api/ai_label")', main)
        self.assertIn("_wmk.finish_video(final_video, logo=bool(watermark), label=ai_label)",
                      (ROOT / "lipsync_service.py").read_text(encoding="utf-8"))
        self.assertIn("_wmk.finish_video(tmp_final, logo=want_logo, label=want_label)",
                      (ROOT / "longdub_service.py").read_text(encoding="utf-8"))
        self.assertIn("comment={_AI_TAG}", (ROOT / "text_voice.py").read_text(encoding="utf-8"))

    def test_account_page_has_both_languages(self):
        acc = (ROOT / "account.html").read_text(encoding="utf-8")
        for key in ("aiLabelSelect", "loadAiLabel();", "/static/eu_ai_modified.png", "شارة الذكاء الاصطناعي الأوروبية"):
            self.assertIn(key, acc)


if __name__ == "__main__":
    unittest.main()
