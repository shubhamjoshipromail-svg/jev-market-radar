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
        "SELECT j.*, m.question AS market_question, m.url AS market_url,"
        " m.yes_price AS market_yes_price, a.id AS alert_id, a.kind AS alert_kind,"
        " a.direction AS alert_direction FROM judgments j"
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
                },
                "relevant": row["relevant"],
                "effect": row["effect"],
                "effect_conf": row["effect_conf"],
                "strength": row["strength"],
                "latency_ms": row["latency_ms"],
                "alert": (
                    {
                        "id": row["alert_id"],
                        "kind": row["alert_kind"],
                        "direction": row["alert_direction"],
                    }
                    if row["alert_id"] is not None
                    else None
                ),
            }
        )
    return {"headline": dict(headline), "judgments": judgments}


@app.get("/api/feed")
def feed(limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    with closing(connect()) as conn:
        ids = conn.execute(
            "SELECT id FROM headlines ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC LIMIT ?",
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
            "by_kind": by_kind,
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
async def headline(payload: HeadlineIn) -> dict:
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
            row = conn.execute(
                "SELECT id FROM headlines WHERE status='new' ORDER BY id LIMIT 1"
            ).fetchone()
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
