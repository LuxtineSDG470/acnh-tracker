"""Regression coverage for v0.1.13 collection parity, without changing saved data."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES = Path(__file__).resolve().parent.parent / 'app' / 'templates'

try:
    from app.web import create_app
except ModuleNotFoundError as exc:
    if exc.name in ('flask', 'werkzeug'):
        create_app = None
    else:
        raise


class CardTemplateTests(unittest.TestCase):
    def test_missing_images_have_visible_fallback(self):
        env = Environment(loader=FileSystemLoader(str(TEMPLATES)), undefined=StrictUndefined)
        env.globals['url_for'] = lambda endpoint, **kw: '/'+endpoint
        item = {'name':'Unillustrated recipe', 'position':4, 'price':100,
                'meta': {'type':'Housewares', 'sources':['Villager'],
                         'materials':[{'name':'Wood', 'quantity':3}]},
                'owned':False, 'image':False}
        html = str(env.get_template('_item_card.html').module.item_card(item, 'diy', 'secret', 'north'))
        self.assertIn('item-artwork-placeholder', html)
        self.assertIn('Artwork unavailable', html)
        self.assertIn('Found:', html)
        self.assertIn('Wood', html)

    def test_art_forgery_hint_is_visible_without_a_dropdown(self):
        env = Environment(loader=FileSystemLoader(str(TEMPLATES)), undefined=StrictUndefined)
        env.globals['url_for'] = lambda endpoint, **kw: '/'+endpoint
        item = {'name':'Example painting', 'position':1, 'price':None,
                'meta':{'kind':'Painting','forgery':True, 'clues':['Check the eyes']},
                'owned':False, 'image':False}
        html = str(env.get_template('_item_card.html').module.item_card(item, 'art', 'secret', 'north'))
        self.assertIn('Check the eyes', html)
        self.assertIn('art-forgery-warning', html)
        self.assertIn('Fake version exists', html)
        self.assertEqual(html.count('Check the eyes'), 1)
        self.assertNotIn('More forgery clues', html)
        self.assertNotIn('<details', html)


@unittest.skipIf(create_app is None, 'Flask runtime unavailable; runs in GitHub CI')
class InteractiveCollectionRouteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with patch.dict(os.environ, {'DATA_DIR': temp.name, 'TZ':'America/Chicago'}):
            self.app = create_app({'TESTING':True})
        with sqlite3.connect(self.app.config['DATABASE']) as db:
            self.uid = db.execute("INSERT INTO users(username,password_hash,display_name,is_admin,hemisphere) "
                                  "VALUES ('user1','unused','User',1,'north')").lastrowid
            self.fish = db.execute("SELECT name FROM catalog_items WHERE category='fish' ORDER BY position LIMIT 1").fetchone()[0]
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['uid'] = self.uid
            session['csrf'] = 'valid-csrf'

    def test_full_small_category_and_grouped_pagination(self):
        for category, expected in [('fish',80),('bugs',80),('deepsea',40),('fossil',73),('art',43)]:
            with self.subTest(category=category):
                response = self.client.get(f'/collections/{category}?month=all')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data.count(b'<article class="item-card '), expected)
                self.assertNotIn(b'Page 1 of 2', response.data)
        response = self.client.get('/collections/fish?month=5')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Available in May', response.data)
        self.assertIn(b'Always Available', response.data)
        self.assertNotIn(b' on this page', response.data)
        self.assertNotIn(b'Page 1 of 2', response.data)

    def test_diy_progressively_loads_for_pi(self):
        response = self.client.get('/collections/diy?sort=catalog')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'<article class="item-card '), 60)
        self.assertIn(b'Show 60 more recipes', response.data)
        self.assertIn(b'Show all 623', response.data)
        second = self.client.get('/collections/diy?sort=catalog&limit=120')
        self.assertEqual(second.data.count(b'<article class="item-card '), 120)

    def test_uncollected_filter_reflects_server_confirmed_changes(self):
        before = self.client.get('/collections/fish?month=all&status=missing')
        self.assertIn(b'80 results', before.data)
        saved = self.client.post('/collection/set',data={
            '_csrf':'valid-csrf','category':'fish','name':self.fish,'have':'1'},
            headers={'Accept':'application/json'})
        self.assertEqual(saved.status_code,200)
        self.assertTrue(saved.json['owned'])
        after = self.client.get('/collections/fish?month=all&status=missing')
        self.assertIn(b'79 results', after.data)
        self.assertIn(b'1 / 80 collected', after.data)
        self.assertNotIn(f'name="name" value="{self.fish}"'.encode(), after.data)
        removed = self.client.post('/collection/set',data={
            '_csrf':'valid-csrf','category':'fish','name':self.fish,'have':'0'},
            headers={'Accept':'application/json'})
        self.assertFalse(removed.json['owned'])
        reset = self.client.get('/collections/fish?month=all&status=missing')
        self.assertIn(b'80 results', reset.data)
        with sqlite3.connect(self.app.config['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM owned_items WHERE user_id=?',
                                        (self.uid,)).fetchone()[0],0)

    def test_money_tree_timeline_and_preservation(self):
        first = self.client.get('/checklist')
        self.assertEqual(first.status_code,200)
        self.assertIn(b'Seven-day money tree history', first.data)
        self.assertEqual(first.data.count(b'class="tree-day"'),7)
        recorded = self.client.post('/checklist/tree',data={
            '_csrf':'valid-csrf','day':'2026-11-22','outcome':'fertile', 'week':'0'})
        self.assertEqual(recorded.status_code,302)
        with sqlite3.connect(self.app.config['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT outcome FROM money_tree_journal WHERE user_id=? AND day=?',
                (self.uid,'2026-11-22')).fetchone()[0], 'fertile')
        self.assertEqual(self.client.get('/checklist?week=1').status_code,200)
        removed = self.client.post('/checklist/tree',data={
            '_csrf':'valid-csrf','day':'2026-11-22','outcome':'remove', 'week':'0'})
        self.assertEqual(removed.status_code,302)


if __name__ == '__main__':
    unittest.main()
