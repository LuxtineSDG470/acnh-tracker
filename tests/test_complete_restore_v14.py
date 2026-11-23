"""Offline, no-Flask tests for complete-backup validation and restoration."""
import io
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.restore import inspect_complete_backup, restore_complete_backup
from app.sqlite_backup import make_backup

SCHEMA = """
CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT, display_name TEXT, is_admin INTEGER, hemisphere TEXT, theme TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE catalog_items(category TEXT, name TEXT, position INTEGER, price INTEGER, metadata TEXT, PRIMARY KEY(category,name));
CREATE TABLE catalog_images(category TEXT, name TEXT, path TEXT, PRIMARY KEY(category,name));
CREATE TABLE owned_items(user_id INTEGER REFERENCES users(id), category TEXT, name TEXT, PRIMARY KEY(user_id,category,name));
CREATE TABLE daily_tasks(user_id INTEGER REFERENCES users(id), day TEXT, task_key TEXT, PRIMARY KEY(user_id,day,task_key));
CREATE TABLE money_tree_journal(user_id INTEGER REFERENCES users(id), day TEXT, outcome TEXT, PRIMARY KEY(user_id,day));
"""
# Valid PNG file header + tiny 1x1 test image.
PNG = bytes.fromhex('89504e470d0a1a0a') + b'png-test-bytes'


def sample_db(path, user='Alice', item='Anchovy'):
    with sqlite3.connect(path) as con:
        con.executescript(SCHEMA)
        con.execute('INSERT INTO users(id,username,password_hash,display_name,is_admin,hemisphere,theme) VALUES(1,?,?,?,?,?,?)', (user, 'hash', user, 1, 'north','dark'))
        con.execute('INSERT INTO catalog_items VALUES(?,?,?,?,?)', ('fish',item,1,200,'{}'))
        con.execute('INSERT INTO catalog_images VALUES(?,?,?)', ('fish',item,'fish/Anchovy.png'))
        con.execute('INSERT INTO owned_items VALUES(?,?,?)', (1,'fish',item))
        con.execute('INSERT INTO daily_tasks VALUES(?,?,?)', (1,'2026-11-22','moneyRock'))
        con.execute('INSERT INTO money_tree_journal VALUES(?,?,?)', (1,'2026-11-22','fertile'))


def sample_archive(db_bytes, artwork=PNG, bad_name=None):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('tracker.sqlite3',db_bytes)
        z.writestr(bad_name or 'artwork/fish/Anchovy.png',artwork)
        z.writestr('RESTORE.txt','Private backup')
    return output.getvalue()


class CompleteRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        old=self.root/'old.sqlite3'
        sample_db(old,'Restored','Anchovy')
        self.blob=sample_archive(old.read_bytes())

    def test_inspect_valid_backup_counts_without_mutation(self):
        result=inspect_complete_backup(self.blob)
        self.assertEqual(result['counts']['users'],1)
        self.assertEqual(result['counts']['owned_items'],1)
        self.assertEqual(result['counts']['money_tree_journal'],1)
        self.assertIn('fish/Anchovy.png',result['artwork'])

    def test_restore_all_tables_and_artwork_and_keep_previous_snapshot(self):
        data=self.root/'data';data.mkdir()
        current=data/'tracker.sqlite3';sample_db(current,'Current','Anchovy')
        with sqlite3.connect(current) as con:
            self.assertEqual(con.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower(), 'wal')
        (data/'artwork'/'fish').mkdir(parents=True)
        (data/'artwork'/'fish'/'Anchovy.png').write_bytes(b'old image')
        safety=restore_complete_backup(inspect_complete_backup(self.blob),str(current),str(data))
        self.assertTrue(safety.is_file())
        artwork_safety=data/'backups'/'pre-restore-artwork.zip'
        self.assertTrue(artwork_safety.is_file())
        with zipfile.ZipFile(artwork_safety) as existing:
            self.assertEqual(existing.read('fish/Anchovy.png'),b'old image')
        self.assertEqual((data/'artwork'/'fish'/'Anchovy.png').read_bytes(),PNG)
        with sqlite3.connect(current) as con:
            self.assertEqual(con.execute('SELECT username FROM users').fetchone()[0],'Restored')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM owned_items').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT theme FROM users').fetchone()[0],'dark')
        with sqlite3.connect(safety) as con:
            self.assertEqual(con.execute('SELECT username FROM users').fetchone()[0],'Current')
        # A second restore remains idempotent for the user's data.
        restore_complete_backup(inspect_complete_backup(self.blob),str(current),str(data))
        with sqlite3.connect(current) as con:
            self.assertEqual(con.execute('SELECT username FROM users').fetchone()[0],'Restored')

    def test_malicious_paths_and_missing_image_rejected_before_restore(self):
        old=self.root/'old.sqlite3'
        for candidate in ['artwork/../outside.png','artwork/fish/nope.png']:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    inspect_complete_backup(sample_archive(old.read_bytes(),bad_name=candidate))

    def test_reject_bad_sqlite_without_touching_data(self):
        invalid=sample_archive(b'SQLite format 3\x00'+b'garbage')
        with self.assertRaises(ValueError): inspect_complete_backup(invalid)
        self.assertTrue((self.root/'old.sqlite3').exists())

    def test_reject_unknown_paths_and_duplicate_entries(self):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as z:
            z.writestr('tracker.sqlite3',(self.root/'old.sqlite3').read_bytes())
            z.writestr('artwork/fish/Anchovy.png',PNG)
            z.writestr('private.env','secret')
        with self.assertRaises(ValueError): inspect_complete_backup(output.getvalue())
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as z:
            z.writestr('tracker.sqlite3',(self.root/'old.sqlite3').read_bytes())
            z.writestr('tracker.sqlite3',(self.root/'old.sqlite3').read_bytes())
        with self.assertRaises(ValueError): inspect_complete_backup(output.getvalue())

if __name__=='__main__': unittest.main()
