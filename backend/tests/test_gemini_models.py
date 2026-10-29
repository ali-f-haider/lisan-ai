"""The Gemini text models the app tries are all still served.

Google retires models on published dates (https://ai.google.dev/gemini-api/docs/deprecations). A retired name in
a fallback list costs a failed request on every call that reaches it, so this keeps the known-retired ones out."""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RETIRED = {
    "gemini-2.0-flash", "gemini-2.0-flash-001", "gemini-2.0-flash-lite", "gemini-2.0-flash-lite-001",     # 2026-06-01
    "gemini-2.5-flash-preview-05-20", "gemini-2.5-flash-preview-09-25",                                     # 2025-11-18, 2026-02-17
    "gemini-2.5-flash-lite-preview-09-2025",                                                                # 2026-03-31
    "gemini-2.5-pro-preview-03-25", "gemini-2.5-pro-preview-05-06", "gemini-2.5-pro-preview-06-05",         # 2025-12-02
    "gemini-3-pro-preview",                                                                                 # 2026-03-09
    "gemini-2.5-flash",          # 28 Oct 2026: "no longer available to new users", every request failed there first (config.py)
}


def literal_list(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


class GeminiModelListTests(unittest.TestCase):
    def test_the_fallback_list_has_no_retired_model(self):
        models = literal_list(ROOT / "config.py", "GEMINI_MODELS")
        self.assertEqual([m for m in models if m in RETIRED], [])
        self.assertEqual(models[0], "gemini-3.6-flash")                    # the cheap model every call tries first

    def test_the_list_main_uses_is_the_same(self):
        self.assertEqual(literal_list(ROOT / "main.py", "GEMINI_TEXT_MODELS"), literal_list(ROOT / "config.py", "GEMINI_MODELS"))

    def test_there_is_a_newer_model_to_fall_back_to(self):
        models = literal_list(ROOT / "config.py", "GEMINI_MODELS")
        self.assertGreaterEqual(len(models), 2)
        self.assertEqual(len(set(models)), len(models))


if __name__ == "__main__":
    unittest.main()
