"""The one SQLite database."""

import os
import sqlite3

PATH = os.environ.get("PINBOARD_DB", "/var/lib/pinboard/pinboard.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    body TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""


def connect(path: str = PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn
