"""Run the short-editor browser logic tests without network or paid requests."""
import shutil
import subprocess
import unittest
from pathlib import Path


class ShortEditorUITests(unittest.TestCase):
    def test_short_editor_browser_logic(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node.js is required for the editor UI tests')
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [node, '--test', str(root / 'tests/test_shortdub_ui.js')],
            cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
