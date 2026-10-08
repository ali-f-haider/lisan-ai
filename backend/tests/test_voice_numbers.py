"""'Male voice 17' always means the same voice: numbers are given once and never change or get reused."""
import ast, json, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import voice_numbers as vn

MAIN = (ROOT / "main.py").read_text(encoding="utf-8")


def rows(*pairs):
    return [{"voice_id": v, "gender": g} for v, g in pairs]


def numbers(result):
    return {r["voice_id"]: r.get("number") for r in result}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = vn.FILE
        vn.FILE = Path(self.tmp.name) / "voice_numbers.json"
        vn.reset_memory()
    def tearDown(self):
        vn.FILE = self.saved
        vn.reset_memory()
        self.tmp.cleanup()


class NumberingTests(Base):
    def test_numbers_start_at_one_per_gender_in_the_order_given(self):
        got = numbers(vn.assign("e", rows(("a", "male"), ("b", "female"), ("c", "male"))))
        self.assertEqual(got, {"a": 1, "b": 1, "c": 2})

    def test_a_voice_keeps_its_number_whatever_happens_to_the_list(self):
        vn.assign("e", rows(("a", "male"), ("b", "male"), ("c", "male")))
        again = numbers(vn.assign("e", rows(("c", "male"), ("new", "male"), ("a", "male"))))      # reordered, b gone, one added
        self.assertEqual(again, {"c": 3, "a": 1, "new": 4})

    def test_a_number_that_was_given_is_never_given_to_another_voice(self):
        vn.assign("e", rows(("a", "male"), ("b", "male")))
        vn.assign("e", rows(("a", "male")))                                                   # b disappears
        got = numbers(vn.assign("e", rows(("a", "male"), ("z", "male"))))
        self.assertEqual(got["z"], 3)
        self.assertEqual(numbers(vn.assign("e", rows(("b", "male"))))["b"], 2)                # and b gets its own number back if it returns

    def test_numbers_survive_a_restart(self):
        vn.assign("e", rows(("a", "male"), ("b", "male")))
        vn.reset_memory()
        self.assertEqual(numbers(vn.assign("e", rows(("b", "male"), ("a", "male")))), {"b": 2, "a": 1})
        self.assertEqual(json.loads(vn.FILE.read_text())["engines"]["e"]["male"], {"a": 1, "b": 2})

    def test_the_two_engines_number_their_own_voices(self):
        vn.assign("one", rows(("a", "male"), ("b", "male")))
        self.assertEqual(numbers(vn.assign("two", rows(("x", "male")))), {"x": 1})            # not 3

    def test_gender_is_read_in_any_case_and_voices_without_one_get_no_number(self):
        got = numbers(vn.assign("e", rows(("a", "Male"), ("b", ""), ("c", "female"))))
        self.assertEqual(got, {"a": 1, "b": None, "c": 1})

    def test_an_unreadable_register_is_not_overwritten_and_numbering_falls_back_to_position(self):
        vn.FILE.write_text("{ not json")
        got = numbers(vn.assign("e", rows(("a", "male"), ("b", "male"))))
        self.assertEqual(got, {"a": 1, "b": 2})
        self.assertEqual(vn.FILE.read_text(), "{ not json")                                 # left for a person to look at

    def test_a_failed_save_still_answers(self):
        original = vn._write
        vn._write = lambda data: (_ for _ in ()).throw(OSError("disk full"))
        try:
            self.assertEqual(numbers(vn.assign("e", rows(("a", "male")))), {"a": 1})
        finally:
            vn._write = original


