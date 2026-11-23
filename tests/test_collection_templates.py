"""Regression tests for the collection-card template scope error.

Jinja2 imports macros without the caller context by default. Collection cards
must receive the user's hemisphere explicitly instead of trying to read a
nonexistent `user` variable in the imported macro.
"""
import unittest
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES = Path(__file__).resolve().parent.parent / 'app' / 'templates'


class ItemCardTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Environment(loader=FileSystemLoader(str(TEMPLATES)),
                              autoescape=True, undefined=StrictUndefined)
        cls.env.globals['url_for'] = lambda endpoint, **kw: '/'+endpoint
        cls.macro = cls.env.get_template('_item_card.html').module.item_card

    def test_months_render_for_both_hemispheres_without_page_context(self):
        item = {'name':'Example fish', 'position':1, 'price':100,
                'owned':False, 'image':False,
                'meta': {'months':{'north':'111111111111', 'south':'000011110000'}}}
        northern = str(self.macro(item, 'fish', 'token', 'north'))
        southern = str(self.macro(item, 'fish', 'token', 'south'))
        self.assertIn('Example fish', northern)
        self.assertIn('Mark collected', northern)
        self.assertEqual(northern.count('class="active"'), 12)
        self.assertEqual(southern.count('class="active"'), 4)

    def test_other_collections_and_missing_month_data(self):
        item = {'name':'Example recipe', 'position':1, 'price':None,
                'owned':True, 'image':False, 'meta':{'kind':'DIY'}}
        html = str(self.macro(item, 'diy', 'token', 'south'))
        self.assertIn('Collected', html)
        self.assertIn('Example recipe', html)

    def test_malformed_months_do_not_crash_page(self):
        item = {'name':'Example artwork', 'position':1, 'price':None,
                'owned':False, 'image':False, 'meta':{'months':'invalid'}}
        html = str(self.macro(item, 'art', 'token', 'north'))
        self.assertIn('Example artwork', html)


if __name__ == '__main__':
    unittest.main()
