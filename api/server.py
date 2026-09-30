"""FastAPI surface and headline worker for Jev Market Radar.

Run the API with ``uvicorn api.server:app`` and the worker with
``python -m api.server worker``.
"""
from __future__ import annotations

import argparse
import html
import asyncio
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, StreamingResponse
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


_EFFECT = {"resolves_yes": "settles it as YES", "raises_yes": "makes YES more likely",
           "resolves_no": "settles it as NO", "lowers_yes": "makes YES less likely"}


@app.get("/a/{alert_id}", response_class=HTMLResponse)
def share_alert(alert_id: int, request: Request) -> HTMLResponse:
    """Server-rendered page for one alert, so link previews (iMessage, Slack, X) show the story, not a blank app."""
    with closing(connect()) as conn:
        r = conn.execute(
            "SELECT a.*, j.effect, j.strength, h.title, h.source, h.url AS h_url, h.fetched_at, m.question, m.url AS m_url,"
            " m.yes_price, m.outcomes FROM alerts a JOIN judgments j ON j.id=a.judgment_id JOIN headlines h ON h.id=a.headline_id"
            " JOIN markets m ON m.id=a.market_id WHERE a.id=?", (alert_id,)).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="alert not found")
    e = html.escape
    yes = (json.loads(r["outcomes"] or "[]") or ["Yes"])[0]
    yes = "YES" if str(yes).lower() == "yes" else str(yes)
    effect = _EFFECT.get(r["effect"], "affects this market").replace("YES", yes)
    at, now_p = r["price_at_alert"], r["yes_price"]
    moved = (now_p - at) * r["direction"] if at is not None and now_p is not None else 0
    from datetime import datetime, timezone
    age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(r["created_at"])).total_seconds() / 60
    label = ("What-if (pasted, not live news)" if r["source"] == "paste" else "Already priced in" if moved >= 0.02 else "No reaction" if age_min > 30
             else "Settled, price lagging" if r["kind"] == "stale_price" else "Early")
    cents = lambda p: "–" if p is None else f"{p * 100:.1f}¢"
    solid = "Confirmed news" if (r["strength"] or 0) >= 1.4 else "Reported, not final" if (r["strength"] or 0) >= 0.7 else "Rumour"
    base = str(request.base_url).rstrip("/")
    title = f"{label}: {r['question']}"
    desc = f"“{r['title']}” {effect}. {yes} was {cents(at)} at the headline, {cents(now_p)} now."
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">
<meta property="og:type" content="article"><meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<meta property="og:image" content="{base}/media/poster.png"><meta property="og:url" content="{base}/a/{alert_id}">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{e(title)}"><meta name="twitter:description" content="{e(desc)}">
<meta name="twitter:image" content="{base}/media/poster.png">
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/style.css"><style>.share{{max-width:640px;margin:40px auto;padding:0 16px}}.share .lead{{color:var(--ink-3);font-size:13px;margin:0 0 10px}}
.share .foot{{margin-top:22px;font-size:13px;color:var(--ink-2)}}.share .foot a{{color:inherit}}</style></head><body>
<main class="share"><p class="lead">Radar · news → Polymarket, judged by Jev</p>
<article class="acard v-{'whatif' if label.startswith('What') else 'priced' if label.startswith('Already') else 'quiet' if label.startswith('No') else 'settled' if label.startswith('Settled') else 'early'}">
<header><span class="vlabel v-{'whatif' if label.startswith('What') else 'priced' if label.startswith('Already') else 'quiet' if label.startswith('No') else 'settled' if label.startswith('Settled') else 'early'}">{e(label)}</span>
<span class="vdesc">{e(r['source'].split(':', 1)[-1])} · {e((r['fetched_at'] or '')[:16].replace('T', ' '))} UTC</span></header>
<p class="whyline" style="margin:10px 0 0">“{e(r['title'])}”</p>
<h3><a href="{e(r['m_url'] or '#')}">{e(r['question'])}</a></h3>
<dl class="facts"><div><dt>Effect</dt><dd class="{'up' if r['direction'] > 0 else 'down'}">{'▲' if r['direction'] > 0 else '▼'} {e(effect[0].upper() + effect[1:])}</dd></div>
<div><dt>Price of {e(yes)}</dt><dd class="mono">{cents(at)} at the headline → {cents(now_p)} now</dd></div><div><dt>How solid</dt><dd>{solid}</dd></div></dl>
{f'<p class="whyline">{e(r["why"])}</p>' if r["why"] else ''}
<div class="acts"><a class="btn primary" href="{e(r['m_url'] or '#')}">Open on Polymarket ↗</a>{f'<a class="btn" href="{e(r["h_url"])}">Read the source ↗</a>' if r["h_url"] else ''}</div></article>
<p class="foot">Radar reads the news, checks every live Polymarket market's rules, and flags the ones the news moves before the price catches up.
<a href="/">How it works</a> · <a href="/board.html">Live board</a>. Information, not trading advice.</p></main></body></html>"""
    return HTMLResponse(body)


# Mount last so /api/* routes always win. StaticFiles serves web/index.html at / when present.
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("worker",))
    parser.add_argument("--poll", type=float, default=1.0)
    args = parser.parse_args()
    worker_loop(args.poll)
