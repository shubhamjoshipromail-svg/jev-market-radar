"""FastAPI surface and headline worker for Jev Market Radar.

Run the API with ``uvicorn api.server:app`` and the worker with
``python -m api.server worker``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from radar.db import connect, now
from radar.match import add_paste, process_headline

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

app = FastAPI(title="Jev Market Radar", version="0.1.0")

# Public deploys: anyone can hit POST /api/headline, and every call spends Jev credit. Cap it per IP and per day.
import os
from collections import defaultdict, deque

PUBLIC_MODE = os.getenv("PUBLIC_MODE") == "1"
PASTE_PER_IP_10MIN = int(os.getenv("PASTE_PER_IP_10MIN", "6"))
PASTE_PER_DAY = int(os.getenv("PASTE_PER_DAY", "300"))
_hits: dict[str, deque] = defaultdict(deque)
_day: deque = deque()


def _rate_limit(request: Request) -> None:
    if not PUBLIC_MODE:
        return
    t = time.time()
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    q = _hits[ip]
    while q and q[0] < t - 600:
        q.popleft()
    while _day and _day[0] < t - 86400:
        _day.popleft()
    if len(q) >= PASTE_PER_IP_10MIN or len(_day) >= PASTE_PER_DAY:
        raise HTTPException(status_code=429, detail="Paste limit reached on this public demo. Try again in a few minutes.")
    q.append(t)
    _day.append(t)


@app.on_event("startup")
def _warm_index() -> None:
    # build the market search index off the request path so the first pasted headline isn't slow
    import threading

    from radar.match import _load_index

    threading.Thread(target=lambda: _load_index(connect()), daemon=True).start()


class HeadlineIn(BaseModel):
    title: str = Field(min_length=1, max_length=2_000)
    body: str = Field(default="", max_length=50_000)
    url: str | None = Field(default=None, max_length=8_000)


class FeedbackIn(BaseModel):
    alert_id: int = Field(gt=0)
    vote: Literal[-1, 1]


def _feed_item(conn: sqlite3.Connection, headline_id: int) -> dict | None:
    headline = conn.execute("SELECT * FROM headlines WHERE id=?", (headline_id,)).fetchone()
    if not headline:
        return None
    judgments = []
    rows = conn.execute(
        "SELECT j.*, m.question AS market_question, m.url AS market_url, m.rules AS market_rules,"
        " m.end_date AS market_end_date, m.outcomes AS market_outcomes, m.is_game AS market_is_game,"
        " m.yes_price AS market_yes_price, a.id AS alert_id, a.kind AS alert_kind,"
        " a.direction AS alert_direction, a.price_at_alert AS alert_price, a.created_at AS alert_at, a.why AS alert_why FROM judgments j"
        " JOIN markets m ON m.id=j.market_id"
        " LEFT JOIN alerts a ON a.judgment_id=j.id"
        " WHERE j.headline_id=? ORDER BY j.relevant DESC, j.id",
        (headline_id,),
    )
    for row in rows:
        row = dict(row)
        judgments.append(
            {
                "id": row["id"],
                "market": {
                    "id": row["market_id"],
                    "question": row["market_question"],
                    "url": row["market_url"],
                    "yes_price": row["market_yes_price"],
                    "rules": (row["market_rules"] or "")[:700],
                    "end_date": row["market_end_date"],
                    "outcomes": json.loads(row["market_outcomes"] or "null"),
                    "is_game": bool(row["market_is_game"]),
                },
                "relevant": row["relevant"],
                "same_period": row["same_period"],
                "strength_conf": row["strength_conf"],
                "yes_price_at": row["yes_price_at"],
                "effect_probs": json.loads(row["effect_probs"] or "{}"),
                "effect": row["effect"],
                "effect_conf": row["effect_conf"],
                "strength": row["strength"],
                "latency_ms": row["latency_ms"],
                "alert": (
                    {
                        "id": row["alert_id"],
                        "kind": row["alert_kind"],
                        "direction": row["alert_direction"],
                        "price_at_alert": row["alert_price"],
                        "created_at": row["alert_at"],
                        "why": row["alert_why"],
                        "checks": [dict(c) for c in conn.execute(
                            "SELECT offset_min, price, checked_at FROM price_checks WHERE alert_id=? ORDER BY offset_min",
                            (row["alert_id"],))],
                    }
                    if row["alert_id"] is not None
                    else None
                ),
            }
        )
    also = [r["source"] for r in conn.execute("SELECT source FROM headlines WHERE dup_of=?", (headline_id,))]
    return {"headline": {**dict(headline), "also_reported_by": also}, "judgments": judgments}


@app.get("/api/feed")
def feed(limit: int = Query(default=50, ge=1, le=300), judged: bool = False) -> list[dict]:
    with closing(connect()) as conn:
        where = "WHERE EXISTS (SELECT 1 FROM judgments j WHERE j.headline_id=headlines.id)" if judged else ""
        ids = conn.execute(
            f"SELECT id FROM headlines {where} ORDER BY fetched_at DESC, id DESC LIMIT ?",
            (limit,),
        )
        return [_feed_item(conn, row["id"]) for row in ids]


def _table_max(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COALESCE(max(id), 0) FROM {table}").fetchone()[0])


@app.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    async def events():
        with closing(connect()) as conn:
            cursors = {name: _table_max(conn, name) for name in ("headlines", "judgments", "alerts")}
            yield ": connected\n\n"
            while not await request.is_disconnected():
                emitted = False
                for table, event in (("headlines", "headline"), ("judgments", "judgment"), ("alerts", "alert")):
                    rows = conn.execute(
                        f"SELECT * FROM {table} WHERE id>? ORDER BY id", (cursors[table],)
                    ).fetchall()
                    for row in rows:
                        payload = dict(row)
                        cursors[table] = payload["id"]
                        emitted = True
                        yield f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
                if not emitted:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _hit(row: sqlite3.Row) -> bool | None:
    if row["price"] is None or row["price_at_alert"] is None:
        return None
    return (row["price"] - row["price_at_alert"]) * row["direction"] > 0


@app.get("/api/stats")
def stats() -> dict:
    with closing(connect()) as conn:
        headlines = conn.execute("SELECT count(*) FROM headlines").fetchone()[0]
        judgments = conn.execute("SELECT count(*) FROM judgments").fetchone()[0]
        alerts = conn.execute("SELECT count(*) FROM alerts").fetchone()[0]
        cost_usd = conn.execute("SELECT COALESCE(sum(cost_usd), 0) FROM judgments").fetchone()[0]
        latencies = [r[0] for r in conn.execute(
            "SELECT latency_ms FROM judgments WHERE latency_ms IS NOT NULL ORDER BY latency_ms"
        )]
        p50 = None
        if latencies:
            middle = len(latencies) // 2
            p50 = latencies[middle] if len(latencies) % 2 else (latencies[middle - 1] + latencies[middle]) / 2
        checks = conn.execute(
            "SELECT a.kind, a.direction, a.price_at_alert, p.price"
            " FROM alerts a JOIN price_checks p ON p.alert_id=a.id WHERE p.offset_min=10"
        ).fetchall()
        known_hits = [_hit(row) for row in checks]
        known_hits = [hit for hit in known_hits if hit is not None]
        # a flat price is "no reaction", not a wrong call; report direction accuracy among markets that moved
        moved = [(r["price"] - r["price_at_alert"]) * r["direction"] for r in checks
                 if r["price"] is not None and r["price_at_alert"] is not None]
        moved_nz = [d for d in moved if abs(d) > 1e-9]
        by_kind = {
            row["kind"]: row["count"]
            for row in conn.execute("SELECT kind, count(*) AS count FROM alerts GROUP BY kind")
        }
        return {
            "headlines": headlines,
            "judgments": judgments,
            "alerts": alerts,
            "cost_usd": cost_usd,
            "p50_latency_ms": p50,
            "hit_rate_10m": sum(known_hits) / len(known_hits) if known_hits else None,
            "direction_right_10m": sum(d > 0 for d in moved_nz) / len(moved_nz) if moved_nz else None,
            "moved_share_10m": len(moved_nz) / len(moved) if moved else None,
            "checked_10m": len(moved),
            "by_kind": by_kind,
        }


CATEGORIES = ["sports", "crypto", "politics", "elections", "economy", "finance", "business", "tech", "ai",
              "geopolitics", "world", "culture", "science", "weather"]


def _category(tags_json: str | None) -> str:
    tags = set(json.loads(tags_json or "[]"))
    return next((c for c in CATEGORIES if c in tags), "other")


@app.get("/api/scorecard")
def scorecard() -> dict:
    """Which sources, alert kinds, categories, volume buckets and prompt versions predict real price moves (10 min)."""
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT h.source, a.kind, a.direction, a.price_at_alert, p.price, m.tags, m.volume_24h, j.model,"
            " (SELECT vote FROM feedback f WHERE f.alert_id=a.id ORDER BY f.id DESC LIMIT 1) AS vote"
            " FROM alerts a JOIN headlines h ON h.id=a.headline_id JOIN markets m ON m.id=a.market_id"
            " JOIN judgments j ON j.id=a.judgment_id"
            " LEFT JOIN price_checks p ON p.alert_id=a.id AND p.offset_min=10")]
        heads = {r["source"]: r["n"] for r in conn.execute(
            "SELECT source, count(*) n FROM headlines WHERE status IN ('done','duplicate') GROUP BY source")}
        dups = conn.execute("SELECT count(*) FROM headlines WHERE status='duplicate'").fetchone()[0]

    def vol_bucket(v):
        return "unknown" if v is None else "<$1k" if v < 1e3 else "$1k–10k" if v < 1e4 else "$10k–100k" if v < 1e5 else "$100k+"

    def group(key):
        out = {}
        for r in rows:
            g = out.setdefault(key(r), {"alerts": 0, "checked": 0, "moved": 0, "right": 0, "useful": 0, "wrong": 0})
            g["alerts"] += 1
            g["useful"] += r["vote"] == 1
            g["wrong"] += r["vote"] == -1
            if r["price"] is not None and r["price_at_alert"] is not None:
                d = (r["price"] - r["price_at_alert"]) * r["direction"]
                g["checked"] += 1
                g["moved"] += abs(d) > 1e-9
                g["right"] += d > 1e-9
        return sorted(({"key": k, **v} for k, v in out.items()), key=lambda x: -x["alerts"])

    by_source = group(lambda r: r["source"])
    for g in by_source:
        g["headlines"] = heads.get(g["key"], 0)
    return {
        "total_alerts": len(rows), "duplicates_skipped": dups,
        "by_source": by_source,
        "by_kind": group(lambda r: r["kind"]),
        "by_category": group(lambda r: _category(r["tags"])),
        "by_volume_24h": group(lambda r: vol_bucket(r["volume_24h"])),
        "by_prompt": group(lambda r: (r["model"] or "").split("+")[-1] if "+" in (r["model"] or "") else "v1"),
    }


@app.get("/api/markets")
def markets(q: str = Query(default="", max_length=500)) -> list[dict]:
    with closing(connect()) as conn:
        if q.strip():
            escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = conn.execute(
                "SELECT * FROM markets WHERE active=1 AND (question LIKE ? ESCAPE '\\' OR rules LIKE ? ESCAPE '\\')"
                " ORDER BY liquidity DESC LIMIT 100",
                (f"%{escaped}%", f"%{escaped}%"),
            )
        else:
            rows = conn.execute("SELECT * FROM markets WHERE active=1 ORDER BY liquidity DESC LIMIT 100")
        return [dict(row) for row in rows]


def _add_and_process(payload: HeadlineIn) -> dict:
    conn = connect()
    try:
        headline_id = add_paste(conn, payload.title.strip(), payload.body, payload.url)
        process_headline(conn, headline_id)
        return _feed_item(conn, headline_id)
    finally:
        conn.close()


@app.post("/api/headline")
async def headline(payload: HeadlineIn, request: Request) -> dict:
    _rate_limit(request)
    return await run_in_threadpool(_add_and_process, payload)


@app.post("/api/feedback")
def feedback(payload: FeedbackIn) -> dict:
    with closing(connect()) as conn:
        if not conn.execute("SELECT 1 FROM alerts WHERE id=?", (payload.alert_id,)).fetchone():
            raise HTTPException(status_code=404, detail="alert not found")
        cursor = conn.execute(
            "INSERT INTO feedback (alert_id, vote, created_at) VALUES (?,?,?)",
            (payload.alert_id, payload.vote, now()),
        )
        conn.commit()
        return {"id": cursor.lastrowid, "alert_id": payload.alert_id, "vote": payload.vote}


def worker_loop(poll_seconds: float = 1.0) -> None:
    """Process the oldest queued headline, then check for another."""
    conn = connect()
    try:
        while True:
            # atomic claim, so several workers can drain the queue in parallel without double-judging
            row = conn.execute(
                "UPDATE headlines SET status='processing' WHERE id=(SELECT id FROM headlines WHERE status='new'"
                " ORDER BY id DESC LIMIT 1) RETURNING id"
            ).fetchone()
            conn.commit()
            if row is None:
                time.sleep(poll_seconds)
                continue
            try:
                process_headline(conn, row["id"])
            except Exception as exc:
                print(f"[worker] headline {row['id']}: {exc}", flush=True)
                time.sleep(poll_seconds)
    finally:
        conn.close()


# Mount last so /api/* routes always win. StaticFiles serves web/index.html at / when present.
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("worker",))
    parser.add_argument("--poll", type=float, default=1.0)
    args = parser.parse_args()
    worker_loop(args.poll)
