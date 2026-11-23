"""Validate and restore ACNH Tracker's own complete-backup ZIPs.

The ZIP is private account data. Never unpack arbitrary paths from uploaded archives.
The database is restored with SQLite's online backup API rather than copying over
an open WAL database file. The previous database is retained as a safety snapshot.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from io import BytesIO
from pathlib import Path
from pathlib import PurePosixPath

from .sqlite_backup import make_backup

MAX_ARCHIVE_BYTES = 80_000_000
MAX_DB_BYTES = 40_000_000
MAX_ARTWORK_BYTES = 55_000_000
MAX_IMAGE_BYTES = 2_000_000
MAX_IMAGES = 2_000
EXPECTED_TABLES = {
    'users': {'id', 'username', 'password_hash', 'display_name', 'is_admin', 'hemisphere', 'theme', 'created_at'},
    'catalog_items': {'category', 'name', 'position', 'price', 'metadata'},
    'catalog_images': {'category', 'name', 'path'},
    'owned_items': {'user_id', 'category', 'name'},
    'daily_tasks': {'user_id', 'day', 'task_key'},
    'money_tree_journal': {'user_id', 'day', 'outcome'},
}
PRIMARY_KEYS = {
    'users': ['id'],
    'catalog_items': ['category', 'name'],
    'catalog_images': ['category', 'name'],
    'owned_items': ['user_id', 'category', 'name'],
    'daily_tasks': ['user_id', 'day', 'task_key'],
    'money_tree_journal': ['user_id', 'day'],
}


def safe_image_path(name: str) -> Path:
    if not isinstance(name, str) or '\\' in name or '\x00' in name:
        raise ValueError('Unsafe artwork path in backup')
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or not path.parts
            or any(part in ('..', '.', '') for part in path.parts)
            or path.suffix.lower() not in ('.png', '.webp')):
        raise ValueError('Unsafe artwork path in backup')
    return Path(*path.parts)


def _check_backup_db(db_bytes: bytes, artwork_names: set[str]) -> dict:
    # SQLite reads an actual temp file. Use immutable read-only mode to avoid
    # executing any write or honoring a WAL from an untrusted uploaded database.
    with tempfile.TemporaryDirectory(prefix='acnh-backup-validate-') as directory:
        db_file = Path(directory) / 'backup.sqlite3'
        db_file.write_bytes(db_bytes)
        try:
            with closing(sqlite3.connect(db_file.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
                db.execute('PRAGMA query_only=ON')
                db.execute('PRAGMA trusted_schema=OFF')
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Backup SQLite integrity check failed')
                if db.execute("SELECT name FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1").fetchone():
                    raise ValueError('Backup contains unsupported database triggers or views')
                for table, required in EXPECTED_TABLES.items():
                    definitions = list(db.execute(f'PRAGMA table_info({table})'))
                    fields = {row[1] for row in definitions}
                    if not required.issubset(fields):
                        raise ValueError(f'Backup is missing expected {table} columns')
                    primary = [row[1] for row in sorted(definitions, key=lambda item: item[5]) if row[5]]
                    if primary != PRIMARY_KEYS[table]:
                        raise ValueError(f'Backup has an incompatible {table} primary key')
                if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                    raise ValueError('Backup has broken database relationships')
                counts = {table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                          for table in EXPECTED_TABLES}
                if counts['users'] < 1 or db.execute('SELECT 1 FROM users WHERE is_admin=1 LIMIT 1').fetchone() is None:
                    raise ValueError('Backup does not contain an administrator account')
                if counts['catalog_items'] > 100_000 or counts['users'] > 10_000:
                    raise ValueError('Backup contains an unreasonable number of records')
                for (path,) in db.execute('SELECT path FROM catalog_images'):
                    safe_image_path(path)
                    if path not in artwork_names:
                        raise ValueError(f'Backup image mapping is missing artwork: {path}')
                return counts
        except sqlite3.DatabaseError as exc:
            raise ValueError('Backup database could not be validated') from exc


def inspect_complete_backup(blob: bytes) -> dict:
    """Validate bytes and all image references before permitting a restore."""
    if not isinstance(blob, bytes) or not 0 < len(blob) <= MAX_ARCHIVE_BYTES:
        raise ValueError('Complete backup ZIP must be under 80 MB')
    try:
        with zipfile.ZipFile(BytesIO(blob)) as archive:
            files = archive.infolist()
            if len(files) > MAX_IMAGES + 2:
                raise ValueError('Too many files in backup')
            if len({entry.filename for entry in files}) != len(files):
                raise ValueError('Duplicate file names in backup')
            image_names = set()
            db_bytes = None
            artwork = {}
            total_image_size = 0
            for entry in files:
                name = entry.filename
                if entry.is_dir() or entry.flag_bits & 1 or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f'Unsupported archive entry: {name}')
                if name == 'tracker.sqlite3':
                    if not 0 < entry.file_size <= MAX_DB_BYTES:
                        raise ValueError('Backup database is too large or empty')
                    db_bytes = archive.read(entry)
                elif name == 'RESTORE.txt':
                    if entry.file_size > 20_000:
                        raise ValueError('Unexpected backup metadata size')
                elif name.startswith('artwork/'):
                    relative = name[len('artwork/'):]
                    safe_image_path(relative)
                    if relative in image_names:
                        raise ValueError('Duplicate artwork filename in backup')
                    if not 0 < entry.file_size <= MAX_IMAGE_BYTES:
                        raise ValueError('Invalid artwork file size in backup')
                    total_image_size += entry.file_size
                    if total_image_size > MAX_ARTWORK_BYTES:
                        raise ValueError('Backup contains too much artwork')
                    content = archive.read(entry)
                    if not (content.startswith(b'\x89PNG\r\n\x1a\n') or
                            (content.startswith(b'RIFF') and content[8:12] == b'WEBP')):
                        raise ValueError(f'Unsupported image content in backup: {relative}')
                    artwork[relative] = content
                    image_names.add(relative)
                else:
                    raise ValueError(f'Unexpected file in backup: {name}')
            if db_bytes is None:
                raise ValueError('Backup is missing tracker.sqlite3')
            counts = _check_backup_db(db_bytes, image_names)
            return {'database': db_bytes, 'artwork': artwork, 'counts': counts}
    except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise ValueError('Uploaded backup is not a valid ZIP file') from exc


def restore_complete_backup(plan: dict, db_path: str, data_dir: str) -> Path:
    """Restore a validated backup, retaining a rollback SQLite snapshot.

    On failure, restore the artwork directory and (if needed) the DB snapshot.
    No backup may put files outside the persistent data directory.
    """
    data_root = Path(data_dir)
    current_db = Path(db_path)
    if not current_db.is_file():
        raise ValueError('Current database is missing')
    old_artwork = data_root / 'artwork'
    rollback_db = data_root / 'backups' / 'pre-restore.sqlite3'
    rollback_images = data_root / 'backups' / 'pre-restore-artwork.zip'
    make_backup(str(current_db), str(rollback_db))
    # A database snapshot alone cannot recover user-uploaded artwork after an
    # accidental full restore. Keep a separate portable safety ZIP as well.
    existing_artwork = data_root / 'artwork'
    if existing_artwork.is_symlink():
        raise ValueError('Artwork directory is not a safe directory')
    if existing_artwork.is_dir():
        temp_archive = rollback_images.with_name('pre-restore-artwork.pending.zip')
        try:
            with zipfile.ZipFile(temp_archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=4) as backup:
                for file in existing_artwork.rglob('*'):
                    if not file.is_file():
                        continue
                    if file.is_symlink() or not file.resolve().is_relative_to(existing_artwork.resolve()):
                        raise ValueError('Cannot back up unsafe artwork symlinks')
                    relative = file.relative_to(existing_artwork).as_posix()
                    safe_image_path(relative)
                    backup.write(file, relative)
            os.replace(temp_archive, rollback_images)
        finally:
            temp_archive.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='.restore-', dir=data_root) as temp:
        temp_root = Path(temp)
        uploaded_db = temp_root / 'uploaded.sqlite3'
        uploaded_db.write_bytes(plan['database'])
        staged_artwork = temp_root / 'new-artwork'
        staged_artwork.mkdir()
        for name, contents in plan['artwork'].items():
            target = staged_artwork / safe_image_path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
        rollback_artwork = temp_root / 'previous-artwork'
        had_artwork = old_artwork.exists()
        if had_artwork:
            if old_artwork.is_symlink() or not old_artwork.is_dir():
                raise ValueError('Artwork directory is not a safe directory')
            os.replace(old_artwork, rollback_artwork)
        try:
            os.replace(staged_artwork, old_artwork)
            try:
                with closing(sqlite3.connect(uploaded_db)) as source, closing(sqlite3.connect(current_db, timeout=30)) as dest:
                    dest.execute('PRAGMA busy_timeout=30000')
                    source.backup(dest, pages=100, sleep=0.1)
            except Exception:
                # SQLite backup is transactional, but explicitly restore the
                # known-good snapshot if the backup API reports a failure.
                with closing(sqlite3.connect(rollback_db)) as source, closing(sqlite3.connect(current_db, timeout=30)) as dest:
                    source.backup(dest)
                raise
        except Exception:
            if old_artwork.is_dir():
                # Move attempted new artwork away; don't delete an unknown dir.
                os.replace(old_artwork, temp_root / 'failed-artwork')
            if had_artwork:
                os.replace(rollback_artwork, old_artwork)
            raise
    return rollback_db
