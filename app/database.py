"""Minimal SQLite persistence shared by all collection categories."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from .sqlite_backup import make_backup
from flask import g, current_app

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  hemisphere TEXT NOT NULL DEFAULT 'north',
  theme TEXT NOT NULL DEFAULT 'light',
  timezone TEXT,
  daily_reset_mode TEXT NOT NULL DEFAULT 'automatic',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS catalog_items (
  category TEXT NOT NULL,
  name TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  price INTEGER,
  metadata TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(category, name)
);
CREATE TABLE IF NOT EXISTS catalog_images (
  category TEXT NOT NULL,
  name TEXT NOT NULL,
  path TEXT NOT NULL,
  PRIMARY KEY(category, name)
);
CREATE INDEX IF NOT EXISTS catalog_by_category ON catalog_items(category, position);
CREATE TABLE IF NOT EXISTS owned_items (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category TEXT NOT NULL,
  name TEXT NOT NULL,
  PRIMARY KEY(user_id, category, name)
);
CREATE TABLE IF NOT EXISTS daily_tasks (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  task_key TEXT NOT NULL,
  PRIMARY KEY(user_id, day, task_key)
);
CREATE TABLE IF NOT EXISTS money_tree_journal (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN ('fertile','normal','unknown')),
  PRIMARY KEY(user_id, day)
);
"""


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=15000')
    con.execute('PRAGMA foreign_keys=ON')
    return con


def init_db(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript(SCHEMA)
        # Add-only migration for databases created by ACNH Tracker v0.1.14.
        # An unset timezone uses the Docker server default until a user selects one.
        columns = {row['name'] for row in con.execute('PRAGMA table_info(users)')}
        if 'timezone' not in columns:
            con.execute('ALTER TABLE users ADD COLUMN timezone TEXT')
        if 'daily_reset_mode' not in columns:
            con.execute("ALTER TABLE users ADD COLUMN daily_reset_mode TEXT NOT NULL DEFAULT 'automatic'")


def get_db() -> sqlite3.Connection:
    if 'db' not in g:
        g.db = connect(current_app.config['DATABASE'])
    return g.db


def close_db(_error=None):
    con = g.pop('db', None)
    if con is not None:
        con.close()


