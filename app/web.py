"""Flask web app implementing the ACNH Tracker requirements in Python."""
from __future__ import annotations

import calendar
from difflib import SequenceMatcher
import io
import zipfile
import functools
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .catalog import CATEGORIES
from .database import close_db, get_db, init_db, make_backup
from .seed import install_bundled_data
from .timezone_utils import timezone_name, local_date, valid_timezone
from .display import (display_metadata, calendar_badges, currently_catchable)
from .downloads import timestamped_filename
from .restore import inspect_complete_backup, restore_complete_backup as apply_complete_backup
from zoneinfo import ZoneInfo

TASKS = [
    ('moneyTree', 'Money tree', '🌱'), ('moneyRock', 'Money rock', '🪨'),
    ('harvestFruit', 'Harvest fruit', '🍎'), ('sellFruit', 'Sell fruit', '🧺'),
    ('turnipPrices', 'Check turnip prices', '📈'), ('nookStop', 'Nook Stop terminal', '🏧'),
    ('fossils', 'Collect fossils', '🦴'), ('messageInABotttle', 'Message in a bottle', '✉️'),
    ('talkToVillagers', 'Talk to villagers', '💬'), ('recyclingBin', 'Recycling bin', '♻️'),
    ('islandVisitors', 'Island visitors', '🏝️'), ('treeFurniture', 'Shake trees for furniture', '🌳'),
]
TASK_KEYS = {k for k, _, _ in TASKS}
ICONS = {'fish':'🐟','bugs':'🦋','deepsea':'🐙','fossil':'🦴','art':'🖼️','diy':'🛠️'}
MONTHS = list(enumerate(calendar.month_abbr))[1:]
SIZES = (12, 24, 48, 96)
_lock = threading.Lock()
_restore_lock = threading.Lock()


