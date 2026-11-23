"""Regression coverage for feature completion work."""
import io
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


class TemplateFeatureTests(unittest.TestCase):
    def test_diy_icons_are_category_specific(self):
        env = Environment(loader=FileSystemLoader(str(TEMPLATES)), undefined=StrictUndefined)
        env.globals['url_for'] = lambda endpoint, **kw: '/' + endpoint
        icons = {'Tool':'🧰','Houseware':'🏠','Miscellaneous':'📦',
                 'Wall-mounted':'🧱','Wallpaper / Flooring / Rug':'🧵',
                 'Equipment':'👕','Other':'🪵'}
        for kind, symbol in icons.items():
            item = {'name':kind,'position':1,'price':None,'image':False,'owned':False,
                    'meta':{'type':kind}}
            with self.subTest(kind=kind):
                html = str(env.get_template('_item_card.html').module.item_card(item,'diy','csrf','north'))
                self.assertIn(symbol,html)
                self.assertIn('recipe-type-icon',html)
                self.assertIn('data-item-category="diy"',html)
                self.assertIn('data-item-name=',html)

    def test_templates_parse_including_restore_and_mobile_menu(self):
        env = Environment(loader=FileSystemLoader(str(TEMPLATES)))
        for template in TEMPLATES.glob('*.html'):
            with self.subTest(template=template.name):
                env.parse(template.read_text())
        self.assertIn('mobile-menu-toggle', (TEMPLATES/'base.html').read_text())
        self.assertIn('restore_full_backup', (TEMPLATES/'data.html').read_text())
        self.assertIn('tree_extend', (TEMPLATES/'checklist.html').read_text())

    def test_offline_script_has_pending_and_server_acknowledgment(self):
        script = (TEMPLATES.parent/'static'/'app.js').read_text()
        self.assertIn('queueLocalChange',script)
        self.assertIn('syncOfflineQueue',script)
        self.assertIn('changeCard',script)
        self.assertIn('data-item-name', (TEMPLATES/'_item_card.html').read_text())
        self.assertIn('offlineInstall',script)


@unittest.skipIf(create_app is None, 'Flask runtime is not installed locally; this test runs in GitHub Actions')
class FlaskFeatureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with patch.dict(os.environ, {'DATA_DIR':temp.name, 'TZ':'America/Chicago'}):
            self.app = create_app({'TESTING':True})
        from werkzeug.security import generate_password_hash
        self.password = 'this-is-a-test-admin-password'
        with sqlite3.connect(self.app.config['DATABASE']) as db:
            self.uid = db.execute('''INSERT INTO users(username,password_hash,display_name,is_admin)
                            VALUES(?,?,?,1)''',('adminuser',generate_password_hash(self.password),'Admin')).lastrowid
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['uid']=self.uid
            sess['csrf']='v14-csrf'

    def test_money_tree_extend_days_and_history(self):
        for action in ['add','add','remove']:
            result = self.client.post('/checklist/tree-extend', data={'_csrf':'v14-csrf', 'action':action})
            self.assertEqual(result.status_code,302)
        with sqlite3.connect(self.app.config['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM money_tree_journal WHERE user_id=?',
                                        (self.uid,)).fetchone()[0],1)
        html = self.client.get('/checklist')
        self.assertEqual(html.status_code,200)
        self.assertIn(b'Add day',html.data)
        self.assertIn(b'Remove latest day',html.data)

    def test_complete_backup_preview_rejects_invalid_and_restores(self):
        with sqlite3.connect(self.app.config['DATABASE']) as db:
            db.execute('INSERT INTO owned_items VALUES(?,?,?)',(self.uid,'fish','Anchovy'))
        backup = self.client.get('/data/complete-backup')
        self.assertEqual(backup.status_code,200)
        payload = backup.data
        with sqlite3.connect(self.app.config['DATABASE']) as db:
            db.execute('DELETE FROM owned_items WHERE user_id=?',(self.uid,))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM owned_items').fetchone()[0],0)
        wrong = self.client.post('/data/restore-complete-backup', data={
            '_csrf':'v14-csrf','password':'incorrect','mode':'restore','confirm':'RESTORE',
            'file':(io.BytesIO(payload),'backup.zip')}, content_type='multipart/form-data')
        self.assertEqual(wrong.status_code,302)
        preview = self.client.post('/data/restore-complete-backup', data={
            '_csrf':'v14-csrf','password':self.password,'mode':'preview',
            'file':(io.BytesIO(payload),'backup.zip')}, content_type='multipart/form-data')
        self.assertEqual(preview.status_code,302)
        with sqlite3.connect(self.app.config['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM owned_items').fetchone()[0],0)
        applied = self.client.post('/data/restore-complete-backup', data={
            '_csrf':'v14-csrf','password':self.password,'mode':'restore','confirm':'RESTORE',
            'file':(io.BytesIO(payload),'backup.zip')}, content_type='multipart/form-data')
        self.assertEqual(applied.status_code,302)
        self.assertIn('/login', applied.headers['Location'])
        with sqlite3.connect(self.app.config['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM owned_items').fetchone()[0],1)
        self.assertTrue((Path(self.app.config['DATA_DIR'])/'backups'/'pre-restore.sqlite3').exists())
        self.assertEqual(self.client.get('/collections/fish').status_code,302)

if __name__=='__main__':unittest.main()