class LibraryTests(Base):
    def function(self, **env):
        tree = ast.parse(MAIN)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_library_voices")
        scope = dict(env)
        exec(compile(ast.Module([node], []), "main", "exec"), scope)
        return scope["_library_voices"]

    def eleven(self, ids):
        class E:
            @staticmethod
            def fetch_voices(key):
                return {"voices": [{"voice_id": i, "gender": g} for i, g in ids]}
        return E

    def test_the_original_list_starts_numbered_exactly_as_before_by_voice_id(self):
        fn = self.function(_active_voice_engine=lambda: "elevenlabs", eleven_service=self.eleven([("c", "male"), ("a", "male"), ("b", "female"), ("B", "male")]),
                           ELEVENLABS_API_KEY="k", inworld_service=None, INWORLD_API_KEY="")
        got = fn()["voices"]
        by_id = {v["voice_id"]: v["number"] for v in got}
        self.assertEqual(by_id, {"a": 1, "B": 2, "c": 3, "b": 1})                              # male: a, B, c  (case-insensitive like the page's sort)
        self.assertEqual([v["voice_id"] for v in got if v["gender"] == "male"], ["a", "B", "c"])

    def test_a_new_voice_never_changes_a_number_of_the_original_list(self):
        fn = self.function(_active_voice_engine=lambda: "elevenlabs", eleven_service=self.eleven([("b", "male"), ("d", "male")]),
                           ELEVENLABS_API_KEY="k", inworld_service=None, INWORLD_API_KEY="")
        fn()
        fn2 = self.function(_active_voice_engine=lambda: "elevenlabs", eleven_service=self.eleven([("a", "male"), ("b", "male"), ("d", "male")]),
                            ELEVENLABS_API_KEY="k", inworld_service=None, INWORLD_API_KEY="")
        by_id = {v["voice_id"]: v["number"] for v in fn2()["voices"]}
        self.assertEqual(by_id, {"b": 1, "d": 2, "a": 3})                                      # "a" would have been first by id, but it is new

    def test_inworld_voices_show_arabic_first_and_the_fallback_is_numbered_on_its_own(self):
        rows_ = [{"voice_id": "Omar", "gender": "male", "arabic": True, "order": 0}, {"voice_id": "Alex", "gender": "male", "arabic": False, "order": 1}]
        class IW:
            @staticmethod
            def fetch_library(key): return {"voices": rows_}
        fn = self.function(_active_voice_engine=lambda: "inworld", inworld_service=IW, INWORLD_API_KEY="k",
                           eleven_service=self.eleven([("x", "male")]), ELEVENLABS_API_KEY="k")
        got = fn()["voices"]
        self.assertEqual([(v["voice_id"], v["number"], v["order"]) for v in got], [("Omar", 1, 0), ("Alex", 2, 1)])
        self.assertNotIn("number", rows_[0])                                                   # the cached rows are not touched
        class Down:
            @staticmethod
            def fetch_library(key): return {"error": "down"}
        fn = self.function(_active_voice_engine=lambda: "inworld", inworld_service=Down, INWORLD_API_KEY="k",
                           eleven_service=self.eleven([("x", "male")]), ELEVENLABS_API_KEY="k")
        self.assertEqual([(v["voice_id"], v["number"]) for v in fn()["voices"]], [("x", 1)])  # the other list, with its own register

    def test_a_later_arabic_voice_is_shown_early_but_keeps_its_late_number(self):
        class IW:
            voices = [{"voice_id": "Omar", "gender": "male", "arabic": True}, {"voice_id": "Alex", "gender": "male", "arabic": False}]
            @classmethod
            def fetch_library(cls, key): return {"voices": [dict(v) for v in cls.voices]}
        env = dict(_active_voice_engine=lambda: "inworld", inworld_service=IW, INWORLD_API_KEY="k", eleven_service=None, ELEVENLABS_API_KEY="")
        self.function(**env)()
        IW.voices = IW.voices + [{"voice_id": "Salma", "gender": "male", "arabic": True}]
        got = self.function(**env)()["voices"]
        self.assertEqual([(v["voice_id"], v["number"]) for v in got], [("Omar", 1), ("Salma", 3), ("Alex", 2)])


if __name__ == "__main__":
    unittest.main()
