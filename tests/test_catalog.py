"""Current catalog schema tests; do not depend on old tracker formats."""
from __future__ import annotations
import json
import unittest
from app.catalog import CATEGORIES, read_open_catalog


class CatalogTests(unittest.TestCase):
    def test_current_catalog_categories(self):
        self.assertEqual(set(CATEGORIES), {'fish','bugs','deepsea','fossil','art','diy'})

    def test_normalized_catalog_schema(self):
        raw = json.dumps({'items':[{'category':'fish','name':'Sample fish','price':100,
            'metadata':{'months':{'north':'1'*12,'south':'0'*12}}}]}).encode()
        rows = read_open_catalog(raw)
        self.assertEqual(rows[0][:4], ('fish','Sample fish',0,100))
        self.assertEqual(len(rows),1)

    def test_explicit_position(self):
        raw = json.dumps({'items':[{'category':'fish','name':'Test','position':17}]}).encode()
        self.assertEqual(read_open_catalog(raw)[0][2],17)

    def test_rejects_bad_category(self):
        with self.assertRaises(ValueError):
            read_open_catalog(b'{"items":[{"category":"unknown","name":"test"}]}')

    def test_rejects_malformed_metadata(self):
        with self.assertRaises(ValueError):
            read_open_catalog(b'{"items":[{"category":"fish","name":"X","metadata":[] }]}')

    def test_rejects_negative_position(self):
        with self.assertRaises(ValueError):
            read_open_catalog(b'{"items":[{"category":"fish","name":"X","position":-1}]}')


if __name__ == '__main__':
    unittest.main()
