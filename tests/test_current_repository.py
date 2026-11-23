"""Guard against accidentally restoring retired importer/UI paths."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent


class CurrentRepositoryTests(unittest.TestCase):
    def test_old_import_modules_are_retired(self):
        for name in ('app/artwork.py', 'app/migration.py',
                     'scripts/build_bundled_catalog.py'):
            with self.subTest(name=name):
                self.assertFalse((ROOT / name).exists())

    def test_current_bundle_and_active_backup_engine_remain(self):
        for name in ('bundle/catalog.json', 'bundle/artwork-pack.zip',
                     'app/seed.py', 'app/restore.py', 'app/sqlite_backup.py',
                     'tests/test_complete_restore_v14.py'):
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_file())

    def test_hemisphere_sidebar_replaced_previous_header_form(self):
        template = (ROOT / 'app/templates/collection.html').read_text()
        web = (ROOT / 'app/web.py').read_text()
        self.assertIn('name="hemisphere"', template)
        self.assertIn('class="collection-layout"', template)
        self.assertNotIn('hemisphere-switch', template)
        self.assertNotIn("@app.post('/collections/hemisphere')", web)

    def test_no_old_catalog_import_messages_in_user_facing_templates(self):
        for name in ('auth.html', 'home.html', 'bunny.html'):
            with self.subTest(name=name):
                text = (ROOT / 'app/templates' / name).read_text()
                self.assertNotIn('import a catalog', text.lower())
                self.assertNotIn('import your existing zip', text.lower())


if __name__ == '__main__':
    unittest.main()
