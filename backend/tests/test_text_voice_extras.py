"""Text to voice extras (Nov 2026): the pronunciation list, speed, subtitles (SRT), every line as its own MP3 (ZIP) and a text
read from a .txt, .docx or .srt file. Fake voice and fake ledger, real ffmpeg."""
import io
import re
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import text_voice as tv
from test_text_voice import UID, Base, mp3

ROOT = Path(__file__).resolve().parents[1]


class Ready(Base):
    def ready(self, text="Sara: Hello there.\nAdam: Good evening.\n\nSara: Goodbye."):
        job, _ = tv.new_job(UID, text, "en")
        job["prepared"] = True
        arabic = ["مَرْحَبًا يَا جُوجِل", "مَسَاءُ الخَيْرِ", "إِلَى اللِّقَاءِ"]
        for ln, a in zip(job["lines"], arabic):
            ln["arabic"], ln["emotion"] = a, "neutral"
        tv._save(job)
        tv.set_speakers(job, [{"id": "s1", "voice_id": "v1"}, {"id": "s2", "voice_id": "v2"}])
        return tv.load(job["id"])

    def speak(self, job, sent, seconds=0.6):
        tone = mp3(seconds)

        def tts(voice, text, waqf="auto", gap=None, delivery=None):
            sent.append(text)
            return tone, ""
        with mock.patch("longdub_service._tts_with_retry", tts):
            ok, err = tv.start_speak(tv.load(job["id"]))
            self.assertTrue(ok, err)
            return self.wait(job["id"])


class Pronunciation(Ready):
    def test_only_the_voice_copy_changes_and_only_those_lines_are_made_again(self):
        job = self.ready()
        sent = []
        job = self.speak(job, sent)
        ok, res = tv.set_options(job, {"pronunciation": "جوجل = غُوغِل\nnot understood\n"})
        self.assertEqual((ok, res), (True, {"bad": 1}))
        job = tv.load(job["id"])
        view = tv.public(job)
        self.assertEqual((view["pronunciation"], view["pronunciation_count"]), ("جوجل = غُوغِل", 1))
        self.assertEqual(job["lines"][0]["arabic"], "مَرْحَبًا يَا جُوجِل")           # the text stays as written
        self.assertEqual(view["lines_to_speak"], 1)
        price, chars = tv.speak_price(job)
        self.assertEqual(chars, len(tv._tag("neutral")) + len("مَرْحَبًا يَا غُوغِل"))
        sent.clear()
        job = self.speak(job, sent)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].endswith("مَرْحَبًا يَا غُوغِل"))
        self.assertTrue(tv.public(job)["audio_ready"])

    def test_words_are_found_with_or_without_marks_and_after_prefixes(self):
        job = {"pronunciation": tv.parse_pronunciation("احمد = أَحْمَد\nLisan = لِسَان")[0]}
        said = lambda a: tv._spoken_arabic(job, {"arabic": a})
        self.assertEqual(said("قَالَ أَحمد وَلِأحمد"), "قَالَ أَحْمَد وَلِأَحْمَد")
        self.assertEqual(said("أحمدي"), "أحمدي")                    # part of another word: left alone
        self.assertEqual(said("Lisan و lisan AI"), "لِسَان و لِسَان AI")
        entries, bad = tv.parse_pronunciation("a = b\n# note\nجوجل=غوغل\nجُوجِل = آخر\n" + "x" * 81 + " = y")
        self.assertEqual(([e["word"] for e in entries], bad), (["a", "جوجل"], 1))


