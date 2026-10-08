"""The Step 4 library follows the admin Voice Engine switch: Inworld's stock voices, cached, with automatic fallbacks."""
import ast, json, re, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import inworld_service as iw
import voice_match_service as vms
import voice_match as vm

MAIN = (ROOT / "main.py").read_text(encoding="utf-8")


def voice(vid, gender="male", lang="EN_US", source="SYSTEM", age="middle_aged", **extra):
    return dict({"voiceId": vid, "displayName": vid, "langCode": lang, "source": source, "gender": gender,
                 "ageGroup": age, "description": "A warm voice", "tags": ["warm", "calm", "extra"]}, **extra)


class Pages:
    """Stands in for the Inworld voice list: pages of voices, and a switch to make it fail."""
    def __init__(self, pages, fail=False):
        self.pages, self.fail, self.calls = pages, fail, []
    def __call__(self, method, path, key, body=None, timeout=30):
        self.calls.append(path)
        if self.fail:
            raise OSError("down")
        index = 0
        if "pageToken=" in path:
            index = int(path.split("pageToken=")[1])
        out = {"voices": self.pages[index]}
        if index + 1 < len(self.pages):
            out["nextPageToken"] = str(index + 1)
        return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (iw.LIBRARY_FILE, iw.PREVIEW_DIR, iw._request)
        iw.LIBRARY_FILE = Path(self.tmp.name) / "inworld_library.json"
        iw.PREVIEW_DIR = Path(self.tmp.name) / "previews"
        iw.reset_library_cache()
    def tearDown(self):
        iw.LIBRARY_FILE, iw.PREVIEW_DIR, iw._request = self.saved
        iw.reset_library_cache()
        self.tmp.cleanup()


class RowTests(unittest.TestCase):
    def test_only_stock_voices_with_a_gender_are_listed(self):
        self.assertIsNotNone(iw.library_row(voice("Omar")))
        self.assertIsNone(iw.library_row(voice("lime__custom_x", source="IVC")))       # the account's own clones are never mixed in
        self.assertIsNone(iw.library_row(voice("Odd", gender="")))
        self.assertIsNone(iw.library_row(voice("", gender="male")))
        self.assertIsNone(iw.library_row("not a voice"))

    def test_a_row_carries_what_step_4_and_the_matcher_need(self):
        row = iw.library_row(voice("Omar", lang="AR_SA", age="elderly"))
        self.assertEqual((row["voice_id"], row["gender"], row["age"], row["arabic"]), ("Omar", "male", "elderly", True))
        self.assertEqual(vm.age_band(row["age"]), "senior")                             # the matcher understands Inworld's age words
        self.assertEqual(row["use_case"], "warm, calm")
        self.assertIn("warm", row["descriptive"])
        self.assertEqual(row["preview_url"], "/api/voices/preview/Omar")
        self.assertEqual(iw.library_row(voice("Alex Q/1"))["preview_url"], "/api/voices/preview/Alex%20Q%2F1")

    def test_arabic_is_recognised_in_either_spelling_and_other_languages_are_not(self):
        self.assertTrue(iw.library_row(voice("A", lang="AR_SA"))["arabic"])
        self.assertTrue(iw.library_row(voice("B", langCode="", languageCode="ar-SA"))["arabic"])
        self.assertFalse(iw.library_row(voice("C", lang="EN_US"))["arabic"])
        self.assertFalse(iw.library_row(voice("D", lang="ARM_AM"))["arabic"])            # not just any code that starts with "ar"


class FetchTests(Base):
    def test_all_pages_are_read_and_arabic_voices_come_first(self):
        iw._request = Pages([[voice("Zed"), voice("Nour", "female", "AR_SA"), voice("clone", source="IVC")],
                             [voice("Omar", lang="AR_SA"), voice("Alex")]])
        rows = iw.fetch_library("key")["voices"]
        self.assertEqual([r["voice_id"] for r in rows], ["Nour", "Omar", "Alex", "Zed"])
        self.assertEqual([r["order"] for r in rows], [0, 1, 2, 3])
        self.assertTrue(rows[0]["arabic"] and not rows[-1]["arabic"])

    def test_the_list_is_cached_so_step_4_does_not_ask_inworld_every_time(self):
        pages = Pages([[voice("Omar")]]); iw._request = pages
        iw.fetch_library("key"); iw.fetch_library("key")
        self.assertEqual(len(pages.calls), 1)

    def test_an_outage_serves_the_last_good_list_and_keeps_serving_after_a_restart(self):
        iw._request = Pages([[voice("Omar", lang="AR_SA")]])
        iw.fetch_library("key")
        iw.reset_library_cache()                                                        # a restart: nothing in memory, the saved copy is on disk
        iw._request = Pages([[]], fail=True)
        got = iw.fetch_library("key")
        self.assertEqual([r["voice_id"] for r in got["voices"]], ["Omar"])

    def test_an_outage_with_nothing_saved_is_a_clear_error_not_an_empty_list(self):
        iw._request = Pages([[]], fail=True)
        self.assertIn("error", iw.fetch_library("key"))
        self.assertIn("error", iw.fetch_library(""))                                    # no key at all

    def test_an_answer_without_stock_voices_does_not_wipe_the_saved_list(self):
        iw._request = Pages([[voice("Omar")]]); iw.fetch_library("key")
        iw.reset_library_cache()
        iw._request = Pages([[voice("clone", source="IVC")]])
        self.assertEqual([r["voice_id"] for r in iw.fetch_library("key")["voices"]], ["Omar"])

    def test_a_stock_voice_is_known_even_without_a_key_once_listed(self):
        iw._request = Pages([[voice("Omar")]]); iw.fetch_library("key")
        iw.reset_library_cache()
        self.assertTrue(iw.is_library_voice("Omar"))                                    # from the saved copy
        self.assertFalse(iw.is_library_voice("21m00Tcm4TlvDq8ikWAM"))                   # an ElevenLabs id
        self.assertFalse(iw.is_library_voice(""))
        self.assertFalse(iw.is_library_voice(None))


