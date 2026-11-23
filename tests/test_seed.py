"""Regression tests for automatic collection/artwork seeding and safe updates."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import zipfile
import unittest
from collections import Counter
from pathlib import Path

from app.catalog import read_open_catalog
from app.seed import BUNDLE_DIR, IMAGE_PACK, inspect_bundled_data, install_bundled_data


def fresh_database(filename: Path):
    with sqlite3.connect(filename) as db:
        db.executescript('''CREATE TABLE catalog_items (
            category TEXT NOT NULL, name TEXT NOT NULL, position INTEGER NOT NULL,
            price INTEGER, metadata TEXT NOT NULL, PRIMARY KEY(category,name));
            CREATE TABLE catalog_images (
            category TEXT NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL,
            PRIMARY KEY(category,name));
            CREATE TABLE owned_items (
            user_id INTEGER NOT NULL, category TEXT NOT NULL, name TEXT NOT NULL,
            PRIMARY KEY(user_id,category,name));''')


class SeedTests(unittest.TestCase):
    def test_bundle_has_expected_catalog_and_usable_images(self):
        raw = (BUNDLE_DIR / 'catalog.json').read_bytes()
        data = json.loads(raw)
        records = read_open_catalog(raw)
        self.assertEqual(len(records), 939)
        self.assertEqual(Counter(row[0] for row in records), {
            'fish': 80, 'bugs': 80, 'deepsea': 40, 'fossil': 73,
            'art': 43, 'diy': 623,
        })
        self.assertEqual(len(data['image_mappings']), 887)
        self.assertTrue((BUNDLE_DIR / IMAGE_PACK).is_file())
        items, links, paths = inspect_bundled_data()
        self.assertEqual(len(items), 939)
        self.assertEqual(len(links), 887)
        self.assertEqual(len(paths), 923)
        with zipfile.ZipFile(BUNDLE_DIR / IMAGE_PACK) as images:
            self.assertEqual(len(images.namelist()), 923)
            self.assertTrue(all(i.file_size > 0 for i in images.infolist()))
        self.assertFalse(list(BUNDLE_DIR.rglob('.env')))
        self.assertFalse(list(BUNDLE_DIR.rglob('*.ts')))
        self.assertFalse(list(BUNDLE_DIR.rglob('*.tsx')))
        self.assertEqual(list(BUNDLE_DIR.rglob('*.zip')), [BUNDLE_DIR / IMAGE_PACK])

    def test_missing_artwork_has_specific_error_before_database_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / 'bundle'
            bundle.mkdir()
            (bundle / 'catalog.json').write_bytes((BUNDLE_DIR / 'catalog.json').read_bytes())
            with zipfile.ZipFile(BUNDLE_DIR / IMAGE_PACK) as src, zipfile.ZipFile(bundle / IMAGE_PACK, 'w') as out:
                for name in src.namelist():
                    if name != 'fish/Anchovy.png':
                        out.writestr(name, src.read(name))
            database = root / 'test.sqlite3'
            fresh_database(database)
            with self.assertRaisesRegex(ValueError, 'fish/Anchovy.png'):
                install_bundled_data(str(database), str(root), bundle)
            with sqlite3.connect(database) as con:
                self.assertEqual(con.execute('SELECT count(*) FROM catalog_items').fetchone()[0], 0)

    def test_new_and_existing_installations_preserve_user_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / 'tracker.sqlite3'
            fresh_database(database)
            initial = install_bundled_data(str(database), str(root))
            self.assertEqual(initial['catalog_total'], 939)
            self.assertEqual(initial['catalog_added'], 939)
            self.assertEqual(initial['images_copied'], 923)
            self.assertEqual(initial['images_mapped'], 887)
            with sqlite3.connect(database) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM catalog_images').fetchone()[0], 887)
                first = con.execute("SELECT name FROM catalog_items WHERE category='fish' LIMIT 1").fetchone()[0]
                con.execute('INSERT INTO owned_items VALUES(?,?,?)', (7, 'fish', first))
                con.execute("UPDATE catalog_items SET price=999999 WHERE category='fish' AND name=?", (first,))
                con.execute("UPDATE catalog_images SET path='custom/fish-custom.png' WHERE category='fish' AND name=?", (first,))
            # A user may also replace the content of an existing named asset.
            image = root / 'artwork' / 'fish' / 'Anchovy.png'
            self.assertTrue(image.exists())
            image.write_bytes(b'user replacement is protected')
            repeat = install_bundled_data(str(database), str(root))
            self.assertEqual(repeat['catalog_added'], 0)
            self.assertEqual(repeat['images_copied'], 0)
            self.assertEqual(image.read_bytes(), b'user replacement is protected')
            with sqlite3.connect(database) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM owned_items').fetchone()[0], 1)
                self.assertEqual(con.execute('SELECT price FROM catalog_items WHERE category=? AND name=?',
                                             ('fish', first)).fetchone()[0], 999999)
                self.assertEqual(con.execute('SELECT path FROM catalog_images WHERE category=? AND name=?',
                                             ('fish', first)).fetchone()[0], 'custom/fish-custom.png')
                con.execute('DELETE FROM catalog_items WHERE category=? AND name=?', ('fish', first))
            # Upgrades repair missing seed records without clearing collection checkmarks.
            repair = install_bundled_data(str(database), str(root))
            self.assertEqual(repair['catalog_added'], 1)
            with sqlite3.connect(database) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM catalog_items').fetchone()[0], 939)
                self.assertEqual(con.execute('SELECT COUNT(*) FROM owned_items').fetchone()[0], 1)

    def test_empty_old_database_receives_catalog_automatically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / 'tracker.sqlite3'
            fresh_database(database)
            with sqlite3.connect(database) as con:
                con.execute('INSERT INTO owned_items VALUES(?,?,?)', (5, 'bugs', 'Agrias butterfly'))
            result = install_bundled_data(str(database), str(root))
            self.assertEqual(result['catalog_total'], 939)
            with sqlite3.connect(database) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM owned_items').fetchone()[0], 1)
                self.assertEqual(con.execute('SELECT COUNT(*) FROM catalog_items').fetchone()[0], 939)

if __name__ == '__main__':
    unittest.main()
