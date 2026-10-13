"""The landing language display must describe the real supported sources accessibly."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

from languages import supported


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.root = {'tag': 'root', 'attrs': {}, 'children': [], 'text': ''}
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'children': [], 'text': ''}
        self.stack[-1]['children'].append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        for node in self.stack:
            node['text'] += text

    @staticmethod
    def descendants(node):
        for child in node['children']:
            yield child
            yield from Page.descendants(child)


class LandingLanguagesTests(unittest.TestCase):
    def setUp(self):
        page = Page((Path(__file__).resolve().parents[1] / 'landing.html').read_text(encoding='utf-8'))
        self.nodes = list(page.descendants(page.root))
        self.cards = [node for node in self.nodes if 'data-source-language' in node['attrs']]

    def test_each_supported_source_has_its_actual_english_and_arabic_name(self):
        records = {language.code: language for language in supported()}
        self.assertCountEqual([card['attrs']['data-source-language'] for card in self.cards], records)
        for card in self.cards:
            language = records[card['attrs']['data-source-language']]
            with self.subTest(language=language.code):
                nodes = list(Page.descendants(card))
                for cls, name in (('en', language.english_name), ('ar', language.arabic_name)):
                    labels = [node for node in nodes if cls in node['attrs'].get('class', '').split()]
                    self.assertEqual([node['text'].strip() for node in labels], [name])

    def test_language_list_has_a_bilingual_accessible_heading(self):
        lists = [node for node in self.nodes if 'language-orbit' in node['attrs'].get('class', '').split()]
        self.assertEqual(len(lists), 1)
        self.assertEqual(lists[0]['tag'], 'ul')
        heading = next(node for node in self.nodes if node['attrs'].get('id') == lists[0]['attrs']['aria-labelledby'])
        self.assertIn('Supported source languages', heading['text'])
        self.assertIn('لغات الفيديو الأصلية المدعومة', heading['text'])
        self.assertEqual([node for node in lists[0]['children'] if node['tag'] == 'li'], self.cards)

    def test_flag_graphics_are_decorative_local_vectors_and_cannot_steal_keyboard_focus(self):
        for card in self.cards:
            with self.subTest(language=card['attrs']['data-source-language']):
                nodes = list(Page.descendants(card))
                flag = next(node for node in nodes if 'language-flag' in node['attrs'].get('class', '').split())
                self.assertEqual(flag['attrs'].get('aria-hidden'), 'true')
                self.assertEqual(flag['children'][0]['tag'], 'svg')
                self.assertEqual(flag['children'][0]['attrs'].get('focusable'), 'false')
                self.assertFalse(any(node['tag'] in {'a', 'button', 'input', 'iframe', 'img', 'use'} or
                                     'tabindex' in node['attrs'] or 'onclick' in node['attrs'] for node in nodes))


if __name__ == '__main__':
    unittest.main()
