#!/usr/bin/env bash
# Read-only sanity check for an already-running Raspberry Pi Docker installation.
set -euo pipefail
cd "$(dirname "$0")/.."
echo '== ACNH Tracker container status =='
docker compose ps

echo '== Read-only in-container smoke checks =='
docker compose exec -T tracker python - <<'PY'
import os, platform, sqlite3, urllib.request
from pathlib import Path

for path, expected in [('/health', b'ok'), ('/login', b'ACNH'), ('/static/style.css', b':root')]:
    with urllib.request.urlopen('http://127.0.0.1:8080' + path, timeout=5) as response:
        body=response.read(1_000_000)
        assert response.status == 200, (path, response.status)
        assert expected in body, path
    print(path, 'OK')

assert Path('/data/.flask-secret').is_file(), 'Missing persisted session key'
assert Path('/data/tracker.sqlite3').is_file(), 'Missing persisted SQLite database'
con=sqlite3.connect('file:/data/tracker.sqlite3?mode=ro',uri=True)
tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
assert {'users','catalog_items','catalog_images','owned_items','daily_tasks'} <= tables, tables
print('Database schema OK')
count = con.execute('SELECT count(*) FROM catalog_items').fetchone()[0]
assert count >= 939, f'Expected 939 bundled collectibles, found {count}'
print('Catalog entries:', count)
links = con.execute('SELECT count(*) FROM catalog_images').fetchone()[0]
assert links >= 887, f'Expected at least 887 artwork mappings, found {links}'
print('Artwork mappings:', links)
con.close()
print('Architecture:', platform.machine())
print('All automated smoke checks passed.')
PY
cat <<'MSG'

Still check manually in the browser:
  1. Register/login; open every collection, checklist, events and data page.
  2. Confirm all 939 catalog items and included artwork appear automatically.
  3. Toggle a collectible, apply sidebar filters and export your progress.
  4. Verify per-account timezone, checklist reset, DIY browsing and larger art cards.
  5. Download a complete backup, restart the container, and confirm progress persists.
  6. Test full restore on a separate instance before relying on it.
MSG
