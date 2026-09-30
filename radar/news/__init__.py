"""News ingest. Every source calls store() so dedupe is identical everywhere."""
import hashlib
import re

from radar.db import now


def dedupe_key(title: str) -> str:
    return hashlib.sha1(re.sub(r"[^a-z0-9]+", "", title.lower()).encode()).hexdigest()


def store(conn, source: str, title: str, url: str | None = None, body: str = "", published_at: str | None = None) -> int | None:
    """Insert a headline; returns its id, or None if we've already seen it."""
    title = re.sub(r"\s+", " ", title or "").strip()
    if len(title) < 15:
        return None
    cur = conn.execute(
        "INSERT OR IGNORE INTO headlines (source, url, title, body, published_at, fetched_at, dedupe_key)"
        " VALUES (?,?,?,?,?,?,?)",
        (source, url, title, body, published_at or now(), now(), dedupe_key(title)),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None
