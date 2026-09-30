"""Headline -> candidate markets (BM25 prefilter) -> Jev judgments -> alerts.

Entry point for the worker: process_headline(conn, headline_id) -> list[judgment_id]
Test by hand:  python -m radar.match --paste "Fed cuts rates by 50bp" [--k 20]
"""
import argparse
import asyncio
import json
import re
import time
from datetime import datetime, timezone

import httpx
from rank_bm25 import BM25Okapi

from radar import alerts, jev, llm
from radar.config import JEV_CONCURRENCY, PREFILTER_K
from radar.db import connect, now
from radar.judge import judge_pair

# Numeric price-threshold markets ("BTC between $78k and $80k on Oct 1") are settled by a price feed, not by
# reading news: Jev is unreliable at numeric comparison, so they never reach the judge.
PRICE_MARKET = re.compile(
    r"\bprice of\b|\bbetween \$[\d,.]+[kmb]? and \$|\b(above|below|reach|hit|dip to|close (above|below))\b[^?]*\$[\d,.]+",
    re.I,
)
PER_TEMPLATE = 2  # "Will 1/2/3... Fed cuts happen" families would otherwise crowd out everything else


def _template(q: str) -> str:
    return re.sub(r"[\d$.,%]+", "#", q.lower())


STOP = set("a an the of in on at to for by with and or is are was were be will would this that it as from market resolve resolves yes no if otherwise".split())
_index = {"key": None, "bm25": None, "rows": []}


def _tok(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOP and len(t) > 1]


INDEX_TTL = 900  # s. Building BM25 over ~38k markets takes ~3.5 s, so don't redo it on every price sync


def _load_index(conn):
    count = conn.execute("SELECT count(*) FROM markets WHERE active=1").fetchone()[0]
    old = _index["key"]
    stale = old is None or time.time() - old[1] > INDEX_TTL or abs(count - old[0]) > max(50, old[0] * 0.02)
    if stale:
        key = (count, time.time())
        rows = [dict(r) for r in conn.execute("SELECT * FROM markets WHERE active=1")]
        # question counts double: it's the most specific text
        docs = [_tok(r["question"]) * 2 + _tok(r["rules"])[:300] for r in rows]
        _index.update(key=key, rows=rows, bm25=BM25Okapi(docs) if rows else None)
    return _index


def candidates(conn, headline: dict, k: int = PREFILTER_K) -> list[dict]:
    idx = _load_index(conn)
    if not idx["bm25"]:
        return []
    q = _tok(headline["title"] + " " + (headline.get("body") or "")[:500])
    q += _tok(" ".join(headline.get("search_terms") or [])) * 2  # LLM-expanded names weigh double
    scores = idx["bm25"].get_scores(q)
    nowiso = datetime.now(timezone.utc).isoformat()
    ranked = sorted(range(len(scores)), key=lambda i: -scores[i])
    out, per_template = [], {}
    for i in ranked:
        if scores[i] <= 0 or len(out) >= k:
            break
        m = idx["rows"][i]
        if m["end_date"] and m["end_date"] < nowiso:  # date logic stays in code
            continue
        if PRICE_MARKET.search(m["question"]):
            continue
        t = _template(m["question"])
        if per_template.get(t, 0) >= PER_TEMPLATE:
            continue
        per_template[t] = per_template.get(t, 0) + 1
        out.append(dict(m))
    # the index is cached, so re-read live fields (price, active) for just these candidates
    if out:
        fresh = {r["id"]: r for r in conn.execute(
            f"SELECT id, yes_price, active, volume_24h FROM markets WHERE id IN ({','.join('?' * len(out))})", [m["id"] for m in out])}
        out = [{**m, "yes_price": fresh[m["id"]]["yes_price"], "volume_24h": fresh[m["id"]]["volume_24h"]} for m in out if fresh.get(m["id"], {"active": 0})["active"]]
    return out


DUP_WINDOW_H = 12
DUP_MIN_OVERLAP = 0.3   # cheap word-overlap prefilter before asking Jev
DUP_MIN_P = 0.8


def _title_tokens(title: str) -> set[str]:
    return set(_tok(re.sub(r"\s+[-|–]\s+[^-|–]+$", "", title)))  # drop Google News " - Outlet" suffix


def find_duplicate(conn, h: dict) -> int | None:
    """Earlier headline (last 12h) reporting the same event, decided by Jev over word-overlap candidates."""
    mine = _title_tokens(h["title"])
    if not mine:
        return None
    rows = conn.execute(
        "SELECT id, title FROM headlines WHERE id<>? AND status='done' AND dup_of IS NULL"
        " AND fetched_at > datetime('now', ?)", (h["id"], f"-{DUP_WINDOW_H} hours")).fetchall()
    scored = sorted(((len(mine & t) / len(mine | t), r) for r in rows if (t := _title_tokens(r["title"]))),
                    key=lambda x: -x[0])
    cands = [r for s, r in scored[:5] if s >= DUP_MIN_OVERLAP]
    if not cands:
        return None
    questions = {f"E{i}": {
        "type": "noul",
        "instructions": f"Do `new` and `earlier.E{i}` report the same specific news event (same happening, "
                        "possibly worded differently or from another outlet)? Different events on the same topic are false.",
        "criteria": {"true": "Same event.", "false": "Different events, even if related."}} for i in range(len(cands))}
    state = {"new": h["title"], "earlier": {f"E{i}": r["title"] for i, r in enumerate(cands)}}

    async def ask():
        async with jev.client() as http:
            return await jev.ask(http, state, questions)
    res = asyncio.run(ask())
    best = max(range(len(cands)), key=lambda i: res["answers"][f"E{i}"]["noul"])
    return cands[best]["id"] if res["answers"][f"E{best}"]["noul"] >= DUP_MIN_P else None