def create_app(test_config=None):
    app = Flask(__name__)
    data_dir = Path(os.environ.get('DATA_DIR', '/data'))
    data_dir.mkdir(parents=True, exist_ok=True)
    secret_path = data_dir / '.flask-secret'
    if not secret_path.exists():
        try:
            with secret_path.open('x') as f:
                f.write(secrets.token_hex(48))
            secret_path.chmod(0o600)
        except FileExistsError:
            pass
    app.config.update(
        SECRET_KEY=os.environ.get('SECRET_KEY') or secret_path.read_text().strip(),
        DATABASE=str(data_dir / 'tracker.sqlite3'),
        DATA_DIR=str(data_dir),
        MAX_CONTENT_LENGTH=80_000_000,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('SECURE_COOKIES', '0') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        ALLOW_REGISTRATION=os.environ.get('ALLOW_REGISTRATION', '1') == '1',
        TIMEZONE=timezone_name(os.environ.get('TZ')),
        OFFLINE_NAMESPACE=hashlib.sha256(secret_path.read_bytes()).hexdigest()[:18],
    )
    if test_config:
        app.config.update(test_config)
    init_db(app.config['DATABASE'])
    seed_result = install_bundled_data(app.config['DATABASE'], app.config['DATA_DIR'])
    app.logger.info('Bundled collection initialization: %s', seed_result)
    app.teardown_appcontext(close_db)

    def user_timezone():
        """Use an account's validated timezone, or the Docker TZ by default."""
        user = getattr(g, 'user', None)
        configured = user['timezone'] if user is not None else None
        return configured if valid_timezone(configured) else app.config['TIMEZONE']

    def local_today():
        return local_date(user_timezone())

    def download_name(stem: str, extension: str) -> str:
        return timestamped_filename(stem, extension, datetime.now(ZoneInfo(user_timezone())))

    def task_day():
        """Manual checklists persist until explicitly reset; automatic are date keyed."""
        if g.user['daily_reset_mode'] == 'manual':
            return 'manual'
        return local_today().isoformat()

    def owned_names(category):
        return {r['name'] for r in get_db().execute(
            'SELECT name FROM owned_items WHERE user_id=? AND category=?',
            (g.user['id'], category))}

    def stats():
        result = {}
        for cat in CATEGORIES:
            r = get_db().execute('''SELECT COUNT(*) total, COUNT(o.name) owned FROM catalog_items c
                LEFT JOIN owned_items o ON o.category=c.category AND o.name=c.name AND o.user_id=?
                WHERE c.category=?''', (g.user['id'], cat)).fetchone()
            result[cat] = dict(total=r['total'], owned=r['owned'],
                               percent=round(100*r['owned']/r['total']) if r['total'] else 0)
        return result

    def requires_login(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            if g.user is None:
                if request.path.startswith('/api/'):
                    return jsonify(error='Sign in required'), 401
                return redirect(url_for('login'))
            return view(*args, **kwargs)
        return wrapper

    @app.context_processor
    def context():
        return {'categories':CATEGORIES, 'icons':ICONS, 'user':getattr(g,'user',None),
                'csrf': session.get('csrf'), 'current_year':local_today().year,
                'offline_namespace':app.config['OFFLINE_NAMESPACE']}

    @app.before_request
    def load_identity():
        if _restore_lock.locked() and request.endpoint != 'restore_full_backup':
            abort(503, 'Backup restoration in progress. Please try again shortly.')
        session.setdefault('csrf', secrets.token_hex(24))
        g.user = None
        if session.get('uid') is not None:
            g.user = get_db().execute('SELECT * FROM users WHERE id=?', (session['uid'],)).fetchone()
            if g.user is None:
                session.clear()
                session['csrf'] = secrets.token_hex(24)
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            submitted = request.headers.get('X-CSRF-Token') or request.form.get('_csrf')
            if not submitted or not secrets.compare_digest(submitted, session['csrf']):
                abort(400, 'Invalid security token. Refresh the page and try again.')
        # No scheduler required: a regular Docker health-check request triggers
        # a single rolling daily online backup, including WAL state.
        marker = Path(app.config['DATA_DIR']) / '.last-backup'
        if time.time() - marker.stat().st_mtime > 86400 if marker.exists() else True:
            if _lock.acquire(blocking=False):
                try:
                    if not marker.exists() or time.time() - marker.stat().st_mtime > 86400:
                        make_backup(app.config['DATABASE'], str(Path(app.config['DATA_DIR']) / 'backups' / 'tracker.sqlite3'))
                        marker.touch()
                except OSError:
                    app.logger.exception('Automatic database backup failed')
                finally:
                    _lock.release()

    @app.after_request
    def security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = ("default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        return response

    @app.errorhandler(413)
    def too_large(e):
        return render_template('notice.html', title='File too large', message='Upload a file smaller than 20 MB.'), 413

    @app.route('/health')
    def health():
        return 'ok', 200, {'Content-Type':'text/plain; charset=utf-8'}

    @app.route('/register', methods=['GET','POST'])
    def register():
        first = get_db().execute('SELECT COUNT(*) n FROM users').fetchone()['n'] == 0
        if not first and not app.config['ALLOW_REGISTRATION']:
            abort(403)
        if request.method == 'POST':
            name = request.form.get('username','').strip()
            password = request.form.get('password','')
            if not re.fullmatch('[A-Za-z0-9_-]{3,32}', name):
                flash('Username must be 3–32 letters, numbers, underscores or hyphens.', 'error')
            elif len(password) < 12 or len(password) > 256:
                flash('Use a password between 12 and 256 characters.', 'error')
            else:
                try:
                    with get_db():
                        get_db().execute('''INSERT INTO users(username,password_hash,display_name,is_admin)
                              VALUES(?,?,?,?)''', (name, generate_password_hash(password), name, int(first)))
                    flash('Account created. Sign in to begin.', 'success')
                    return redirect(url_for('login'))
                except Exception as exc:
                    if 'UNIQUE constraint failed' not in str(exc):
                        raise
                    flash('That username is already taken.', 'error')
        return render_template('auth.html', mode='register', first=first)

    @app.route('/login', methods=['GET','POST'])
    def login():
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            row = get_db().execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if row and check_password_hash(row['password_hash'], password):
                session.clear()
                session['uid'] = row['id']
                session['csrf'] = secrets.token_hex(24)
                session.permanent = True
                return redirect(url_for('index'))
            flash('Invalid username or password.', 'error')
        return render_template('auth.html', mode='login')

    @app.post('/logout')
    @requires_login
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    @requires_login
    def index():
        return render_template('home.html', stats=stats())

    @app.get('/collections/<category>')
    @requires_login
    def collection(category):
        if category not in CATEGORIES:
            abort(404)
        raw = get_db().execute('SELECT * FROM catalog_items WHERE category=?', (category,)).fetchall()
        owned = owned_names(category)
        image_names = {r['name'] for r in get_db().execute('SELECT name FROM catalog_images WHERE category=?', (category,))}
        items = [dict(r) | {'meta':display_metadata(category, json.loads(r['metadata'])),
                            'owned':r['name'] in owned, 'image':r['name'] in image_names}
                 for r in raw]
        # A filter within one property allows multiple selected values (OR).
        # Selections across different properties are combined (AND).
        facet_fields = ('location', 'rarity', 'size', 'movement', 'type', 'kind', 'sources')
        def facet_values(item, field):
            value = item['meta'].get(field)
            if field == 'sources' and isinstance(value, list):
                return [str(v) for v in value if isinstance(v, str) and v]
            return [value] if isinstance(value,str) and value else []
        facets = {key: sorted({v for item in items for v in facet_values(item,key)}, key=str.casefold)
                  for key in facet_fields}
        selected = {key: request.args.getlist(key)[:30] for key in facets}
        selected = {key: [v for v in dict.fromkeys(values) if v in facets[key]]
                    for key,values in selected.items()}
        q = request.args.get('q', '').strip().casefold()[:100]
        month = request.args.get('month', str(local_today().month) if category in ('fish','bugs','deepsea') else 'all')
        if month not in [str(x) for x in range(1,13)] + ['all']:
            month = 'all'
        status = request.args.get('status', 'all')
        season = request.args.get('season','all')
        order = request.args.get('sort', 'catalog')
        hemi = request.args.get('hemisphere', g.user['hemisphere'])
        if hemi not in ('north','south'):
            hemi = g.user['hemisphere']
        this_moment = datetime.now(ZoneInfo(user_timezone()))
        selected_month = int(month) if month != 'all' else this_moment.month
        # Separate current real-time availability from the selected calendar month.
        for item in items:
            item['badges'] = (calendar_badges(item['meta'], hemi, selected_month)
                if month != 'all' else {'year_round': False, 'new': False, 'leaving': False})
            item['available_now'] = (category in ('fish','bugs','deepsea')
                and currently_catchable(item['meta'], hemi, this_moment))
        def include(item):
            m = item['meta']
            if q:
                terms = [item['name'], str(m.get('artist','')), str(m.get('real_name',''))]
                clean = [value.casefold() for value in terms if value]
                if not any(q in value for value in clean):
                    # Small spelling errors should not hide otherwise matching items.
                    # Require a strong match to an entire title or individual word.
                    words = [word for value in clean for word in value.split()]
                    if len(q) < 4 or not any(SequenceMatcher(None, q, value).ratio() >= .78
                                                    for value in clean + words):
                        return False
            if status == 'missing' and item['owned'] or status == 'owned' and not item['owned']:
                return False
            if any(chosen and not set(facet_values(item, field)).intersection(chosen)
                   for field,chosen in selected.items()):
                return False
            months = m.get('months',{}).get(hemi) if isinstance(m.get('months'),dict) else None
            if month != 'all' and isinstance(months,str) and len(months) == 12:
                n = int(month)-1
                if months[n] != '1': return False
                if season == 'new' and months[(n-1)%12] == '1': return False
                if season == 'leaving' and months[(n+1)%12] == '1': return False
            elif season in ('new','leaving'):
                return False
            return True
        items = [item for item in items if include(item)]
        if order == 'price_high': items.sort(key=lambda i: (-(i['price'] or 0), i['name'].casefold()))
        elif order == 'price_low': items.sort(key=lambda i: ((i['price'] or 0), i['name'].casefold()))
        elif order == 'catalog': items.sort(key=lambda i: (i['position'],i['name'].casefold()))
        else: items.sort(key=lambda i: i['name'].casefold())
        total_filtered = len(items)
        try: page = max(1,int(request.args.get('page','1')))
        except ValueError: page = 1
        # DIY has hundreds of recipes. Keep filters server-side and show the
        # first 60, with progressive 'Show more' and optional 'Show all'.
        # This avoids page hopping and still works when JavaScript is disabled.
        page_params = request.args.to_dict(flat=False)
        for key in ('page', 'limit', 'view'):
            page_params.pop(key, None)
        page = last_page = 1
        prev_page_url = next_page_url = None
        diy_more_url = diy_all_url = None
        if category == 'diy':
            try:
                limit = int(request.args.get('limit', '60'))
            except (ValueError, OverflowError):
                limit = 60
            limit = max(60, min(limit, 2000))
            if request.args.get('view') == 'all':
                limit = total_filtered
            items = items[:limit]
            if len(items) < total_filtered:
                diy_more_url = url_for('collection', category=category,
                                       limit=min(limit + 60, total_filtered), **page_params)
                diy_all_url = url_for('collection', category=category, view='all', **page_params)
        diy_displayed = len(items) if category == 'diy' else None
        missing_params = dict(page_params)
        missing_params['status'] = ['all' if status == 'missing' else 'missing']
        show_missing_url = url_for('collection', category=category, **missing_params)
        selected_month_name = calendar.month_name[selected_month]
        seasonal_items, year_round_items, paintings, sculptures = [], [], [], []
        if month != 'all' and category in ('fish', 'bugs', 'deepsea'):
            for item in items:
                (year_round_items if item['badges']['year_round'] else seasonal_items).append(item)
        if category == 'art':
            for item in items:
                (sculptures if item['meta'].get('kind') == 'Sculpture' else paintings).append(item)
        return render_template('collection.html', category=category, items=items,
            current_page_url=request.full_path.rstrip('?'),
            current_collection_url=url_for('collection', category=category),
            available_now_items=seasonal_items, always_available_items=year_round_items,
            paintings=paintings, sculptures=sculptures, current_month_name=selected_month_name,
            facets=facets, q=request.args.get('q','')[:100], month=month, status=status,
            selected=selected, season=season, sort=order, page=page,
            prev_page_url=prev_page_url, next_page_url=next_page_url,
            last_page=last_page, total_filtered=total_filtered,
            diy_displayed=diy_displayed, diy_more_url=diy_more_url, diy_all_url=diy_all_url,
            overall_total=len(raw), total_owned=len(owned & {r['name'] for r in raw}),
            months=MONTHS, hemisphere=hemi, show_missing_url=show_missing_url)

    @app.post('/collection/set')
    @requires_login
    def set_collection():
        category = request.form.get('category','')
        name = request.form.get('name','')
        have = request.form.get('have') == '1'
        if category not in CATEGORIES or not name or len(name) > 200:
            abort(400)
        if not get_db().execute('SELECT 1 FROM catalog_items WHERE category=? AND name=?',(category,name)).fetchone():
            abort(404)
        with get_db():
            if have:
                get_db().execute('INSERT OR IGNORE INTO owned_items(user_id,category,name) VALUES(?,?,?)',
                                 (g.user['id'],category,name))
            else:
                get_db().execute('DELETE FROM owned_items WHERE user_id=? AND category=? AND name=?',
                                 (g.user['id'],category,name))
        if request.headers.get('Accept') == 'application/json':
            return jsonify(ok=True, owned=have)
        return redirect(url_for('collection', category=category))

    @app.get('/checklist')
    @requires_login
    def checklist():
        day = task_day()
        rows = get_db().execute('SELECT task_key FROM daily_tasks WHERE user_id=? AND day=?',
                                (g.user['id'],day)).fetchall()
        done = {r['task_key'] for r in rows}
        tree = get_db().execute('''SELECT day, outcome FROM money_tree_journal
                  WHERE user_id=? ORDER BY day DESC LIMIT 35''',(g.user['id'],)).fetchall()
        try: week = min(520, max(0, int(request.args.get('week', '0'))))
        except ValueError: week = 0
        end_date = local_today() - timedelta(days=week * 7)
        timeline_start = (end_date - timedelta(days=6)).isoformat()
        timeline_end = end_date.isoformat()
        timeline_rows = get_db().execute('''SELECT day, outcome FROM money_tree_journal WHERE user_id=? AND day BETWEEN ? AND ?''',
                                         (g.user['id'], timeline_start, timeline_end)).fetchall()
        entries = {r['day']: r['outcome'] for r in timeline_rows}
        timeline = []
        for count in range(6, -1, -1):
            date = end_date - timedelta(days=count)
            key = date.isoformat()
            timeline.append({'day': key, 'label': date.strftime('%a, %b ') + str(date.day),
                             'outcome': entries.get(key, 'unknown')})
        # Timeline is a record of observations, not a fertile-day prediction.
        return render_template('checklist.html', today=local_today().isoformat(),
                               reset_mode=g.user['daily_reset_mode'], user_timezone=user_timezone(),
                               tasks=TASKS, done=done,
                               journal=tree, timeline=timeline, week=week)

    @app.post('/checklist/task')
    @requires_login
    def task_update():
        key = request.form.get('task','')
        if key not in TASK_KEYS:
            abort(400)
        day = task_day()
        with get_db():
            if request.form.get('done') == '1':
                get_db().execute('INSERT OR IGNORE INTO daily_tasks VALUES(?,?,?)',(g.user['id'],day,key))
            else:
                get_db().execute('DELETE FROM daily_tasks WHERE user_id=? AND day=? AND task_key=?',
                                 (g.user['id'],day,key))
        if request.headers.get('Accept') == 'application/json':
            return jsonify(ok=True)
        return redirect(url_for('checklist'))

    @app.post('/checklist/reset')
    @requires_login
    def checklist_reset():
        with get_db():
            get_db().execute('DELETE FROM daily_tasks WHERE user_id=? AND day=?',
                             (g.user['id'],task_day()))
        flash('Checklist cleared.' if g.user['daily_reset_mode'] == 'manual'
              else 'Today’s checklist has been reset.', 'success')
        return redirect(url_for('checklist'))

    @app.post('/checklist/tree')
    @requires_login
    def tree_update():
        day = request.form.get('day',str(local_today()))
        outcome = request.form.get('outcome','')
        try:
            day_date = datetime.strptime(day,'%Y-%m-%d').date()
            if abs((day_date-local_today()).days)>3660:
                raise ValueError('Too far from current date')
        except ValueError: abort(400)
        if outcome not in ('fertile','normal','unknown','remove'):
            abort(400)
        with get_db():
            if outcome == 'remove':
                get_db().execute('DELETE FROM money_tree_journal WHERE user_id=? AND day=?',
                                 (g.user['id'],day))
            else:
                get_db().execute('''INSERT INTO money_tree_journal VALUES (?,?,?)
                      ON CONFLICT(user_id,day) DO UPDATE SET outcome=excluded.outcome''',
                      (g.user['id'],day,outcome))
        try: week = min(520, max(0, int(request.form.get('week', '0'))))
        except ValueError: week = 0
        return redirect(url_for('checklist', week=week) if week else url_for('checklist'))

    @app.post('/checklist/tree-extend')
    @requires_login
    def tree_extend():
        """Add the next observation date or remove the latest logged date."""
        action = request.form.get('action')
        if action not in ('add', 'remove'):
            abort(400)
        row = get_db().execute('SELECT MAX(day) AS last_day FROM money_tree_journal WHERE user_id=?',
                               (g.user['id'],)).fetchone()
        last = row['last_day'] if row else None
        if action == 'add':
            day = (datetime.strptime(last, '%Y-%m-%d').date() + timedelta(days=1)
                   if last else local_today())
            if abs((day - local_today()).days) > 3660:
                flash('The observation date is outside the supported range.', 'error')
            else:
                with get_db():
                    get_db().execute('INSERT OR IGNORE INTO money_tree_journal(user_id,day,outcome) VALUES(?,?,?)',
                                     (g.user['id'], day.isoformat(), 'unknown'))
                flash(f'Added observation day {day.isoformat()}.', 'success')
        elif last:
            with get_db():
                get_db().execute('DELETE FROM money_tree_journal WHERE user_id=? AND day=?',
                                 (g.user['id'], last))
            flash(f'Removed latest observation day {last}.', 'success')
        else:
            flash('No observation days to remove.', 'error')
        return redirect(url_for('checklist'))

    @app.get('/events')
    @requires_login
    def events():
        return render_template('events.html')

    @app.get('/events/nature')
    @requires_login
    def nature():
        return render_template('notice.html',title='Nature Day',message='This event was a placeholder in your backup; additional tracking is not yet implemented.')

    @app.get('/events/bunny')
    @requires_login
    def bunny():
        rows = get_db().execute('SELECT * FROM catalog_items WHERE category=?',('diy',)).fetchall()
        image_names = {r['name'] for r in get_db().execute('SELECT name FROM catalog_images WHERE category=?',('diy',))}
        owned = owned_names('diy')
        items = []
        materials = {}
        for r in rows:
            m = json.loads(r['metadata'])
            sources = m.get('sources') or []
            mats = m.get('materials') or []
            if (any('bunny day' in str(s).lower() or 'zipper' in str(s).lower() for s in sources)
                    or any(str(entry.get('name','')).lower().endswith(' egg') for entry in mats if isinstance(entry,dict))):
                items.append(dict(r) | {'meta':display_metadata('diy',m),
                                        'owned':r['name'] in owned, 'image':r['name'] in image_names})
                for entry in mats:
                    if not isinstance(entry,dict): continue
                    name, qty = str(entry.get('name','')), entry.get('quantity',0)
                    if name and isinstance(qty,(int,float)):
                        materials[name] = materials.get(name,0) + qty
        return render_template('bunny.html', items=items, materials=sorted(materials.items()),
                               owned=owned, hemisphere=g.user['hemisphere'])

    @app.route('/settings',methods=['GET','POST'])
    @requires_login
    def settings():
        if request.method == 'POST':
            hemi = request.form.get('hemisphere')
            theme = request.form.get('theme')
            chosen_timezone = request.form.get('timezone', '').strip()
            reset_mode = request.form.get('daily_reset_mode', 'automatic')
            if hemi not in ('north','south') or theme not in ('light','dark') or reset_mode not in ('automatic','manual'):
                abort(400)
            if chosen_timezone and not valid_timezone(chosen_timezone):
                flash('Invalid time zone. Use an IANA name such as America/New_York or Europe/London.', 'error')
                return redirect(url_for('settings'))
            old_mode = g.user['daily_reset_mode']
            current_day = task_day()
            # Preserve today's checked tasks across a mode switch; historical
            # date-keyed rows are never deleted by this preference change.
            if reset_mode == 'manual':
                new_day = 'manual'
            else:
                new_day = local_date(chosen_timezone or app.config['TIMEZONE']).isoformat()
            with get_db():
                if new_day != current_day and reset_mode != old_mode:
                    get_db().execute('''INSERT OR IGNORE INTO daily_tasks(user_id,day,task_key)
                        SELECT user_id,?,task_key FROM daily_tasks WHERE user_id=? AND day=?''',
                        (new_day, g.user['id'], current_day))
                get_db().execute('''UPDATE users
                      SET hemisphere=?, theme=?, timezone=?, daily_reset_mode=? WHERE id=?''',
                      (hemi, theme, chosen_timezone or None, reset_mode, g.user['id']))
            flash('Preferences saved.', 'success')
            return redirect(url_for('settings'))
        return render_template('settings.html', effective_timezone=user_timezone(),
                               server_timezone=app.config['TIMEZONE'])

    @app.get('/data')
    @requires_login
    def data_page():
        return render_template('data.html')

    @app.get('/data/export')
    @requires_login
    def export_progress():
        rows = get_db().execute('SELECT category, name FROM owned_items WHERE user_id=? ORDER BY category,name',
                                (g.user['id'],)).fetchall()
        collections = {category:[] for category in CATEGORIES}
        for row in rows:
            if row['category'] in collections:
                collections[row['category']].append(row['name'])
        generated_at = datetime.now(ZoneInfo(user_timezone()))
        filename = timestamped_filename('acnh-progress', 'json', generated_at)
        payload = {'version':'2.0','exportDate':generated_at.isoformat(), 'collections':collections}
        return app.response_class(json.dumps(payload,indent=2,ensure_ascii=False),mimetype='application/json',
            headers={'Content-Disposition':f'attachment; filename="{filename}"'})

    @app.post('/data/import-progress')
    @requires_login
    def import_progress():
        file = request.files.get('file')
        mode = request.form.get('mode')
        if not file or mode not in ('merge','replace'):
            abort(400)
        raw = file.read(1_000_001)
        if len(raw) > 1_000_000:
            flash('Progress file must be under 1 MB.', 'error')
            return redirect(url_for('data_page'))
        try:
            doc = json.loads(raw)
            collections = doc.get('collections')
            if not isinstance(collections,dict): raise ValueError('Expected collections object')
            valid = {}
            for category in CATEGORIES:
                values = collections.get(category,[])
                if not isinstance(values,list): raise ValueError(f'Invalid {category} collection')
                if len(values) > 10000 or not all(isinstance(x,str) and 0<len(x)<=200 for x in values):
                    raise ValueError(f'Invalid items in {category}')
                valid[category] = list(dict.fromkeys(values))
            with get_db():
                if mode == 'replace':
                    get_db().execute('DELETE FROM owned_items WHERE user_id=?', (g.user['id'],))
                get_db().executemany('INSERT OR IGNORE INTO owned_items VALUES(?,?,?)',
                    ((g.user['id'],cat,name) for cat, names in valid.items() for name in names))
            count = sum(map(len,valid.values()))
            flash(f'{count} collected-item names imported ({mode}).', 'success')
        except (ValueError,TypeError,UnicodeDecodeError) as ex:
            flash(f'Could not import progress: {ex}', 'error')
        return redirect(url_for('data_page'))

    @app.post('/data/restore-complete-backup')
    @requires_login
    def restore_full_backup():
        # A complete restore replaces accounts and all data. Require administrator
        # access and current-password reauthentication, even for a preview.
        if not g.user['is_admin']:
            abort(403)
        if not check_password_hash(g.user['password_hash'], request.form.get('password', '')):
            flash('Current account password is required to review or restore a full backup.', 'error')
            return redirect(url_for('data_page'))
        file = request.files.get('file')
        if not file or not file.filename or not file.filename.lower().endswith('.zip'):
            flash('Select an ACNH Tracker complete-backup ZIP.', 'error')
            return redirect(url_for('data_page'))
        payload = file.read(80_000_001)
        mode = request.form.get('mode', 'preview')
        if mode not in ('preview', 'restore'):
            abort(400)
        try:
            plan = inspect_complete_backup(payload)
            c = plan['counts']
            note = (f"{c['users']} user(s), {c['catalog_items']} catalog items, "
                    f"{c['owned_items']} collected checkmarks, {c['daily_tasks']} daily tasks, "
                    f"{c['money_tree_journal']} Money Tree observations and "
                    f"{len(plan['artwork'])} images")
            if mode == 'preview':
                flash('Backup validation passed; no data changed. Contains: ' + note, 'success')
                return redirect(url_for('data_page'))
            if request.form.get('confirm') != 'RESTORE':
                flash('To replace the installation, enter RESTORE exactly.', 'error')
                return redirect(url_for('data_page'))
            if not _restore_lock.acquire(blocking=False):
                abort(409, 'A restoration is already running')
            try:
                # Close this request's connection before SQLite's online backup
                # replaces the live DB. Other requests get a temporary 503.
                close_db()
                # Prepare a new key before touching the DB. Restoring accounts
                # without invalidating the old cookies could grant the wrong user
                # access if user ids changed between installations.
                new_secret = secrets.token_hex(48)
                secret_file = Path(app.config['DATA_DIR']) / '.flask-secret'
                pending = secret_file.with_name('.flask-secret.new')
                pending.write_text(new_secret)
                pending.chmod(0o600)
                try:
                    rollback = apply_complete_backup(plan, app.config['DATABASE'], app.config['DATA_DIR'])
                    # A v0.1.14 backup has no per-user timezone/reset columns.
                    # Upgrade it before allowing any restored account to log in.
                    init_db(app.config['DATABASE'])
                    os.replace(pending, secret_file)
                finally:
                    pending.unlink(missing_ok=True)
                app.config['SECRET_KEY'] = new_secret
                app.config['OFFLINE_NAMESPACE'] = hashlib.sha256(new_secret.encode()).hexdigest()[:18]
                session.clear()
                flash('Full backup restored: ' + note + '. Sign in with an account from the backup. '
                      'A safety copy of the previous database is in backups/pre-restore.sqlite3.', 'success')
                app.logger.warning('Full backup restoration completed; previous database retained at %s', rollback)
                return redirect(url_for('login'))
            finally:
                _restore_lock.release()
        except (ValueError, OSError, sqlite3.DatabaseError) as exc:
            app.logger.warning('Backup restoration was rejected or failed: %s', exc)
            flash('Backup not restored: ' + str(exc), 'error')
            return redirect(url_for('data_page'))

    @app.get('/data/complete-backup')
    @requires_login
    def complete_backup():
        if not g.user['is_admin']:
            abort(403)
        database = Path(app.config['DATA_DIR']) / 'backups' / 'tracker.sqlite3'
        make_backup(app.config['DATABASE'], str(database))
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, 'w', zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
            archive.write(database, 'tracker.sqlite3')
            root = Path(app.config['DATA_DIR'])/'artwork'
            if root.exists():
                for file in root.rglob('*'):
                    if (file.is_file() and file.suffix in ('.png','.webp')
                            and file.resolve().is_relative_to(root.resolve())):
                        archive.write(file, 'artwork/'+file.relative_to(root).as_posix())
            archive.writestr('RESTORE.txt',
                'Private backup: contains user account hashes, collection progress, and artwork.\n'
                'To restore, stop the container, restore tracker.sqlite3 under /data and\n'
                'artwork/ under /data/artwork, then restart the app. Preserve passwords.\n')
        memory.seek(0)
        return send_file(memory,as_attachment=True,download_name=download_name('acnh-tracker-complete-backup', 'zip'),
                         mimetype='application/zip')

    @app.get('/artwork/<category>/<path:name>')
    @requires_login
    def item_artwork(category, name):
        if category not in CATEGORIES:
            abort(404)
        row = get_db().execute('SELECT path FROM catalog_images WHERE category=? AND name=?',
                               (category, name)).fetchone()
        if row is None:
            abort(404)
        root = Path(app.config['DATA_DIR']) / 'artwork'
        path = root / row['path']
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            abort(404)
        return send_file(path, mimetype='image/webp' if path.suffix == '.webp' else 'image/png',
                         conditional=True, max_age=86400)

    @app.get('/data/database-backup')
    @requires_login
    def manual_backup():
        if not g.user['is_admin']:
            abort(403)
        path = str(Path(app.config['DATA_DIR'])/'backups'/'tracker.sqlite3')
        make_backup(app.config['DATABASE'],path)
        return send_file(path,as_attachment=True,download_name=download_name('acnh-tracker-backup', 'sqlite3'),mimetype='application/octet-stream')

    return app
