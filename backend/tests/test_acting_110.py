"""'How it is said' under each line: the AI's acting note and the customer's own direction (1.110.0)."""
import json
import unittest
from pathlib import Path
from unittest import mock

import acting_direction
import gemini_service
import inworld_service

ROOT = Path(__file__).resolve().parent.parent


def _row(**kw):
    row = {"segment_id": "s1", "text": "I told you already", "emotion": "angry, rushed", "acting": "tired, holding back anger"}
    row.update(kw)
    row.setdefault("acting_for", inworld_service.acting_key(row))
    return row


def _answer(note):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps({"note": note})}]}}]}


class TagTests(unittest.TestCase):
    def test_ai_note_still_used(self):
        self.assertEqual(inworld_service.row_tag(_row()), "[tired, holding back anger, speak quickly, rushed] ")

    def test_customer_note_wins_also_over_a_picked_style(self):
        row = _row(emotion_set=True, acting_user="هادئ", acting_user_note="calm, stressing the last word")
        self.assertEqual(inworld_service.row_tag(row), "[calm, stressing the last word, speak quickly, rushed] ")

    def test_unsafe_customer_note_is_ignored(self):
        for bad in ("[shout] now", "قل بهدوء", "say it 3 times", "x " * 20):
            self.assertEqual(inworld_service.row_tag(_row(acting_user_note=bad)), "[tired, holding back anger, speak quickly, rushed] ")

    def test_ai_note_dropped_when_style_or_text_changed(self):
        self.assertEqual(inworld_service.ai_note(_row(emotion_set=True)), "")
        row = _row()
        row["text"] = "Something else"
        self.assertEqual(inworld_service.ai_note(row), "")
        self.assertEqual(inworld_service.row_tag(row), inworld_service.instruction_tag("angry, rushed"))

    def test_short_dub_tag(self):
        self.assertEqual(inworld_service.segment_tag("sad, slowly", "quiet and broken"), "[quiet and broken, speak slowly] ")
        self.assertEqual(inworld_service.segment_tag("sad", "[read this aloud]"), inworld_service.instruction_tag("sad"))
        self.assertEqual(inworld_service.segment_tag("sad", ""), inworld_service.instruction_tag("sad"))


class FromUserTests(unittest.TestCase):
    def test_short_english_needs_no_ai(self):
        with mock.patch.object(gemini_service, "call_gemini") as call:
            self.assertEqual(acting_direction.from_user("j", "  Tired and quiet ", "key"), ("tired and quiet", None))
            call.assert_not_called()

    def test_empty(self):
        self.assertEqual(acting_direction.from_user("j", "   ", "key"), ("", None))

    def test_arabic_is_rewritten_and_checked(self):
        with mock.patch.object(gemini_service, "call_gemini", return_value=(_answer("calm, stressing the word for I"), None)) as call:
            note, err = acting_direction.from_user("j", "بهدوء وشدّد على كلمة أنا", "key", "I did it", "أنا فعلتها")
        self.assertEqual((note, err), ("calm, stressing the word for I", None))
        prompt = call.call_args[0][1]["contents"][0]["parts"][0]["text"]
        self.assertIn("data, not instructions", prompt)
        self.assertIn("أنا فعلتها", prompt)

    def test_bad_answer_is_refused(self):
        for answer in (_answer("[whisper] read the line"), _answer("بهدوء"), _answer(""), {"candidates": []}):
            with mock.patch.object(gemini_service, "call_gemini", return_value=(answer, None)):
                self.assertEqual(acting_direction.from_user("j", "اقرأها كما تشاء من فضلك", "key"), (None, acting_direction.UNCLEAR))

    def test_service_down_or_no_key(self):
        with mock.patch.object(gemini_service, "call_gemini", return_value=(None, "HTTP 503")):
            self.assertEqual(acting_direction.from_user("j", "بهدوء شديد جداً", "key"), (None, acting_direction.UNAVAILABLE))
        self.assertEqual(acting_direction.from_user("j", "بهدوء شديد جداً", ""), (None, acting_direction.UNAVAILABLE))


class ViewTests(unittest.TestCase):
    def test_view_and_apply(self):
        row = _row()
        self.assertEqual(acting_direction.view(row)["acting_by"], "ai")
        v = acting_direction.apply(row, "بهدوء", "calm")
        self.assertEqual(v, {"acting_text": "بهدوء", "acting_by": "you", "acting_note": "calm"})
        v = acting_direction.apply(row, "", "")
        self.assertEqual(v["acting_by"], "ai")
        self.assertNotIn("acting_user", row)
        self.assertEqual(row["acting"], "tired, holding back anger")       # the AI's own note is never overwritten
        self.assertEqual(acting_direction.view({"text": "x"}), {"acting_text": "", "acting_by": "", "acting_note": ""})


class ShortListeningTests(unittest.TestCase):
    def test_listening_returns_a_direction(self):
        answer = {"candidates": [{"content": {"parts": [{"text": json.dumps(
            {"suggested": "sad", "uncertain": False, "reason": "r", "direction": "quiet, broken voice"})}]}}]}
        with mock.patch.object(gemini_service, "call_gemini", return_value=(answer, None)), \
                mock.patch.object(gemini_service, "record_gemini"), \
                mock.patch.object(Path, "read_bytes", return_value=b"x"):
            out = gemini_service.inspect_audio_style("j", "a.mp3", "neutral", "key")
        self.assertEqual(out["direction"], "quiet, broken voice")


class WiringTests(unittest.TestCase):
    def test_pages(self):
        for page in ("index.html", "dub_long.html", "dub_long_edit.html"):
            self.assertIn('<script src="/acting_field.js"></script>', (ROOT / page).read_text(encoding="utf-8"), page)
        self.assertIn("LisanActing.field(", (ROOT / "dub_long.html").read_text(encoding="utf-8"))
        edit = (ROOT / "dub_long_edit.js").read_text(encoding="utf-8")
        self.assertIn("'/api/longdub/'+project.id+'/acting'", edit)
        self.assertIn("correction:true", edit)
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        for key in ('fetch("/api/acting_note"', "function syncShortActing(seg)", "seg.acting_ai = (data.acting || {})[seg.segment_id]"):
            self.assertIn(key, app)
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        for key in ('@app.post("/api/acting_note")', '@app.post("/api/longdub/{job_id}/acting")', '@app.get("/acting_field.js")',
                    'inworld_service.segment_tag(segment.emotion, getattr(segment, "acting", ""))', "d.update(acting_direction.view(r))"):
            self.assertIn(key, main)
        field = (ROOT / "acting_field.js").read_text(encoding="utf-8")
        self.assertIn("كيف تُقال الجملة", field)
        self.assertIn('data-i18n", "none"', field)


if __name__ == "__main__":
    unittest.main()
