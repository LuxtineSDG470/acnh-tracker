"""v0.1.16 regression checks: downloaded file names and retired Data panels."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader
from app.downloads import timestamped_filename

try:
    from app.web import create_app
except ModuleNotFoundError as exc:
    if exc.name in ('flask', 'werkzeug'):
        create_app = None
    else:
        raise

ROOT = Path(__file__).resolve().parents[1]


class FilenameAndTemplateTests(unittest.TestCase):
    def test_timestamp_filenames_are_sortable_safe_and_offset_disambiguated(self):
        chicago = ZoneInfo('America/Chicago')
        instant = datetime(2026, 11, 23, 2, 32, 15, tzinfo=timezone.utc)
        local = instant.astimezone(chicago)
        self.assertEqual(timestamped_filename('acnh-progress', 'json', local),
                         'acnh-progress-2026-11-22_20-32-15-0600.json')
        self.assertNotRegex(timestamped_filename('acnh-progress', 'json', local), r'[:/\\]')
        a = datetime(2026, 11, 1, 1, 30, tzinfo=chicago, fold=0)
        b = datetime(2026, 11, 1, 1, 30, tzinfo=chicago, fold=1)
        self.assertNotEqual(timestamped_filename('acnh-progress', 'json', a),
                            timestamped_filename('acnh-progress', 'json', b))

    def test_timestamp_requires_timezone(self):
        with self.assertRaises(ValueError):
            timestamped_filename('acnh-progress', 'json', datetime(2026, 11, 22, 20, 32))

    def test_retired_data_sections_are_gone_but_backup_and_import_remain(self):
        page = (ROOT / 'app/templates/data.html').read_text()
        template_dir = ROOT / 'app/templates'
        env = Environment(loader=FileSystemLoader(str(template_dir)))
        env.get_template('data.html')
        for gone in ('Finish missing images', 'Collection catalog', 'Migrate old SQLite collection progress',
                     'import_catalog', 'upload_manual_artwork', 'migrate_previous_database'):
            self.assertNotIn(gone, page)
        for current in ('Export your progress', 'Import collection progress',
                        'Restore full backup ZIP', 'Download full backup'):
            self.assertIn(current, page)


@unittest.skipIf(create_app is None, 'Flask/werkzeug unavailable locally; validated in GitHub Actions')
class DownloadsAndRoutesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with patch.dict(os.environ, {'DATA_DIR': temp.name, 'TZ': 'America/Chicago'}):
            self.app = create_app({'TESTING': True})
        db = self.app.config['DATABASE']
        with sqlite3.connect(db) as con:
            self.uid = con.execute("INSERT INTO users(username,password_hash,display_name,is_admin,timezone) VALUES ('tester','unused','Tester',1,'Asia/Tokyo')").lastrowid
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['uid'] = self.uid
            sess['csrf'] = 'test-token'

    def test_personal_export_has_matching_timezone_timestamp_in_name_and_json(self):
        response = self.client.get('/data/export')
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.data)
        generated = datetime.fromisoformat(body['exportDate'])
        expected = timestamped_filename('acnh-progress', 'json', generated)
        self.assertIn(expected, response.headers['Content-Disposition'])
        self.assertEqual(generated.utcoffset().total_seconds(), 9 * 3600)

    def test_backups_have_time_stamped_files_and_keep_valid_payloads(self):
        database = self.client.get('/data/database-backup')
        self.assertEqual(database.status_code, 200)
        self.assertRegex(database.headers['Content-Disposition'],
                         r'acnh-tracker-backup-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\+0900\.sqlite3')
        self.assertTrue(database.data.startswith(b'SQLite format 3'))
        archive = self.client.get('/data/complete-backup')
        self.assertEqual(archive.status_code, 200)
        self.assertRegex(archive.headers['Content-Disposition'],
                         r'acnh-tracker-complete-backup-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\+0900\.zip')
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(archive.data)) as z:
            self.assertIn('tracker.sqlite3', z.namelist())

    def test_removed_endpoints_are_not_available(self):
        page = self.client.get('/data')
        self.assertEqual(page.status_code, 200)
        for old in ('/data/import-catalog', '/data/artwork/upload', '/data/migrate-database'):
            response = self.client.post(old, data={'_csrf': 'test-token'})
            self.assertEqual(response.status_code, 404, old)
        self.assertIn(b'Import collection progress', page.data)


if __name__ == '__main__':
    unittest.main()
