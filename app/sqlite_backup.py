"""SQLite online snapshot utility, independent from Flask and its request context."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def make_backup(db_path: str, backup_path: str) -> None:
    """Consistent SQLite online snapshot, atomically replacing one backup file."""
    target = Path(backup_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='backup-', suffix='.sqlite', dir=target.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(db_path, timeout=30)) as source, closing(sqlite3.connect(temp)) as dest:
            source.backup(dest)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