class Speed(Ready):
    def test_the_speed_is_free_and_makes_no_voice_again(self):
        job = self.ready()
        sent = []
        job = self.speak(job, sent, seconds=1.2)
        normal = tv.public(job)["audio_seconds"]
        self.assertEqual(tv.set_options(job, {"speed": 3})[0], False)
        self.assertTrue(tv.set_options(job, {"speed": 1.2})[0])
        job = tv.load(job["id"])
        view = tv.public(job)
        self.assertTrue(view["audio_outdated"])
        self.assertEqual((view["speak_price"], view["lines_to_speak"], view["speed"]), (0, 0, 1.2))
        sent.clear()
        job = self.speak(job, sent)
        self.assertEqual(sent, [])
        self.assertLess(tv.public(job)["audio_seconds"], normal - 0.3)
        charges = len(self.ledger.charges)
        tv.set_lines(job, [{"id": "l2", "speed": 0.85}, {"id": "l3", "speed": 7}])
        job = tv.load(job["id"])
        self.assertEqual((job["lines"][1]["speed"], job["lines"][2]["speed"]), (0.85, None))
        self.assertEqual(tv._speed_of(job, job["lines"][1]), 0.85)
        self.assertEqual(tv._speed_of(job, job["lines"][2]), 1.2)
        self.assertEqual(len(self.ledger.charges), charges)

    def test_audio_made_before_speeds_existed_stays_current(self):
        job = self.ready()
        job = self.speak(job, [])
        job.pop("speed", None)
        self.assertTrue(tv.public(job)["audio_ready"])


class Downloads(Ready):
    def test_subtitles_follow_the_mp3(self):
        job = self.ready()
        self.assertIsNone(tv.subtitles(job))
        job = self.speak(job, [])
        srt = tv.subtitles(job)
        blocks = [b for b in srt.strip().split("\n\n") if b]
        self.assertEqual(len(blocks), 3)
        times = re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", srt)
        self.assertEqual(len(times), 3)
        self.assertEqual(times, sorted(times))
        self.assertIn("مرحبا يا جوجل", srt)                          # without the diacritics
        self.assertIn("مَرْحَبًا", tv.subtitles(job, marks=True))
        tv.set_lines(job, [{"id": "l2", "arabic": "مَسَاءُ النُّورِ"}])
        self.assertIsNone(tv.subtitles(tv.load(job["id"])))          # the MP3 no longer matches the text
        self.assertFalse(tv.public(tv.load(job["id"]))["subtitles_ready"])

    def test_a_long_line_is_cut_into_short_subtitles(self):
        pieces = tv._sub_pieces(" ".join(["كلمة"] * 60))
        self.assertGreater(len(pieces), 2)
        for p in pieces:
            self.assertLessEqual(len(p.split("\n")), 2)
            self.assertLessEqual(max(len(r) for r in p.split("\n")), tv.SUB_ROW + 6)

    def test_every_line_as_its_own_mp3(self):
        job = self.ready()
        job = self.speak(job, [])
        dest = Path(self.tmp.name) / "lines.zip"
        self.assertEqual(tv.lines_zip(job, dest), dest)
        with zipfile.ZipFile(dest) as z:
            names = z.namelist()
            self.assertEqual(names, ["001_Sara.mp3", "002_Adam.mp3", "003_Sara.mp3"])
            self.assertTrue(all(len(z.read(n)) > 1000 for n in names))
        self.assertFalse((tv._dir(job["id"]) / "zip_work").exists())


class SlowAddresseeAnswer(Base):
    def test_the_translation_waits_at_most_the_deadline(self):
        # 6 Nov 2026: "who is each you said to" took about 2.5 minutes; now the translation goes on without it
        job, _ = tv.new_job(UID, "Sara: Trace the call.\nAdam: You know who I am.", "en")
        job = tv.load(job["id"])
        rows = [{"segment_id": ln["id"], "start": float(i), "speaker": "x", "speaker_id": ln["speaker_id"], "text": ln["text"]}
                for i, ln in enumerate(job["lines"])]

        def slow(*a, **k):
            time.sleep(1.5)
            return {"l2": {"kind": "speaker"}}
        events = []
        with mock.patch.object(tv, "ASK_DEADLINE", 0.2), mock.patch("addressee_ask.ask", slow), mock.patch.object(tv, "GEMINI_API_KEY", "k"), \
             mock.patch.object(tv, "_ev", lambda job, step, status="ok", detail="": events.append((step, status, detail))):
            t0 = time.time()
            tv._ask_addressees(job, rows)
            self.assertLess(time.time() - t0, 1.0)
        self.assertNotIn("addressees", job)
        self.assertEqual([e[:2] for e in events], [("addressees_asked", "skipped")])

    def test_a_quick_answer_is_used_and_timed(self):
        job, _ = tv.new_job(UID, "Sara: Trace the call.\nAdam: You know who I am.", "en")
        job = tv.load(job["id"])
        rows = [{"segment_id": ln["id"], "start": float(i), "speaker": "x", "speaker_id": ln["speaker_id"], "text": ln["text"]}
                for i, ln in enumerate(job["lines"])]

        def quick(rows, todo, key, call, record=None):
            with mock.patch("gemini_service.call_gemini", lambda k, p, timeout=0: ({"modelVersion": "gemini-x"}, None)):
                call(key, {}, 240)
            return {"l2": {"kind": "unsure"}}
        events = []
        with mock.patch("addressee_ask.ask", quick), mock.patch.object(tv, "GEMINI_API_KEY", "k"), \
             mock.patch.object(tv, "_ev", lambda job, step, status="ok", detail="": events.append((step, status, detail))):
            tv._ask_addressees(job, rows)
        self.assertEqual(job["addressees"], {"l2": {"kind": "unsure"}})
        self.assertEqual(events[0][:2], ("addressees_asked", "ok"))
        self.assertIn("gemini-x", events[0][2])


