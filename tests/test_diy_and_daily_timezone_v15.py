"""v0.1.15 regression checks for progressive recipe browsing and local-day behavior."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader
from app.timezone_utils import valid_timezone, local_date

try:
    from app.web import create_app
except ModuleNotFoundError as exc:
    if exc.name in ('flask', 'werkzeug'):
        create_app = None
    else:
        raise


class TimezoneAndTemplateUnitTests(unittest.TestCase):
    def test_iana_validation_and_dst_local_day(self):
        self.assertTrue(valid_timezone('America/New_York'))
        self.assertTrue(valid_timezone('Australia/Sydney'))
        self.assertTrue(valid_timezone('UTC'))
        for bad in ('', 'Not/A/Timezone', '/etc/passwd', '../Etc/UTC', 'America/Chicago\n'):
            self.assertFalse(valid_timezone(bad))
        instant = datetime(2026, 11, 23, 3, 30, tzinfo=timezone.utc)
        # At the same instant it is still Nov 22 in North America.
        from zoneinfo import ZoneInfo
        self.assertEqual(instant.astimezone(ZoneInfo('America/Chicago')).date(), date(2026,11,22))
        self.assertEqual(instant.astimezone(ZoneInfo('Asia/Tokyo')).date(), date(2026,11,23))

    def test_templates_and_browser_enhancements(self):
        templates = Path(__file__).resolve().parents[1] / 'app' / 'templates'
        env = Environment(loader=FileSystemLoader(str(templates)))
        for path in templates.glob('*.html'):
            env.parse(path.read_text())
        page = (templates / 'collection.html').read_text()
        self.assertIn('Show all {{ total_filtered }}', page)
        self.assertIn('diy_more_url', page)
        settings = (templates / 'settings.html').read_text()
        self.assertIn('detect-timezone', settings)
        self.assertIn('daily_reset_mode', settings)
        script = (templates.parent/'static'/'app.js').read_text()
        self.assertIn("params.delete('limit')", script)
        self.assertIn('data-reset-mode="automatic"', script)


@unittest.skipIf(create_app is None, 'Flask/werkzeug unavailable locally; tests run in GitHub Actions')
class ApplicationBehaviorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.database = Path(temp.name)/'tracker.sqlite3'
        with patch.dict(os.environ, {'DATA_DIR':temp.name,'TZ':'America/Chicago'}):
            self.app = create_app({'TESTING':True})
        with sqlite3.connect(self.database) as con:
            self.uid = con.execute("INSERT INTO users(username,password_hash,display_name,is_admin) VALUES ('alice','unused','Alice',1)").lastrowid
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['uid'] = self.uid
            sess['csrf'] = 'test-token'

    def post(self, url, **fields):
        return self.client.post(url, data={'_csrf':'test-token', **fields})

    def test_diy_progressive_browsing_and_all_match_filters(self):
        initial = self.client.get('/collections/diy?sort=name')
        self.assertEqual(initial.status_code, 200, initial.data[:300])
        self.assertEqual(initial.data.count(b'data-item-category="diy"'), 60)
        self.assertIn(b'Show 60 more recipes', initial.data)
        self.assertIn(b'Show all 623', initial.data)
        next_batch = self.client.get('/collections/diy?limit=120&sort=name')
        self.assertEqual(next_batch.data.count(b'data-item-category="diy"'), 120)
        all_recipes = self.client.get('/collections/diy?view=all')
        self.assertEqual(all_recipes.status_code, 200)
        self.assertEqual(all_recipes.data.count(b'data-item-category="diy"'), 623)
        self.assertNotIn(b'Show 60 more recipes', all_recipes.data)
        filtered = self.client.get('/collections/diy?q=scorpio&view=all')
        self.assertEqual(filtered.status_code, 200)
        self.assertIn(b'Scorpio', filtered.data)
        self.assertNotIn(b'Show all 623', filtered.data)

    def test_account_timezone_preferences_validate_and_persist(self):
        invalid = self.post('/settings', hemisphere='north', theme='light', timezone='Nowhere/Unknown',daily_reset_mode='manual')
        self.assertEqual(invalid.status_code, 302)
        with sqlite3.connect(self.database) as con:
            self.assertIsNone(con.execute('SELECT timezone FROM users WHERE id=?',(self.uid,)).fetchone()[0])
        ok = self.post('/settings', hemisphere='south',theme='dark',timezone='Asia/Tokyo',daily_reset_mode='manual')
        self.assertEqual(ok.status_code, 302)
        with sqlite3.connect(self.database) as con:
            tz,mode = con.execute('SELECT timezone,daily_reset_mode FROM users WHERE id=?',(self.uid,)).fetchone()
        self.assertEqual((tz,mode),('Asia/Tokyo','manual'))
        page = self.client.get('/settings')
        self.assertIn(b'Asia/Tokyo',page.data)
        checklist = self.client.get('/checklist')
        self.assertIn(b'Keeps checked tasks until you reset them',checklist.data)

    def test_manual_checkmarks_persist_and_mode_switch_keeps_them(self):
        with patch('app.web.local_date', return_value=date(2026,11,22)):
            self.post('/checklist/task', task='moneyRock', done='1')
            self.post('/settings', hemisphere='north',theme='light',timezone='UTC',daily_reset_mode='manual')
        with sqlite3.connect(self.database) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_tasks WHERE user_id=? AND day='manual' AND task_key='moneyRock'",(self.uid,)).fetchone()[0],1)
        with patch('app.web.local_date', return_value=date(2026,11,23)):
            self.assertIn(b'class="task-form done"', self.client.get('/checklist').data)
            self.post('/settings', hemisphere='north',theme='light',timezone='UTC',daily_reset_mode='automatic')
        with sqlite3.connect(self.database) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_tasks WHERE user_id=? AND day='2026-11-23' AND task_key='moneyRock'",(self.uid,)).fetchone()[0],1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_tasks WHERE user_id=? AND day='2026-11-22' AND task_key='moneyRock'",(self.uid,)).fetchone()[0],1)

    def test_two_users_in_different_timezones_have_independent_local_days(self):
        with sqlite3.connect(self.database) as con:
            bob = con.execute("INSERT INTO users(username,password_hash,display_name,is_admin,timezone) VALUES ('bob','unused','Bob',0,'Asia/Tokyo')").lastrowid
            con.execute('UPDATE users SET timezone=? WHERE id=?',('America/Chicago',self.uid))
        def mocked_day(zone):
            return date(2026,11,23) if zone == 'Asia/Tokyo' else date(2026,11,22)
        with patch('app.web.local_date', side_effect=mocked_day):
            self.post('/checklist/task', task='moneyTree',done='1')
            with self.client.session_transaction() as sess:
                sess['uid']=bob
                sess['csrf']='test-token'
            self.post('/checklist/task', task='moneyTree',done='1')
        with sqlite3.connect(self.database) as con:
            rows=con.execute("SELECT user_id,day FROM daily_tasks WHERE task_key='moneyTree' ORDER BY user_id").fetchall()
        self.assertEqual(rows,[(self.uid,'2026-11-22'),(bob,'2026-11-23')])


class DatabaseSchemaUpgradeTests(unittest.TestCase):
    @unittest.skipIf(create_app is None, 'Flask unavailable locally; upgrade verified in GitHub Actions')
    def test_upgrade_from_v14_keeps_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'tracker.sqlite3'
            with sqlite3.connect(path) as con:
                con.executescript('''CREATE TABLE users (id INTEGER PRIMARY KEY,username TEXT,password_hash TEXT,display_name TEXT,is_admin INTEGER,hemisphere TEXT,theme TEXT,created_at TEXT);
                    CREATE TABLE daily_tasks (user_id INTEGER,day TEXT,task_key TEXT,PRIMARY KEY(user_id,day,task_key));''')
                con.execute("INSERT INTO users VALUES (1,'alice','hash','Alice',1,'north','light','2026-11-01')")
                con.execute("INSERT INTO daily_tasks VALUES (1,'2026-11-22','moneyRock')")
            with patch.dict(os.environ, {'DATA_DIR':directory, 'TZ':'America/Chicago'}):
                create_app({'TESTING':True})
            with sqlite3.connect(path) as con:
                row=con.execute('SELECT username,timezone,daily_reset_mode FROM users').fetchone()
                self.assertEqual(row, ('alice',None,'automatic'))
                self.assertEqual(con.execute('SELECT COUNT(*) FROM daily_tasks').fetchone()[0],1)

if __name__ == '__main__':
    unittest.main()
