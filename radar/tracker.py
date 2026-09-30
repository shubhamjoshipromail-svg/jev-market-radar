"""Record live market prices 1, 5, 10, and 60 minutes after each alert."""
from __future__ import annotations

import argparse
import time

from radar.db import connect, now
from radar.markets import get_price

OFFSETS_MIN = (1, 5, 10, 60)


def check_due(conn) -> int:
    """Record each due, previously unchecked alert/offset pair once."""
    recorded = 0
    for offset in OFFSETS_MIN:
        rows = conn.execute(
            "SELECT a.id, a.market_id FROM alerts a"
            " LEFT JOIN price_checks p ON p.alert_id=a.id AND p.offset_min=?"
            " WHERE p.alert_id IS NULL"
            " AND datetime(a.created_at) <= datetime('now', ?)",
            (offset, f"-{offset} minutes"),
        ).fetchall()
        for row in rows:
            try:
                price = get_price(row["market_id"])
            except Exception as exc:
                price = None
                print(f"[tracker] alert {row['id']} +{offset}m: {exc}", flush=True)
            if price is None:
                # Leave it due so a transient venue/API failure is retried next poll.
                continue
            conn.execute(
                "INSERT OR IGNORE INTO price_checks (alert_id, offset_min, price, checked_at) VALUES (?,?,?,?)",
                (row["id"], offset, price, now()),
            )
            recorded += 1
        conn.commit()
    return recorded


def tracker_loop(poll_seconds: float = 5.0) -> None:
    conn = connect()
    try:
        while True:
            count = check_due(conn)
            if count:
                print(f"[tracker] recorded {count} price check(s)", flush=True)
            time.sleep(poll_seconds)
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll", type=float, default=5.0)
    args = parser.parse_args()
    if args.once:
        connection = connect()
        try:
            print(f"[tracker] recorded {check_due(connection)} price check(s)")
        finally:
            connection.close()
    else:
        tracker_loop(args.poll)
