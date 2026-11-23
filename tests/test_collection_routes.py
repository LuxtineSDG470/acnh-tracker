"""Authenticated Flask page tests using the shipped 939-item catalog.

Runs under GitHub Actions (requirements.txt installed) and catches failures
that unauthenticated /login and /health smoke checks cannot detect.
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from app.web import create_app
except ModuleNotFoundError as exc:
    if exc.name in ('flask', 'werkzeug'):
        create_app = None
    else:
        raise


@unittest.skipIf(create_app is None, 'Flask is unavailable outside the CI/runtime environment')
class AuthenticatedPagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {'DATA_DIR':self.temp.name, 'TZ':'America/Chicago'}):
            self.app = create_app({'TESTING': True})
        with sqlite3.connect(self.app.config['DATABASE']) as conn:
            cursor = conn.execute("INSERT INTO users(username,password_hash,display_name,is_admin,hemisphere) "
                                  "VALUES ('tester','unused','Tester',1,'north')")
            self.uid = cursor.lastrowid
            self.first_fish = conn.execute("SELECT name FROM catalog_items WHERE category='fish' LIMIT 1").fetchone()[0]
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM catalog_items').fetchone()[0], 939)
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['uid'] = self.uid
            sess['csrf'] = 'test-csrf-token'

    def test_overview_excludes_diy_but_shows_separate_recipe_progress(self):
        with sqlite3.connect(self.app.config['DATABASE']) as conn:
            for category, limit in (('fish', 10), ('diy', 16)):
                names = [row[0] for row in conn.execute(
                    'SELECT name FROM catalog_items WHERE category=? LIMIT ?', (category, limit))]
                conn.executemany(
                    'INSERT INTO owned_items(user_id,category,name) VALUES (?,?,?)',
                    ((self.uid, category, name) for name in names))
            main_count = conn.execute(
                "SELECT COUNT(*) FROM catalog_items WHERE category!='diy'").fetchone()[0]
            recipe_count = conn.execute(
                "SELECT COUNT(*) FROM catalog_items WHERE category='diy'").fetchone()[0]
        self.assertEqual(main_count, 316)
        self.assertEqual(recipe_count, 623)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200, response.data[:300])
        self.assertIn(b'<span class="muted">Collected</span><strong>10</strong>', response.data)
        self.assertIn(f'<span class="muted">In the catalog</span><strong>{main_count}</strong>'.encode(), response.data)
        self.assertIn(b'<span class="muted">Completion</span><strong>3%</strong>', response.data)
        self.assertIn(b'16 / 623 collected', response.data)
        self.assertIn(b'excluding DIY', response.data)

    def test_collection_filters_include_offcanvas_drawer_controls(self):
        response = self.client.get('/collections/fish?month=all')
        self.assertEqual(response.status_code, 200, response.data[:300])
        self.assertIn(b'id="filter-drawer-toggle"', response.data)
        self.assertIn(b'aria-expanded="false"', response.data)
        self.assertIn(b'id="filter-drawer-backdrop" hidden', response.data)
        self.assertIn(b'id="filter-drawer-close"', response.data)
        self.assertIn(b'class="filters panel filters-sidebar filter-drawer"', response.data)
        self.assertIn(b'name="hemisphere"', response.data)

    def test_all_six_collection_pages_render_with_populated_cards(self):
        for category in ('fish','bugs','deepsea','fossil','art','diy'):
            with self.subTest(category=category):
                response = self.client.get(f'/collections/{category}?month=all')
                self.assertEqual(response.status_code, 200, response.data[:500])
                self.assertIn(b'Mark collected', response.data)

    def test_seasonal_group_and_ability_to_browse_other_months(self):
        response = self.client.get('/collections/fish?month=5')
        self.assertEqual(response.status_code, 200, response.data[:500])
        self.assertIn(b'Available in May', response.data)
        self.assertIn(b'Always Available', response.data)
        self.assertIn(b'New', response.data)
        self.assertNotIn(b'Available in November', response.data)

    def test_paintings_and_sculptures_are_separate(self):
        response = self.client.get('/collections/art?month=all')
        self.assertEqual(response.status_code, 200, response.data[:500])
        self.assertIn(b'Paintings', response.data)
        self.assertIn(b'Sculptures', response.data)

    def test_size_labels_are_readable(self):
        response = self.client.get('/collections/fish?month=all')
        self.assertEqual(response.status_code, 200, response.data[:500])
        self.assertNotIn(b'<strong>5</strong>', response.data)
        self.assertNotIn(b'<strong>6</strong>', response.data)

    def test_bunny_day_renders_cards(self):
        response = self.client.get('/events/bunny')
        self.assertEqual(response.status_code, 200, response.data[:500])
        self.assertIn(b'Bunny Day', response.data)
        self.assertIn(b'collect-form', response.data)

    def test_sidebar_hemisphere_filter_preserves_account_setting(self):
        north = self.client.get('/collections/fish?month=5&hemisphere=north')
        south = self.client.get('/collections/fish?month=5&hemisphere=south')
        self.assertEqual(north.status_code, 200)
        self.assertEqual(south.status_code, 200)
        self.assertIn(b'name="hemisphere"', south.data)
        self.assertIn(b'Southern Hemisphere', south.data)
        self.assertIn(b'Available in May', south.data)
        with sqlite3.connect(self.app.config['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT hemisphere FROM users WHERE id=?',
                                        (self.uid,)).fetchone()[0], 'north')
        # /collections/<category> is still a GET route. The retired POST
        # endpoint therefore returns 405 (method not allowed), not 404.
        # A GET with the nonexistent category still correctly returns 404.
        retired = self.client.post('/collections/hemisphere',
            data={'_csrf':'test-csrf-token','hemisphere':'south','category':'fish'})
        self.assertEqual(retired.status_code, 405)
        self.assertEqual(self.client.get('/collections/hemisphere').status_code, 404)
        with sqlite3.connect(self.app.config['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT hemisphere FROM users WHERE id=?',
                                        (self.uid,)).fetchone()[0], 'north')

    def test_southern_hemisphere_and_collecting_item(self):
        with sqlite3.connect(self.app.config['DATABASE']) as conn:
            conn.execute("UPDATE users SET hemisphere='south' WHERE id=?", (self.uid,))
        response = self.client.get('/collections/fish?month=all')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Southern Hemisphere', response.data)
        result = self.client.post('/collection/set', data={
            '_csrf':'test-csrf-token', 'category':'fish',
            'name':self.first_fish, 'have':'1'}, follow_redirects=True)
        self.assertEqual(result.status_code, 200, result.data[:500])
        with sqlite3.connect(self.app.config['DATABASE']) as conn:
            count = conn.execute("SELECT COUNT(*) FROM owned_items WHERE user_id=? AND category='fish' AND name=?",
                                 (self.uid, self.first_fish)).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == '__main__':
    unittest.main()
