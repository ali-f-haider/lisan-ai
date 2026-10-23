import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import publish_pack as pp


def ar(n):
    return f"سطر عربي رقم {n} للاختبار"


ROWS = [{"start": i * 30, "arabic_text": ar(i)} for i in range(40)]


def answer(**over):
    d = {"title": "عنوان الفيديو", "description": "وصف أول.\n\nوصف ثان.", "tags": ["علوم", "تقنية"],
         "hashtags": ["علوم", "#تقنية", "ذكاء اصطناعي", "رابع"],
         "chapters": [{"t": 0, "title": "البداية"}, {"t": 120, "title": "الجزء الثاني"}, {"t": 400, "title": "الجزء الثالث"}, {"t": 900, "title": "الختام"}]}
    d.update(over)
    return json.dumps(d, ensure_ascii=False)


class Input(unittest.TestCase):
    def test_needs_title(self):
        self.assertIsNone(pp.clean_input("   ", "x"))
        self.assertIsNone(pp.clean_input(None))

    def test_trims_and_strips(self):
        r = pp.clean_input("A\x00  B <<<x>>>", "l1\n\n\n\nl2", "k " * 500)
        self.assertEqual(r["title"], "A B x")
        self.assertEqual(r["description"], "l1\n\nl2")
        self.assertLessEqual(len(r["keywords"]), pp.IN_KW_MAX)

    def test_long_title_cut(self):
        self.assertEqual(len(pp.clean_input("x" * 999)["title"]), pp.IN_TITLE_MAX)


class Lines(unittest.TestCase):
    def test_spread_and_cap(self):
        rows = [{"start": i, "arabic_text": ar(i)} for i in range(500)]
        c = pp.candidates(rows)
        self.assertEqual(len(c), pp.CANDIDATES)
        self.assertEqual(c[0]["t"], 0)
        self.assertGreater(c[-1]["t"], 400)

    def test_skips_bad_rows(self):
        c = pp.candidates([None, {"start": "x", "arabic_text": ar(1)}, {"start": 3, "arabic_text": "كلمة"}, {"start": -1, "arabic_text": ar(2)}, {"start": 5, "arabic_text": ar(3)}])
        self.assertEqual([x["t"] for x in c], [5])

    def test_strips_vowel_marks(self):
        c = pp.candidates([{"start": 1, "arabic_text": "مَرْحَباً بِكُمْ"}])
        self.assertEqual(c[0]["text"], "مرحبا بكم")
        self.assertNotIn("َ", c[0]["text"])

    def test_prompt_marks_data(self):
        inp = pp.clean_input("T", "D", "K")
        p = pp.build_prompt(inp, pp.candidates(ROWS), 1200, "English")
        self.assertIn("DATA", p)
        self.assertIn("ORIGINAL TITLE: <<<T>>>", p)


class Parse(unittest.TestCase):
    def test_good(self):
        r = pp.parse_answer(answer(), 1200)
        self.assertEqual(r["title"], "عنوان الفيديو")
        self.assertEqual(len(r["hashtags"]), 3)
        self.assertTrue(all(h.startswith("#") and " " not in h for h in r["hashtags"]))
        self.assertTrue(r["chapters_text"].startswith("0:00 "))
        self.assertTrue(r["description_full"].endswith(r["chapters_text"]))

    def test_fenced_json(self):
        self.assertIsNotNone(pp.parse_answer("```json\n" + answer() + "\n```", 1200))

    def test_garbage(self):
        for bad in ("", "hello", "[]", "{}", json.dumps({"title": "x"}), json.dumps({"description": "x"})):
            self.assertIsNone(pp.parse_answer(bad, 1200))

    def test_limits(self):
        r = pp.parse_answer(answer(title="ع" * 300, description="كلمة " * 3000, tags=["ت" * 31] + ["tag%d" % i for i in range(200)]), 1200)
        self.assertLessEqual(len(r["title"]), pp.TITLE_MAX)
        self.assertLessEqual(len(r["description_full"]), pp.DESC_MAX)
        self.assertLessEqual(sum(len(t) + 1 for t in r["tags"]), pp.TAGS_TOTAL_MAX)
        self.assertTrue(all(len(t) <= pp.TAG_MAX for t in r["tags"]))

    def test_urls_and_tags_removed(self):
        r = pp.parse_answer(answer(description="زوروا https://evil.example/x الآن <b>مهم</b>"), 1200)
        self.assertNotIn("http", r["description"])
        self.assertNotIn("<", r["description"])

    def test_tag_cleaning(self):
        r = pp.parse_answer(answer(tags=["#a,b", "A,B", "ok", "ok", ""]), 1200)
        self.assertEqual(r["tags"], ["a b", "ok"])