class PreviewTests(Base):
    def test_a_preview_is_made_once_and_then_served_from_disk(self):
        calls = []
        def synth(voice_id, text, key, language="ar"):
            calls.append((voice_id, text, language)); return b"ID3audio"
        first = iw.make_preview("Omar", "key", synth)
        second = iw.make_preview("Omar", "key", synth)
        self.assertEqual(first, second); self.assertEqual(first.read_bytes(), b"ID3audio")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], "ar")
        self.assertRegex(calls[0][1], "[ً-ْ]")                                # the sample carries vowel marks, like real lines

    def test_an_empty_answer_is_an_error_and_leaves_no_file(self):
        with self.assertRaises(Exception):
            iw.make_preview("Omar", "key", lambda *a, **k: b"")
        self.assertFalse(iw.preview_path("Omar").exists())

    def test_the_file_name_cannot_escape_the_folder(self):
        p = iw.preview_path("../../etc/passwd")
        self.assertEqual(p.parent, iw.PREVIEW_DIR)


class AutoAssignCacheTests(unittest.TestCase):
    def test_the_matcher_does_not_reuse_another_engines_list(self):
        vms.reset_cache()
        engine = {"now": "elevenlabs"}
        calls = []
        def loader():
            calls.append(engine["now"]); return {"voices": [{"voice_id": engine["now"]}]}
        loader.cache_key = lambda: engine["now"]
        self.assertEqual(vms.library_voices(loader)[0]["voice_id"], "elevenlabs")
        self.assertEqual(vms.library_voices(loader)[0]["voice_id"], "elevenlabs")
        self.assertEqual(len(calls), 1)
        engine["now"] = "inworld"                                                       # the admin switched
        self.assertEqual(vms.library_voices(loader)[0]["voice_id"], "inworld")
        vms.reset_cache()

    def test_a_loader_without_a_key_still_caches(self):
        vms.reset_cache(); calls = []
        def loader():
            calls.append(1); return {"voices": [{"voice_id": "a"}]}
        vms.library_voices(loader); vms.library_voices(loader)
        self.assertEqual(len(calls), 1); vms.reset_cache()

    def test_the_stock_voices_page_addresses_are_not_measured_for_pitch(self):
        src = (ROOT / "voice_match.py").read_text(encoding="utf-8")
        self.assertIn('not str(v["preview_url"]).startswith("/")', src)                  # the stock voices' own page addresses are skipped


class WiringTests(unittest.TestCase):
    def route(self, start, end):
        a = MAIN.index(start); return MAIN[a:MAIN.index(end, a)]

    def test_the_library_follows_the_admin_switch_and_falls_back(self):
        body = self.route("def _library_voices():", '@app.post("/api/voices")')
        self.assertIn('_active_voice_engine() == "inworld"', body)
        self.assertIn("inworld_service.fetch_library(INWORLD_API_KEY)", body)
        self.assertIn("eleven_service.fetch_voices(ELEVENLABS_API_KEY)", body)           # the fallback, and the whole answer on the other engine
        self.assertIn("_library_voices.cache_key", MAIN)

    def test_step_4_and_auto_assign_read_the_same_list(self):
        self.assertRegex(MAIN, r'@app\.post\("/api/voices"\)\s+def voices\(payload: dict = \{\}\):\s+return _library_voices\(\)')
        self.assertIn("fetch_voices=_library_voices", MAIN)

    def test_a_stock_voice_is_always_spoken_by_its_own_engine(self):
        body = self.route("def _voice_engines_for_ids", "def _tag_voice_engine")
        self.assertIn('inworld_service.is_library_voice(vid, INWORLD_API_KEY)', body)
        self.assertLess(body.index("is_library_voice"), body.index("SUPABASE_URL"))      # decided before any database lookup

    def test_the_preview_route_is_limited_and_only_for_stock_voices(self):
        body = self.route('@app.get("/api/voices/preview/{voice_id}")', "# Auto-Assign: pick library voices")
        self.assertIn("is_library_voice(voice_id, INWORLD_API_KEY)", body)
        self.assertIn('_rate_limited(request, "voice_preview"', body)
        self.assertLess(body.index("is_library_voice"), body.index("make_preview"))

    def test_engine_lookup_behaviour(self):
        tree = ast.parse(MAIN)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_voice_engines_for_ids")
        calls = []
        class FakeInworld:
            @staticmethod
            def is_library_voice(v, key=None): calls.append(v); return v in ("Omar", "Nour")
        env = {"inworld_service": FakeInworld, "INWORLD_API_KEY": "k", "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "", "json": json}
        exec(compile(ast.Module([node], []), "main", "exec"), env)
        got = env["_voice_engines_for_ids"](["Omar", "21m00Tcm4TlvDq8ikWAM", "Nour", "", None])
        self.assertEqual(got, {"Omar": "inworld", "Nour": "inworld"})                    # an ElevenLabs voice is left to the old default


if __name__ == "__main__":
    unittest.main()
