"""Upload controls remain separate and usable with a keyboard."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Controls(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.stack, self.elements = [], {}
        self.feed(text)

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if attrs.get('id'):
            self.elements[attrs['id']] = (tag, attrs, list(self.stack))
        if tag not in {'input', 'meta', 'link', 'img', 'br', 'hr', 'source', 'wbr', 'area', 'base', 'embed', 'param', 'track', 'col'}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


class StepOneTests(unittest.TestCase):
    def setUp(self):
        self.controls = Controls((ROOT / 'index.html').read_text(encoding='utf-8'))

    def test_language_field_and_file_inputs_are_outside_upload_action(self):
        for name in ('sourceLanguageSection', 'audioFile', 'projectFileStep1'):
            with self.subTest(control=name):
                _, attrs, parents = self.controls.elements[name]
                self.assertNotIn('onclick', attrs)
                self.assertFalse(any(tag in {'button', 'label'} or 'onclick' in parent for tag, parent in parents))

    def test_upload_and_load_actions_have_native_keyboard_semantics(self):
        for name, file in (('uploadMediaBtn', 'audioFile'), ('loadProjectStep1Btn', 'projectFileStep1')):
            with self.subTest(control=name):
                tag, attrs, _ = self.controls.elements[name]
                self.assertEqual(tag, 'button')
                self.assertEqual(attrs.get('type'), 'button')
                self.assertIn("getElementById('" + file + "').click()", attrs['onclick'])


if __name__ == '__main__':
    unittest.main()
