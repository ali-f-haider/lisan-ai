"""Arabic commands (imperatives) must be written and voiced right: a plain alif with kasra or damma (اِدْفَعْ, اُكْتُبْ), not أَدْفَعْ,
which reads as "I pay". Only commands of verbs on the pattern أَفْعَلَ keep أَ (أَرْسِلْ). The Arabic and its marks come from the AI
model, so what we can guarantee is that every prompt that writes or marks Arabic carries this rule."""
import ast
import unittest
from pathlib import Path

import language_prompts as P

ROOT = Path(__file__).resolve().parents[1]
KASRA_EXAMPLES = ("اِدْفَعْ", "اِذْهَبْ", "اِجْلِسْ")
DAMMA_EXAMPLES = ("اُكْتُبْ", "اُخْرُجْ")
WRONG = "أَدْفَعْ"
FORM_IV = ("أَرْسِلْ", "أَعْطِ")


def source_of(filename, function):
    text = (ROOT / filename).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            return ast.get_source_segment(text, node)
    raise AssertionError(function + " not found in " + filename)


def rows():
    return [{"segment_id": "s1", "start": 0.0, "end": 2.0, "speaker": "A", "text": "Pay him."}]


class Translation(unittest.TestCase):
    def test_every_source_language_gets_the_rule(self):
        for code in (None, "en", "es", "fr", "pt", "it", "de", "nl", "pl"):
            prompt = P.translation_prompt(rows(), code)
            for needle in KASRA_EXAMPLES + DAMMA_EXAMPLES + FORM_IV + (WRONG, "COMMANDS (imperative)"):
                self.assertIn(needle, prompt, (code, needle))

    def test_the_rule_comes_with_the_tashkeel_instruction_and_before_the_emotions(self):
        prompt = P.translation_prompt(rows(), "en")
        a, b, c = prompt.index("Add full Tashkeel"), prompt.index("COMMANDS (imperative)"), prompt.index("Detect the primary emotion")
        self.assertLess(a, b)
        self.assertLess(b, c)

    def test_the_english_prompt_in_the_translator_has_the_same_rule(self):
        body = source_of("gemini_service.py", "translate_segments")
        for needle in KASRA_EXAMPLES + DAMMA_EXAMPLES + FORM_IV + (WRONG, "COMMANDS (imperative)"):
            self.assertIn(needle, body, needle)

    def test_the_rule_never_asks_for_a_hamza_with_kasra_on_a_command(self):
        # a command starts with a plain alif (hamzat al-wasl): أِ is not a letter form and إِ would be a hamza that is not there
        for text in (P.translation_prompt(rows(), "en"), source_of("gemini_service.py", "add_tashkeel_lines")):
            self.assertNotIn("أِ", text)
            self.assertNotIn("إِدْ", text)


class OtherPrompts(unittest.TestCase):
    def test_the_marking_prompts_know_it(self):
        for filename, function in (("gemini_service.py", "add_tashkeel_lines"), ("main.py", "tashkeel")):
            body = source_of(filename, function)
            for needle in ("اِدْفَعْ", "اُكْتُبْ", "never fatha", "أَدْفَعُ"):
                self.assertIn(needle, body, (function, needle))

    def test_the_marking_prompts_still_forbid_changing_letters(self):
        self.assertIn("Do NOT change, add, remove, or reorder any words or letters.", source_of("gemini_service.py", "add_tashkeel_lines"))
        self.assertIn("Do NOT change, add, remove, or reorder any words.", source_of("main.py", "tashkeel"))

    def test_the_shortening_prompt_knows_it(self):
        body = source_of("gemini_service.py", "shorten_arabic_line")
        for needle in ("اِدْفَعْ", "اُكْتُبْ", "أَرْسِلْ", "أَعْطِ"):
            self.assertIn(needle, body, needle)


if __name__ == "__main__":
    unittest.main()