class Chapters(unittest.TestCase):
    def test_adds_start(self):
        c = pp._chapters([{"t": 60, "title": "أ"}, {"t": 200, "title": "ب"}, {"t": 400, "title": "ج"}], 1000)
        self.assertEqual(c[0], {"t": 0, "title": pp.INTRO_TITLE})
        self.assertEqual(len(c), 4)

    def test_gap_and_end(self):
        c = pp._chapters([{"t": 0, "title": "أ"}, {"t": 5, "title": "ب"}, {"t": 50, "title": "ج"}, {"t": 100, "title": "د"}, {"t": 995, "title": "هـ"}], 1000)
        self.assertEqual([x["t"] for x in c], [0, 50, 100])

    def test_too_few_gives_none(self):
        self.assertEqual(pp._chapters([{"t": 0, "title": "أ"}, {"t": 100, "title": "ب"}], 1000), [])
        self.assertEqual(pp._chapters(None, 1000), [])
        self.assertEqual(pp._chapters([{"t": "x", "title": "a"}, "z"], 1000), [])

    def test_max(self):
        c = pp._chapters([{"t": i * 20, "title": "ف%d" % i} for i in range(30)], 2000)
        self.assertEqual(len(c), pp.MAX_CHAPTERS)

    def test_timestamps(self):
        self.assertEqual(pp.fmt_ts(0), "0:00")
        self.assertEqual(pp.fmt_ts(75), "1:15")
        self.assertEqual(pp.fmt_ts(3725), "1:02:05")


class Generate(unittest.TestCase):
    def fake(self, *texts):
        calls = []
        seq = list(texts)

        def call(key, payload, timeout=0):
            calls.append(payload)
            t = seq.pop(0) if len(seq) > 1 else seq[0]
            if t is None:
                return None, "boom"
            return {"candidates": [{"content": {"parts": [{"text": t}]}}]}, None
        return call, calls

    def run_gen(self, call, key="k", rows=ROWS):
        return pp.generate("j", pp.clean_input("T"), rows, 1200, "English", key, call=call, record=lambda *a: None)

    def test_no_key_and_no_lines(self):
        call, calls = self.fake(answer())
        self.assertEqual(self.run_gen(call, key="")[1], "no_key")
        self.assertEqual(self.run_gen(call, rows=[])[1], "no_lines")
        self.assertEqual(calls, [])

    def test_ok(self):
        call, calls = self.fake(answer())
        pack, why = self.run_gen(call)
        self.assertIsNone(why)
        self.assertEqual(len(calls), 1)

    def test_retry_once_on_bad_answer(self):
        call, calls = self.fake("nonsense", answer())
        pack, why = self.run_gen(call)
        self.assertIsNotNone(pack)
        self.assertEqual(len(calls), 2)

    def test_gives_up(self):
        call, calls = self.fake("nonsense")
        self.assertEqual(self.run_gen(call)[1], "bad_answer")
        self.assertEqual(len(calls), 2)
        call, calls = self.fake(None)
        self.assertEqual(self.run_gen(call)[1], "ai_failed")


class Files(unittest.TestCase):
    def test_save_load(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(pp.load(d))
            pp.save(d, {"pack": {"title": "x"}, "input": {"title": "t"}})
            self.assertEqual(pp.load(d)["pack"]["title"], "x")
            self.assertEqual([f for f in os.listdir(d)], ["publish_pack.json"])
            open(os.path.join(d, "publish_pack.json"), "w").write("{bad")
            self.assertIsNone(pp.load(d))

    def test_prepare_audio_passthrough(self):
        self.assertEqual(pp.prepare_audio("/x/y.m4a", "/tmp", lambda c: None), ("/x/y.m4a", "dubbed_audio_ar.m4a", ""))
        self.assertEqual(pp.prepare_audio("/x/y.mp3", "/tmp", lambda c: None)[1], "dubbed_audio_ar.mp3")

    def test_prepare_audio_copy_then_encode(self):
        with tempfile.TemporaryDirectory() as d:
            seen = []

            def run(cmd):
                seen.append(cmd)
                if "copy" in cmd:
                    raise Exception("copy failed")
                open(cmd[-1], "wb").write(b"a" * 5000)
            path, name, note = pp.prepare_audio("/x/v.mp4", d, run)
            self.assertEqual(name, "dubbed_audio_ar.m4a")
            self.assertEqual(len(seen), 2)
            self.assertEqual(note, "")

    def test_prepare_audio_fails_softly(self):
        with tempfile.TemporaryDirectory() as d:
            def run(cmd):
                raise Exception("no")
            path, name, note = pp.prepare_audio("/x/v.mp4", d, run)
            self.assertIsNone(path)
            self.assertTrue(note)

    def test_zip(self):
        with tempfile.TemporaryDirectory() as d:
            audio = os.path.join(d, "a.m4a")
            open(audio, "wb").write(b"z" * 3000)
            pack = pp.parse_answer(answer(), 1200)
            dest = os.path.join(d, "p.zip")
            pp.build_zip(dest, pack, pp.clean_input("Original"), "1\nsrt", "WEBVTT", audio, "dubbed_audio_ar.m4a")
            with zipfile.ZipFile(dest) as z:
                self.assertEqual(sorted(z.namelist()), ["dubbed_audio_ar.m4a", "publishing_pack.txt", "subtitles_ar.srt", "subtitles_ar.vtt"])
                txt = z.read("publishing_pack.txt").decode("utf-8-sig")
                self.assertIn("عنوان الفيديو", txt)
                self.assertIn("choose dubbed_audio_ar.m4a", txt)
                self.assertEqual(z.read("dubbed_audio_ar.m4a"), b"z" * 3000)
            pp.build_zip(dest, pack, pp.clean_input("Original"), "", "", None, None, "no audio because")
            with zipfile.ZipFile(dest) as z:
                self.assertEqual(z.namelist(), ["publishing_pack.txt"])
                self.assertIn("no audio because", z.read("publishing_pack.txt").decode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
