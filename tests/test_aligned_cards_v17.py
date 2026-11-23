"""Independent regression tests for aligned card slots across collection types.

The card grid uses synchronized CSS tracks. A missing field, price, artwork,
or availability badge must not remove a slot and move other items vertically.
"""
from html.parser import HTMLParser
from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parent.parent


class CardSlots(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.slots = []
        self.detail_count = 0
        self.card_seen = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        classes = d.get('class', '').split()
        if not self.card_seen and tag == 'article' and 'item-card' in classes:
            self.card_seen = True
            self.depth = 1
            return
        if self.depth == 1:
            self.slots.append((tag, classes))
        if self.depth > 0 and tag not in {'img','input','br','hr','meta','link','area'}:
            self.depth += 1
        if 'item-detail' in classes:
            self.detail_count += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.depth > 0:
            self.depth -= 1


class AlignedCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = Environment(loader=FileSystemLoader(str(ROOT / 'app' / 'templates')),
                          undefined=StrictUndefined, autoescape=True)
        env.globals['url_for'] = lambda endpoint, **kwargs: '/' + endpoint
        cls.macro = env.get_template('_item_card.html').module.item_card

    def test_every_category_has_stable_row_slots_when_metadata_is_missing(self):
        expected_details = {'fish': 4, 'bugs': 3, 'deepsea': 5,
                            'fossil': 1, 'art': 3, 'diy': 2}
        for category, expected in expected_details.items():
            with self.subTest(category=category):
                item = {'name': 'Test item with a long title', 'position': 1,
                        'price': None, 'meta': {}, 'image': False, 'owned': False}
                markup = str(self.macro(item, category, 'csrf-token', 'north'))
                slots = CardSlots()
                slots.feed(markup)
                self.assertEqual(slots.detail_count, expected)
                names = [' '.join(classes) for _, classes in slots.slots]
                self.assertTrue(any('item-media' in s for s in names))
                self.assertTrue(any('availability-tags' in s for s in names))
                self.assertTrue(any('price-slot' in s for s in names))
                self.assertTrue(any('item-metadata' in s for s in names))
                self.assertTrue(any('item-extras' in s for s in names))
                self.assertTrue(any('item-months' in s for s in names))
                self.assertTrue(any('collect-form' in s for s in names))

    def test_badges_and_values_do_not_remove_other_slots(self):
        base = {'name': 'Test fish', 'position': 2, 'price': 100,
                'image': True, 'owned': False,
                'meta': {'location': 'River', 'time': 'All day',
                         'size': 'Medium', 'rarity': 'Common',
                         'months': {'north': '111111111111'}}}
        plain = dict(base)
        tagged = dict(base, available_now=True,
                      badges={'new': True, 'leaving': True})
        for item in (plain, tagged):
            slots = CardSlots()
            slots.feed(str(self.macro(item, 'fish', 'csrf-token', 'north')))
            self.assertEqual(slots.detail_count, 4)
            self.assertIn('price-slot', ' '.join(' '.join(c) for _, c in slots.slots))
            self.assertIn('item-months', ' '.join(' '.join(c) for _, c in slots.slots))

    def test_css_uses_synchronized_grid_and_per_category_track_counts(self):
        css = (ROOT / 'app' / 'static' / 'style.css').read_text()
        self.assertIn('grid-template-rows:subgrid', css)
        self.assertIn('grid-row:span var(--card-tracks)', css)
        self.assertIn('.item-card .item-metadata {display:contents;}', css)
        for category in ('bugs','deepsea','fossil','art','diy'):
            self.assertIn(f'.item-card-{category} ', css)


if __name__ == '__main__':
    unittest.main()
