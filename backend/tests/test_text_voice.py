"""Text to voice (text_voice.py, 1.89.0): a text in any of the app's languages, or Arabic, spoken in Arabic. The script is cut into
lines and speakers, translated completely (no time limit), corrected by the person, spoken only where something changed, and paid
with an ID saved before every charge; what was not delivered is given back. Fake AI, fake voice and fake ledger only."""
import ast
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import text_voice as tv

ROOT = Path(__file__).resolve().parents[1]
UID = "11111111-1111-4111-8111-111111111111"


def mp3(seconds=0.6):
    """A short real MP3 (a tone) so the joining, levelling and encoding run for real."""
    p = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
                        "-ac", "1", "-ar", "44100", "-f", "mp3", "-"], capture_output=True, check=True)
    return p.stdout


class Ledger:
    """Charges by operation ID (a repeat with the same ID is the same charge) and refunds by (debit, key)."""
    def __init__(self, balance=1000, answer=None):
        self.balance, self.answer, self.charges, self.refunds = balance, answer, {}, {}

    def charge(self, uid, amount, action, job_id, operation_id=None):
        if self.answer is not None:
            return self.answer
        if operation_id in self.charges:
            return self.balance
        if amount > self.balance:
            return False
        self.charges[operation_id] = amount
        self.balance -= amount
        return self.balance

    def refund(self, uid, amount, job_id, debit_id=None, key=None):
        if (debit_id, key) not in self.refunds:
            self.refunds[(debit_id, key)] = amount
            self.balance += amount
        return {"ok": True}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "text_voice"
        self.ledger = Ledger()
        self.patches = [mock.patch.object(tv, "ROOT", root), mock.patch.object(tv, "VOICES", root / "voices"),
                        mock.patch.object(tv.Hooks, "charge", staticmethod(self.ledger.charge)),
                        mock.patch.object(tv.Hooks, "refund", staticmethod(self.ledger.refund)),
                        mock.patch.object(tv.Hooks, "chars_per_credit", staticmethod(lambda: 60)),
                        mock.patch.object(tv.Hooks, "voice_status", staticmethod(lambda v: "ready")),
                        mock.patch.object(tv, "GEMINI_API_KEY", "k")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def wait(self, job_id):
        for _ in range(400):
            if job_id not in tv._RUNNING:
                return tv.load(job_id)
            time.sleep(0.02)
        self.fail("the step did not finish")


class TheText(unittest.TestCase):
    def test_plain_text_is_one_narrator_and_long_text_is_cut_at_sentences(self):
        text = ("This is the first sentence of a long story. " * 12).strip() + "\n\nA new paragraph starts here."
        speakers, lines = tv.parse_script(text)
        self.assertEqual([s["name"] for s in speakers], ["Narrator"])
        self.assertTrue(all(len(ln["text"]) <= tv.LINE_CHARS for ln in lines))
        self.assertTrue(all(ln["text"].endswith(".") for ln in lines))
        self.assertEqual(lines[-1]["text"], "A new paragraph starts here.")
        self.assertEqual(lines[-1]["pause"], "para")
        self.assertEqual(lines[1]["pause"], "same")

    def test_a_script_gets_one_speaker_per_name(self):
        speakers, lines = tv.parse_script("Sara: Good morning, how can I help you?\nAdam: I'd like a table for two.\n"
                                          "Tonight, if possible.\nSara: Of course.")
        self.assertEqual([s["name"] for s in speakers], ["Sara", "Adam"])
        self.assertEqual([(ln["speaker_id"], ln["text"], ln["pause"]) for ln in lines],
                         [("s1", "Good morning, how can I help you?", "same"), ("s2", "I'd like a table for two.", "turn"),
                          ("s2", "Tonight, if possible.", "same"), ("s1", "Of course.", "turn")])

    def test_one_colon_in_prose_is_not_a_speaker(self):
        speakers, _ = tv.parse_script("Note: the museum opens at nine.\nVisitors can buy tickets at the door.\n"
                                      "Groups should book ahead.\nThe cafe closes at five.")
        self.assertEqual([s["name"] for s in speakers], ["Narrator"])

    def test_arabic_script_names_work_too(self):
        speakers, lines = tv.parse_script("سارة: صباح الخير.\nآدم: أهلًا بكِ.")
        self.assertEqual([s["name"] for s in speakers], ["سارة", "آدم"])
        self.assertEqual(lines[1]["text"], "أهلًا بكِ.")

    def test_languages_offered_are_the_enabled_ones_and_arabic(self):
        codes = [l["code"] for l in tv.source_languages()]
        self.assertEqual(codes[-1], "ar")
        self.assertIn("en", codes)
        self.assertEqual(tv.clean_language("AR"), "ar")
        with self.assertRaises(ValueError):
            tv.clean_language("xx")


class Projects(Base):
    def test_a_new_project_is_free_and_private(self):
        job, err = tv.new_job(UID, "Hello there. How are you?", "en", "Greeting")
        self.assertIsNone(err)
        self.assertEqual(self.ledger.charges, {})
        self.assertIsNone(tv.owned(job["id"], "someone-else"))
        self.assertEqual(tv.owned(job["id"], UID)["id"], job["id"])
        view = tv.public(job)
        self.assertNotIn("uid", view)
        self.assertNotIn("ops", view)
        self.assertEqual(tv.list_jobs(UID)[0]["title"], "Greeting")
        self.assertEqual(tv.list_jobs("someone-else"), [])

    def test_limits(self):
        self.assertEqual(tv.new_job(UID, "", "en")[1][1], 400)
        self.assertEqual(tv.new_job(UID, "x" * (tv.MAX_CHARS + 1), "en")[1][1], 413)
        self.assertEqual(tv.new_job(UID, "Hello.", "xx")[1][1], 400)

    def test_prices(self):
        job, _ = tv.new_job(UID, "a" * 2500, "en")
        self.assertEqual(tv.prepare_price(job), 13)          # 2.5 x 5, rounded up
        job, _ = tv.new_job(UID, "ب" * 2500, "ar")
        self.assertEqual(tv.prepare_price(job), 5)           # 2.5 x 2
        self.assertEqual(tv.new_job(UID, "Hi.", "en")[0] and tv.prepare_price(tv.new_job(UID, "Hi.", "en")[0]), 1)

    def test_old_projects_are_removed(self):
        job, _ = tv.new_job(UID, "Hello.", "en")
        tv.cleanup_old(now=time.time() + (tv.KEEP_DAYS + 1) * 86400)
        self.assertIsNone(tv.load(job["id"]))

    def test_corrections_and_speakers(self):
        job, _ = tv.new_job(UID, "Sara: Hi.\nAdam: Hello.", "en")
        ok, _ = tv.set_speakers(job, [{"id": "s1", "gender": "female", "voice_id": "v1", "voice_name": "Female voice 3"}, {"id": "s9", "gender": "male"}])
        self.assertTrue(ok)
        self.assertEqual(tv.load(job["id"])["speakers"][0]["gender"], "female")
        self.assertFalse(tv.set_speakers(job, [{"id": "s2", "voice_id": "bad id<script>"}])[0])
        ok, _ = tv.set_lines(job, [{"id": "l1", "arabic": "  مَرْحَبًا\n", "emotion": "happy"}, {"id": "l2", "emotion": "not-a-tag"}])
        saved = tv.load(job["id"])["lines"]
        self.assertEqual((saved[0]["arabic"], saved[0]["emotion"], saved[1]["emotion"]), ("مَرْحَبًا", "happy", ""))
        self.assertEqual(tv.set_lines(job, [{"id": "l1", "arabic": "ب" * (tv.MAX_ARABIC_CHARS + 1)}])[1][1], 400)


def translated(job_id, items, key, source_language="en", gender=None, previous=None):
    return {"status": "success", "translated_segments": [
        {"segment_id": it["segment_id"], "arabic_text": "تَرْجَمَةُ " + it["segment_id"], "emotion": "narrative", "gender_check": it["segment_id"] == "l2"}
        for it in items]}


class TheArabic(Base):
    def make(self, text="Sara: Trace the call.\nAdam: You know who I am.", lang="en"):
        job, _ = tv.new_job(UID, text, lang)
        tv.set_speakers(job, [{"id": "s1", "gender": "female"}, {"id": "s2", "gender": "male"}])
        return tv.load(job["id"])

    def test_genders_are_needed_before_a_translation(self):
        job, _ = tv.new_job(UID, "Sara: Hi.\nAdam: Hello.", "en")
        ok, err = tv.start_prepare(job)
        self.assertEqual((ok, err[1]), (False, 400))
        self.assertEqual(self.ledger.charges, {})

    def test_translation_is_paid_once_and_fills_every_line(self):
        job = self.make()
        seen = {}

        def tr(job_id, items, key, source_language="en", gender=None, previous=None):
            seen["gender"] = gender
            return translated(job_id, items, key)
        with mock.patch("gemini_service.translate_script_lines", tr), mock.patch("text_voice._ask_addressees"), \
             mock.patch("longdub_service._check_tashkeel"):
            ok, err = tv.start_prepare(job)
            self.assertTrue(ok, err)
            job = self.wait(job["id"])
        self.assertTrue(job["prepared"])
        self.assertEqual([ln["arabic"] for ln in job["lines"]], ["تَرْجَمَةُ l1", "تَرْجَمَةُ l2"])
        self.assertEqual([ln["gender_check"] for ln in job["lines"]], [False, True])
        self.assertEqual(list(self.ledger.charges.values()), [tv.prepare_price(job)])
        self.assertEqual(self.ledger.refunds, {})
        self.assertIn("(certain: chosen by the user)", json.dumps(seen["gender"]["speakers"]))
        self.assertEqual(seen["gender"]["lines"]["l2"]["speaker_gender"], "male")

    def test_a_failed_translation_is_given_back(self):
        job = self.make()
        with mock.patch("gemini_service.translate_script_lines", lambda *a, **k: {"error": "down"}), \
             mock.patch("text_voice._ask_addressees"), mock.patch.object(tv, "time", NS(sleep=lambda s: None, time=time.time)):
            tv.start_prepare(job)
            job = self.wait(job["id"])
        self.assertFalse(job["prepared"])
        self.assertTrue(job["error"])
        self.assertEqual(sum(self.ledger.refunds.values()), sum(self.ledger.charges.values()))

    def test_not_enough_credits_or_an_unconfirmed_payment(self):
        job = self.make()
        self.ledger.balance = 0
        ok, err = tv.start_prepare(job)
        self.assertEqual(err[1], 402)
        self.assertTrue(tv.load(job["id"])["ops"]["prepare"]["closed"])     # a refused charge is never asked again with its ID
        self.ledger.answer = None
        self.ledger.balance = 100
        led = self.ledger
        led.answer = None
        with mock.patch.object(tv.Hooks, "charge", staticmethod(lambda *a, **k: None)):
            ok, err = tv.start_prepare(tv.load(job["id"]))
        self.assertEqual(err[1], 503)
        first = tv.load(job["id"])["ops"]["prepare"]["id"]
        with mock.patch.object(tv.Hooks, "charge", staticmethod(lambda *a, **k: None)):
            tv.start_prepare(tv.load(job["id"]))
        self.assertEqual(tv.load(job["id"])["ops"]["prepare"]["id"], first)   # the same question again, never a second charge

    def test_arabic_text_keeps_its_words_and_gets_the_missing_marks(self):
        job, _ = tv.new_job(UID, "ذهب الولد إلى المدرسة.\nقَالَ: نَعَمْ.", "ar")

        def marks(job_id, items, key):
            return {"l1": "ذَهَبَ الوَلَدُ إِلَى المَدْرَسَةِ.", **({"l2": "x"} if any(i["segment_id"] == "l2" for i in items) else {})}
        with mock.patch("gemini_service.add_tashkeel_lines", marks), mock.patch("longdub_service._check_tashkeel"):
            ok, err = tv.start_prepare(job)
            self.assertTrue(ok, err)
            job = self.wait(job["id"])
        self.assertEqual(job["lines"][0]["arabic"], "ذَهَبَ الوَلَدُ إِلَى المَدْرَسَةِ.")
        self.assertEqual(job["lines"][1]["arabic"], "قَالَ: نَعَمْ.")        # already marked: kept exactly

    def test_a_diacritizer_that_changes_letters_is_not_taken(self):
        job, _ = tv.new_job(UID, "ذهب الولد", "ar")
        with mock.patch("gemini_service.add_tashkeel_lines", lambda *a, **k: {"l1": "ذَهَبَتِ البِنْتُ"}), \
             mock.patch("longdub_service._check_tashkeel"):
            tv.start_prepare(job)
            job = self.wait(job["id"])
        self.assertEqual(job["lines"][0]["arabic"], "ذهب الولد")


class TheVoice(Base):
    def ready(self, text="Sara: Hello there.\nAdam: Good evening.\n\nSara: Goodbye."):
        job, _ = tv.new_job(UID, text, "en")
        job["prepared"] = True
        for ln in job["lines"]:
            ln["arabic"], ln["emotion"] = "مَرْحَبًا " + ln["id"], "neutral"
        tv._save(job)
        return job

    def test_every_speaker_needs_a_voice(self):
        job = self.ready()
        ok, err = tv.start_speak(job)
        self.assertEqual((ok, err[1]), (False, 400))
        self.assertIn("Sara", err[0])

    def test_an_own_voice_still_getting_the_accent_waits(self):
        job = self.ready()
        tv.set_speakers(job, [{"id": "s1", "voice_id": "own1"}, {"id": "s2", "voice_id": "lib2"}])
        with mock.patch.object(tv.Hooks, "voice_status", staticmethod(lambda v: "preparing" if v == "own1" else "ready")):
            ok, err = tv.start_speak(tv.load(job["id"]))
        self.assertEqual(err[1], 409)
        self.assertEqual(self.ledger.charges, {})

    def speak(self, job, tts):
        with mock.patch("longdub_service._tts_with_retry", tts):
            ok, err = tv.start_speak(tv.load(job["id"]))
            self.assertTrue(ok, err)
            return self.wait(job["id"])

    def test_audio_is_made_and_only_changed_lines_are_paid_again(self):
        job = self.ready()
        tv.set_speakers(job, [{"id": "s1", "voice_id": "v1"}, {"id": "s2", "voice_id": "v2"}])
        price, chars = tv.speak_price(tv.load(job["id"]))
        sent = []
        tone = mp3()

        def tts(voice, text, waqf="auto", gap=None, delivery=None):
            sent.append((voice, text, gap))
            return tone, ""
        job = self.speak(job, tts)
        view = tv.public(job)
        self.assertTrue(view["audio_ready"], job.get("error"))
        self.assertGreater(view["audio_seconds"], 1.5)
        self.assertEqual(view["speak_price"], 0)
        self.assertEqual(list(self.ledger.charges.values()), [price])
        self.assertEqual(chars, sum(len(t) for _v, t, _g in sent))
        self.assertEqual([v for v, _t, _g in sent].count("v1"), 2)
        self.assertEqual(sorted(g for _v, _t, g in sent), [0.6, 0.9, 2.0])     # the silence after each line decides its stop form
        self.assertIsNotNone(tv.audio_path(job))
        # one line corrected: only that line is spoken and paid
        tv.set_lines(job, [{"id": "l2", "arabic": "مَسَاءُ الخَيْرِ"}])
        job = tv.load(job["id"])
        self.assertIsNone(tv.audio_path(job, current=True))        # the old file no longer matches the text ...
        self.assertIsNotNone(tv.audio_path(job))                     # ... but it stays to listen to and download until the new one
        self.assertTrue(tv.public(job)["audio_outdated"])
        self.assertEqual([(f["job_id"], f["outdated"]) for f in tv.finished_for(UID)], [(job["id"], True)])
        self.assertEqual(tv.public(job)["lines_to_speak"], 1)
        sent.clear()
        job = self.speak(job, tts)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sorted(self.ledger.charges.values()), sorted([price, int(-(-len("مَسَاءُ الخَيْرِ") // 60))]))
        self.assertTrue(tv.public(job)["audio_ready"])

    def test_a_line_that_could_not_be_spoken_is_given_back(self):
        job = self.ready("Hello there. " * 30)
        tv.set_speakers(job, [{"id": "s1", "voice_id": "v1"}])
        tone = mp3()
        job = self.speak(job, lambda voice, text, waqf="auto", gap=None, delivery=None: (None, "down") if "l2" in text else (tone, ""))
        self.assertFalse(tv.public(job)["audio_ready"])
        self.assertTrue(job["error"])
        lost = int(-(-len("مَرْحَبًا l2") // 60))
        self.assertEqual(sum(self.ledger.refunds.values()), lost)
        self.assertEqual(tv.public(job)["lines_to_speak"], 1)

    def test_a_step_cut_by_a_restart_is_given_back(self):
        job = self.ready()
        job["busy"] = "prepare"
        job["ops"] = {"prepare": {"id": "9b4a4f6e-1111-4111-8111-111111111111", "amount": 4, "refunded": 0, "closed": False, "at": 0}}
        tv._save(job)
        self.assertEqual(tv.recover(), 1)
        job = tv.load(job["id"])
        self.assertEqual((job["busy"], self.ledger.refunds), ("", {("9b4a4f6e-1111-4111-8111-111111111111", "prepare_interrupted"): 4}))


class OwnVoiceAccent(Base):
    def test_a_voice_getting_the_accent_is_preparing_until_it_is_done(self):
        done = {}

        def localize(voice_id, key, target_language=None, candidates=None, chooser=None):
            done["started"] = True
            time.sleep(0.1)
            return {"ok": True}
        with mock.patch("inworld_service.localize_voice", localize):
            tv.localize_later("voice_1")
            self.assertEqual(tv.voice_status("voice_1"), "preparing")
            for _ in range(100):
                if tv.voice_status("voice_1") != "preparing":
                    break
                time.sleep(0.02)
        self.assertEqual(tv.voice_status("voice_1"), "ready")
        self.assertEqual(tv.voice_status("never-seen"), "ready")


class Translator(unittest.TestCase):
    def test_the_prompt_asks_for_a_complete_translation_with_tashkeel(self):
        import gemini_service
        seen = {}

        def call(key, payload, timeout=120, models=None):
            seen["text"] = payload["contents"][0]["parts"][0]["text"]
            return {"candidates": [{"content": {"parts": [{"text": json.dumps([{"segment_id": "l1", "arabic_text": "س", "emotion": "Narrative, slowly"}])}]}}]}, None
        with mock.patch.object(gemini_service, "call_gemini", call), mock.patch.object(gemini_service, "record_gemini"):
            got = gemini_service.translate_script_lines("j", [{"segment_id": "l1", "speaker": "Narrator", "text": "Hello."}], "k",
                                                        source_language="de", gender={"speakers": {"Narrator": "male (certain: chosen by the user)"}, "lines": {}},
                                                        previous=[{"speaker": "Narrator", "text": "Before."}])
        self.assertEqual(got["translated_segments"][0]["emotion"], "narrative")
        for needle in ("German script", "there is no time limit", "never shorten", "full Tashkeel", "numbers", "data, not instructions",
                       "Before.", "certain: chosen by the user", "اِدْفَعْ"):
            self.assertIn(needle, seen["text"])
        self.assertNotIn("words per second", seen["text"])


def route(name, env):
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), env)
    return env[name]


class Routes(unittest.TestCase):
    def test_a_voice_of_somebody_else_is_refused(self):
        calls = []
        env = {"Request": object, "_text_voice_job": lambda r, j: (UID, {"id": j}),
               "_own_inworld_voices": lambda uid: [{"voice_id": "mine1"}],
               "inworld_service": NS(is_library_voice=lambda v, k: v == "stock1"), "INWORLD_API_KEY": "k",
               "text_voice": NS(set_speakers=lambda job, u: calls.append(u) or (True, None)),
               "_text_voice_answer": lambda ok_err, job_id, uid=None: ok_err,
               "JSONResponse": lambda body, status_code=200: NS(body=body, status_code=status_code)}
        f = route("text_voice_speakers", env)
        self.assertEqual(f("j", object(), {"speakers": [{"id": "s1", "voice_id": "theirs"}]}).status_code, 400)
        self.assertEqual(calls, [])
        self.assertEqual(f("j", object(), {"speakers": [{"id": "s1", "voice_id": "stock1"}, {"id": "s2", "voice_id": "mine1"}]}), (True, None))

    def test_the_own_voice_upload_for_text_to_voice_needs_consent_and_uses_the_sample_language(self):
        src = ast.get_source_segment((ROOT / "main.py").read_text(encoding="utf-8"), next(
            n for n in ast.parse((ROOT / "main.py").read_text(encoding="utf-8")).body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "upload_custom_voice"))
        for needle in ('for_text_voice: str = Form("")', 'voice_consent.strip().lower() not in', 'engine = "inworld" if for_text else _active_voice_engine()',
                       '{"language_code": sample_lang} if for_text else {}', 'text_voice.localize_later(res)'):
            self.assertIn(needle, src)



class LineTashkeel(Base):
    def job(self, arabic):
        job, _ = tv.new_job(UID, "Hello there.", "en")
        job["prepared"] = True
        job["lines"][0]["arabic"] = arabic
        tv._save(job)
        return job

    def test_the_button_fills_only_words_without_marks_and_is_free(self):
        job = self.job("ذَهَبَ الولد إلى المَدْرَسَةِ")
        with mock.patch("gemini_service.add_tashkeel_lines", lambda j, items, k: {"l1": "ذَهَبَ الوَلَدُ إِلَى المَدْرَسَةِ"}):
            ok, err, fresh = tv.tashkeel_line(job, "l1")
        self.assertTrue(ok, err)
        self.assertEqual(tv.load(job["id"])["lines"][0]["arabic"], "ذَهَبَ الوَلَدُ إِلَى المَدْرَسَةِ")
        self.assertEqual(self.ledger.charges, {})

    def test_letters_never_change_and_a_marked_line_is_left_alone(self):
        job = self.job("ذهب الولد")
        with mock.patch("gemini_service.add_tashkeel_lines", lambda j, items, k: {"l1": "ذَهَبَتِ البِنْتُ"}):
            ok, err, _ = tv.tashkeel_line(job, "l1")
        self.assertFalse(ok)
        self.assertEqual(tv.load(job["id"])["lines"][0]["arabic"], "ذهب الولد")
        job = self.job("ذَهَبَ الوَلَدُ")
        with mock.patch("gemini_service.add_tashkeel_lines") as call:
            ok, err, _ = tv.tashkeel_line(job, "l1")
        self.assertTrue(ok)
        call.assert_not_called()
        self.assertEqual(tv.tashkeel_line(job, "l9")[1][1], 404)

    def test_route_is_rate_limited_and_private(self):
        src = ast.get_source_segment((ROOT / "main.py").read_text(encoding="utf-8"), next(
            n for n in ast.parse((ROOT / "main.py").read_text(encoding="utf-8")).body
            if isinstance(n, ast.FunctionDef) and n.name == "text_voice_line_tashkeel"))
        self.assertIn("_text_voice_job(request, job_id)", src)
        self.assertIn('_rate_limited(request, "text_voice_tashkeel"', src)


class AccountFiles(Base):
    def made(self, uid=UID):
        job, _ = tv.new_job(uid, "Hello there.", "en", "My narration")
        job["prepared"] = True
        job["lines"][0].update(arabic="مَرْحَبًا", emotion="neutral", audio="")
        tv._save(job)
        job["lines"][0]["audio"] = tv._line_hash(job, 0)
        (tv._dir(job["id"]) / "lines").mkdir(parents=True, exist_ok=True)
        tv._line_file(job, job["lines"][0]).write_bytes(b"x" * 100)
        (tv._dir(job["id"]) / "final.mp3").write_bytes(b"y" * 1000)
        job["audio"] = {"hash": tv._final_hash(job), "seconds": 1.0, "at": time.time()}
        tv._save(job)
        return job

    def test_the_mp3_is_listed_counted_and_removed_with_all_files(self):
        job = self.made()
        self.made("someone-else")
        got = tv.finished_for(UID, 48)
        self.assertEqual([(g["job_id"], g["kind"], g["title"], g["bytes"]) for g in got], [(job["id"], "text", "My narration", 1100)])
        self.assertEqual(tv.audio_bytes(UID), 1100)
        self.assertFalse(tv.busy_for(UID))
        self.assertEqual(tv.delete_all_for(UID), (1, 0))
        self.assertEqual(tv.finished_for(UID), [])
        self.assertEqual(len(list(tv._jobs_of("someone-else"))), 1)

    def test_the_mp3_follows_the_retention_of_finished_files_and_the_text_stays(self):
        job = self.made()
        with mock.patch.object(tv.Hooks, "keep_hours", staticmethod(lambda uid: 48)):
            tv.cleanup_old(now=time.time() + 47 * 3600)
            self.assertTrue(tv.public(tv.load(job["id"]))["audio_ready"])
            tv.cleanup_old(now=time.time() + 49 * 3600)
        left = tv.load(job["id"])
        self.assertIsNotNone(left)
        self.assertEqual(left["lines"][0]["arabic"], "مَرْحَبًا")
        self.assertFalse(tv.public(left)["audio_ready"])
        self.assertIn("audio_expired", tv.public(left)["notes"])
        self.assertEqual(tv.audio_bytes(UID), 0)
        self.assertAlmostEqual(left["updated"], job["updated"], places=3)      # not counted as a change by the person

    def test_nothing_is_removed_when_the_subscription_cannot_be_read(self):
        job = self.made()
        with mock.patch.object(tv.Hooks, "keep_hours", staticmethod(lambda uid: None)):
            tv.cleanup_old(now=time.time() + 20 * 86400)
        self.assertTrue(tv.public(tv.load(job["id"]))["audio_ready"])

    def test_the_account_list_shows_text_to_voice_even_when_the_dub_history_cannot_be_read(self):
        job = self.made()
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(main).body if isinstance(n, ast.FunctionDef) and n.name == "my_jobs")
        node.decorator_list = []
        env = {"Request": object, "_current_uid": lambda r: UID, "_user_job_ids": lambda uid: None, "text_voice": tv,
               "_subscription_active": lambda uid: False, "CLEANUP_FINAL_OUTPUT_DAYS": 30, "PAYONCE_OUTPUT_HOURS": 48,
               "_storage_summary": lambda *a: self.fail("no storage figure from a missing history"),
               "JSONResponse": lambda body, status_code=200: NS(body=body, status_code=status_code)}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), env)
        got = env["my_jobs"](object())
        self.assertEqual([j["job_id"] for j in got["jobs"]], [job["id"]])
        self.assertIsNone(got["storage"])

    def test_the_account_page_and_the_account_routes_include_text_to_voice(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(main)

        def src(name):
            return ast.get_source_segment(main, next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name))
        self.assertIn("text_voice.finished_for(uid", src("my_jobs"))
        self.assertIn("text_voice.audio_bytes(uid)", src("_storage_summary"))
        self.assertIn("text_voice.delete_all_for(uid)", src("delete_all_my_files"))
        self.assertIn("text_voice.delete_all_for(uid)", src("delete_account"))
        self.assertIn("text_voice.busy_for(uid)", src("delete_account"))
        page = (ROOT / "account.html").read_text(encoding="utf-8")
        for needle in ('j.kind === "text"', "/api/text_voice/", "deleteTextVoice(", "/text-voice?open="):
            self.assertIn(needle, page)


class HelpPage(unittest.TestCase):
    def test_the_help_page_explains_it_in_both_languages_and_the_helper_reads_it(self):
        import assistant_service as a
        page = (ROOT / "help.html").read_text(encoding="utf-8")
        for needle in ('id="text-voice"', 'id="text-voice-ar"', "What is Text to voice?", "\u0645\u0627 \u062e\u062f\u0645\u0629"):
            self.assertIn(needle, page)
        text = a._page_text("help.html", None, 10 ** 7)
        self.assertIn("Can I use my own voice?", text)
        self.assertIn("/text-voice", a.FACTS)

if __name__ == "__main__":
    unittest.main()
