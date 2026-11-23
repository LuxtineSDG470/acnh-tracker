"""Art-only gallery improvements: one visible fake clue, bigger art, other grids intact."""
from pathlib import Path
from html import unescape
import json
import unittest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parent.parent


class ArtGalleryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = Environment(loader=FileSystemLoader(str(ROOT / 'app' / 'templates')),
                          autoescape=True, undefined=StrictUndefined)
        env.globals['url_for'] = lambda endpoint, **kwargs: '/' + endpoint
        cls.item_card = env.get_template('_item_card.html').module.item_card

    def art(self, metadata):
        return str(self.item_card({
            'name': 'Moving painting', 'position': 14, 'price': None,
            'image': True, 'owned': False, 'meta': metadata,
        }, 'art', 'csrf', 'north'))

    def test_one_visible_clue_and_explicit_fake_status(self):
        clue = 'The fake painting lacks the trees behind the Hora of Spring.'
        html = self.art({'kind': 'Painting', 'artist': 'Sandro Botticelli',
                         'real_name': 'The Birth of Venus',
                         'forgery': True, 'clues': [clue]})
        self.assertIn('Fake version exists', html)
        self.assertIn('⚠️', html)
        self.assertEqual(html.count(clue), 1)
        self.assertIn('art-forgery-warning', html)
        self.assertIn('art-image-link', html)
        self.assertIn('View full-size artwork', html)
        self.assertNotIn('<details', html)
        self.assertNotIn('More forgery clues', html)

    def test_genuine_art_has_no_false_warning(self):
        html = self.art({'kind': 'Painting', 'forgery': False, 'clues': []})
        self.assertIn('Always genuine', html)
        self.assertNotIn('art-forgery-warning', html)
        self.assertNotIn('Fake version exists', html)

    def test_multiple_clues_are_visible_once(self):
        html = self.art({'kind': 'Sculpture', 'forgery': True,
                         'clues': ['First difference', 'Second difference', 'First difference']})
        self.assertEqual(html.count('First difference'), 1)
        self.assertEqual(html.count('Second difference'), 1)
        self.assertNotIn('<details', html)

    def test_bundle_art_no_duplicate_legacy_forgery_dropdown(self):
        rows = json.loads((ROOT / 'bundle' / 'catalog.json').read_text())['items']
        count = 0
        for row in rows:
            if row['category'] != 'art':
                continue
            count += 1
            html = str(self.item_card({'name': row['name'], 'position': row['position'],
                                       'price': row['price'], 'image': True, 'owned': False,
                                       'meta': row['metadata']}, 'art', 'csrf', 'north'))
            self.assertNotIn('More forgery clues', html)
            self.assertNotIn('<details', html)
            if row['metadata'].get('forgery'):
                for clue in set(row['metadata'].get('clues', [])):
                    self.assertEqual(unescape(html).count(clue), 1, row['name'])
        self.assertEqual(count, 43)

    def test_other_collection_markups_are_unchanged(self):
        item = {'name': 'Test', 'position': 1, 'price': 500,
                'image': True, 'owned': False,
                'meta': {'location': 'Sea', 'size': 'Medium', 'rarity': 'Common',
                         'time': 'All day'}}
        for cat in ('fish', 'bugs', 'deepsea', 'fossil', 'diy'):
            html = str(self.item_card(item, cat, 'csrf', 'north'))
            self.assertNotIn('art-forgery-warning', html)
            self.assertIn('item-card-' + cat, html)

    def test_gallery_css_only_applies_to_art(self):
        css = (ROOT / 'app' / 'static' / 'style.css').read_text()
        template = (ROOT / 'app' / 'templates' / 'collection.html').read_text()
        self.assertIn('.item-grid.item-grid-art', css)
        self.assertIn('.item-card-art .item-artwork', css)
        self.assertEqual(template.count('class="item-grid item-grid-art"'), 2)
        self.assertIn('grid-template-rows:subgrid', css)


if __name__ == '__main__':
    unittest.main()
