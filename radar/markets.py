"""Polymarket indexer: upserts every active market (question, rules, end date, YES price).

python -m radar.markets --once      # one full sync
python -m radar.markets --loop 180  # resync every 180 s
"""
import argparse
import json
import os
import re
import time

import httpx

from radar.db import connect, now

GAMMA = "https://gamma-api.polymarket.com/markets"
EVENTS = "https://gamma-api.polymarket.com/events/keyset"
PAGE = 100  # server max
MIN_LIQUIDITY = int(os.getenv("RADAR_MIN_LIQUIDITY", "1000"))


def _yes_price(m: dict):
    try:
        return float(json.loads(m.get("outcomePrices") or "[]")[0])
    except (ValueError, IndexError, TypeError):
        return None


def fetch_all(http: httpx.Client) -> list[dict]:
    """Walk /events/keyset (fast: ~100 events x ~12 markets per page) and keep live, liquid markets.
    /markets is much slower: ~231k "open" rows, offset pagination capped near 2k."""
    out, cursor, cutoff = [], None, now()
    while True:
        params = {"closed": "false", "limit": PAGE}
        if cursor:
            params["after_cursor"] = cursor
        r = http.get(EVENTS, params=params)
        r.raise_for_status()
        data = r.json()
        for ev in data.get("events", []):
            for m in ev.get("markets") or []:
                if (m.get("active") and not m.get("closed")
                        and float(m.get("liquidity") or 0) >= MIN_LIQUIDITY
                        and (m.get("endDate") or "9") > cutoff):
                    m["_url"] = f"https://polymarket.com/event/{ev.get('slug')}"
                    m["_tags"] = [t.get("slug") for t in ev.get("tags") or []]
                    out.append(m)
        cursor = data.get("next_cursor")
        if not cursor or not data.get("events"):
            return out


GAME_Q = re.compile(r"\bvs\.?\s|\bwin on \d{4}-|leading at halftime|^exact score", re.I)
BET_Q = re.compile(r"^spread:|\bo/u \d|team total|\bmoneyline\b", re.I)


def _is_game(m: dict) -> int:
    """Single-game sports markets get priced live by the crowd, so news alerts on them rarely add anything."""
    sporty = "sports" in (m.get("_tags") or []) or m.get("sportsMarketType")
    q = m.get("question") or ""
    return int(bool(BET_Q.search(q) or (sporty and (GAME_Q.search(q) or m.get("gameStartTime")))))


def sync(conn) -> int:
    with httpx.Client(timeout=60) as http:
        rows = fetch_all(http)
    ts = now()
    for m in rows:
        conn.execute(
            "INSERT INTO markets (id,venue,slug,url,question,rules,end_date,yes_price,volume,liquidity,outcomes,tags,is_game,"
            "active,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET question=excluded.question,"
            " outcomes=excluded.outcomes, tags=excluded.tags, is_game=excluded.is_game,"
            " rules=excluded.rules, end_date=excluded.end_date, yes_price=excluded.yes_price, volume=excluded.volume,"
            " liquidity=excluded.liquidity, url=excluded.url, active=1, updated_at=excluded.updated_at",
            (f"polymarket:{m['id']}", "polymarket", m.get("slug"), m["_url"], m["question"], m.get("description"),
             m.get("endDate"), _yes_price(m), float(m.get("volume") or 0), float(m.get("liquidity") or 0),
             m.get("outcomes"), json.dumps(m.get("_tags") or []), _is_game(m), ts),
        )
    conn.execute("UPDATE markets SET active=0 WHERE venue='polymarket' AND updated_at<?", (ts,))
    conn.commit()
    return len(rows)


def get_price(market_id: str) -> float | None:
    """Live YES price for one market (used by the tracker)."""
    native = market_id.split(":", 1)[1]
    r = httpx.get(f"{GAMMA}/{native}", timeout=15)
    return _yes_price(r.json()) if r.status_code == 200 else None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=int)
    a = ap.parse_args()
    conn = connect()
    while True:
        t0 = time.time()
        print(f"[markets] synced {sync(conn)} in {time.time()-t0:.1f}s")
        if not a.loop:
            break
        time.sleep(a.loop)