class FromAFile(unittest.TestCase):
    def docx(self, paras):
        body = "".join("<w:p><w:r><w:t>%s</w:t></w:r><w:r><w:br/><w:t>x</w:t></w:r></w:p>" % p for p in paras)
        xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               "<w:body>" + body + "</w:body></w:document>")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", xml)
        return buf.getvalue()

    def test_txt_docx_and_srt(self):
        self.assertEqual(tv.read_text_file("a.txt", "Hello\r\n\r\n\r\n\r\nWorld".encode("utf-8")), ("Hello\n\nWorld", None))
        self.assertEqual(tv.read_text_file("a.txt", "مرحبا".encode("cp1256"))[0], "مرحبا")
        self.assertEqual(tv.read_text_file("a.txt", "Hi".encode("utf-16"))[0], "Hi")
        self.assertEqual(tv.read_text_file("Script.DOCX", self.docx(["Sara: Hi.", "مرحبا"]))[0], "Sara: Hi.\nx\nمرحبا\nx")
        srt = "1\n00:00:01,000 --> 00:00:02,000\n<i>Hello</i>\nthere\n\n2\n00:00:03,000 --> 00:00:04,500\nBye\n"
        self.assertEqual(tv.read_text_file("a.srt", srt.encode())[0], "Hello there\nBye")
        self.assertEqual(tv.read_text_file("a.vtt", ("WEBVTT\n\n00:01.000 --> 00:02.000\nHi\n").encode())[0], "Hi")

    def test_what_is_refused(self):
        self.assertIsNotNone(tv.read_text_file("a.pdf", b"%PDF")[1])
        self.assertIsNotNone(tv.read_text_file("a.txt", b"")[1])
        self.assertIsNotNone(tv.read_text_file("a.txt", b"x" * (tv.READ_FILE_MAX + 1))[1])
        self.assertIsNotNone(tv.read_text_file("a.docx", b"not a zip")[1])
        self.assertIsNotNone(tv.read_text_file("a.txt", b"   \n ")[1])


class Wiring(unittest.TestCase):
    def test_routes_and_page(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        for needle in ('@app.post("/api/text_voice/{job_id}/options")', '@app.get("/api/text_voice/{job_id}/subtitles")',
                       '@app.get("/api/text_voice/{job_id}/lines.zip")', '@app.post("/api/text_voice/read_file")'):
            self.assertIn(needle, main)
        self.assertLess(main.index('@app.post("/api/text_voice/read_file")'), main.index('@app.get("/api/text_voice/{job_id}/line/{line_id}")'))
        page = (ROOT / "text_voice.html").read_text(encoding="utf-8")
        for needle in ('id="pronText"', 'id="tvSpeed"', 'id="dlSrt"', 'id="dlZip"', 'id="tvFile"', "/api/text_voice/read_file",
                       '"/options"', "/subtitles", "/lines.zip"):
            self.assertIn(needle, page)
        for key in ("fileBtn", "pronHeading", "pronNote", "speedLabel", "speedNames", "dlSrt", "dlZip", "subsOld", "silent", "speakAgainFree"):
            self.assertEqual(len(re.findall(r"\b" + key + r":", page)), 2, key)


if __name__ == "__main__":
    unittest.main()