async def _judge_all(headline: dict, markets: list[dict]) -> list[tuple[dict, dict | Exception]]:
    sem = asyncio.Semaphore(JEV_CONCURRENCY)
    async with jev.client() as http:
        async def one(m):
            async with sem:
                try:
                    return m, await judge_pair(http, headline, m)
                except Exception as e:  # keep the rest of the batch
                    return m, e
        return await asyncio.gather(*(one(m) for m in markets))


def process_headline(conn, headline_id: int, k: int = PREFILTER_K) -> list[int]:
    h = conn.execute("SELECT * FROM headlines WHERE id=?", (headline_id,)).fetchone()
    if not h:
        return []
    h = dict(h)
    conn.execute("UPDATE headlines SET status='processing' WHERE id=?", (headline_id,))
    conn.commit()
    ids, new_alerts = [], []
    try:
        if h["source"] != "paste":  # a pasted headline is always judged
            dup = find_duplicate(conn, h)
            if dup:
                conn.execute("UPDATE headlines SET status='duplicate', dup_of=? WHERE id=?", (dup, headline_id))
                return []
        if llm.enabled():
            async def expand():
                async with httpx.AsyncClient() as http:
                    return await llm.search_terms(http, h["title"], h.get("body") or "")
            h["search_terms"] = asyncio.run(expand())
            conn.execute("UPDATE headlines SET search_terms=? WHERE id=?", (json.dumps(h["search_terms"]), headline_id))
        markets = candidates(conn, h, k)
        results = asyncio.run(_judge_all(h, markets)) if markets else []
        for rank, (m, j) in enumerate(results):
            if isinstance(j, Exception):
                print(f"[match] {m['id']}: {j}")
                continue
            cur = conn.execute(
                "INSERT OR REPLACE INTO judgments (headline_id, market_id, prefilter_rank, relevant, same_period, effect,"
                " effect_probs, effect_conf, strength, strength_conf, magnitude, yes_price_at, latency_ms, input_tokens,"
                " cost_usd, model, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (headline_id, m["id"], rank, j["relevant"], j["same_period"], j["effect"], j["effect_probs"], j["effect_conf"],
                 j["strength"], j["strength_conf"], j["magnitude"], m["yes_price"], j["latency_ms"], j["input_tokens"],
                 j["cost_usd"], j["model"], now()),
            )
            ids.append(cur.lastrowid)
            aid = alerts.maybe_alert(conn, cur.lastrowid, headline_id, m["id"], j, m["yes_price"], m["question"],
                                     m.get("is_game") or 0, m.get("volume_24h"))
            if aid:
                new_alerts.append((aid, m, j))
        if new_alerts and llm.enabled():
            async def explain():
                async with httpx.AsyncClient() as http:
                    return await asyncio.gather(*(llm.why(http, h["title"], m["question"], m.get("rules") or "",
                                                          j["effect"], j["strength"]) for _, m, j in new_alerts))
            for (aid, _, _), text in zip(new_alerts, asyncio.run(explain())):
                if text:
                    conn.execute("UPDATE alerts SET why=? WHERE id=?", (text, aid))
        conn.execute("UPDATE headlines SET status='done' WHERE id=?", (headline_id,))
    except Exception:
        conn.execute("UPDATE headlines SET status='error' WHERE id=?", (headline_id,))
        raise
    finally:
        conn.commit()
    return ids


def add_paste(conn, title: str, body: str = "", url: str | None = None) -> int:
    cur = conn.execute(
        # 'processing', not 'new': the caller judges it now, so the background worker must not also pick it up
        "INSERT INTO headlines (source, url, title, body, published_at, fetched_at, status)"
        " VALUES ('paste',?,?,?,?,?,'processing')",
        (url, title, body, now(), now()),
    )
    conn.commit()
    return cur.lastrowid


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--paste", required=True)
    ap.add_argument("--k", type=int, default=PREFILTER_K)
    ap.add_argument("--prefilter-only", action="store_true")
    a = ap.parse_args()
    conn = connect()
    hid = add_paste(conn, a.paste)
    if a.prefilter_only:
        for m in candidates(conn, {"title": a.paste}, a.k):
            print(f"{m['yes_price']!s:>6}  {m['question']}")
        raise SystemExit
    process_headline(conn, hid, a.k)
    rows = conn.execute(
        "SELECT j.*, m.question, a.kind FROM judgments j JOIN markets m ON m.id=j.market_id"
        " LEFT JOIN alerts a ON a.judgment_id=j.id WHERE j.headline_id=? ORDER BY j.relevant DESC", (hid,))
    for r in rows:
        print(f"{r['relevant']:.2f} per={r['same_period']:.2f} {r['effect']:<13} s={r['strength']:.2f} p={r['yes_price_at']} {r['latency_ms']}ms "
              f"{r['kind'] or '':<11} {r['question']}")
