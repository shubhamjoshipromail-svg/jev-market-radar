"""Shared SQLite access. Every module uses connect(); schema lives in schema.sql."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from radar.config import DB_PATH

SCHEMA = Path(__file__).with_name("schema.sql")
MIGRATIONS = [
    ("judgments", "same_period", "REAL"),
    ("markets", "outcomes", "TEXT"),
    ("markets", "tags", "TEXT"),
    ("markets", "is_game", "INTEGER NOT NULL DEFAULT 0"),
    ("alerts", "notified", "INTEGER NOT NULL DEFAULT 0"),
    ("markets", "volume_24h", "REAL"),
    ("headlines", "dup_of", "INTEGER"),
    ("headlines", "search_terms", "TEXT"),
    ("alerts", "why", "TEXT"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA.read_text())
    for table, col, typ in MIGRATIONS:  # columns added after a DB was first created
        if col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    return conn


if __name__ == "__main__":
    c = connect()
    print("ok", DB_PATH, [r[0] for r in c.execute("select name from sqlite_master where type='table'")])
