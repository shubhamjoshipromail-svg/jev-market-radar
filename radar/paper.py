"""Paper trading: would following Radar's #1 pick per headline make or lose money? No real money, ever.

Rule (fixed up front, same for backtest and live):
  - For each real (not pasted) headline, take its #1 market by opportunity score; trade only if score >= MIN_SCORE.
  - Buy $STAKE of the side the news favours at the market price plus half the spread (a realistic fill cost).
  - Exit: live account sells after HOLD_H hours, or settles at $1/$0 if the market resolves first. Selling also pays half
    the spread. The backtest reports several exit rules side by side.

python -m radar.paper --backtest [--hours 24] [--min-score 60]   # fast replay over past picks with real price history
python -m radar.paper --loop 120                                  # live paper account, updates every 2 min
"""
import argparse
import json
import time
from datetime import datetime, timezone

import httpx

from radar.db import connect, now
from radar.rank import opportunity

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB_HISTORY = "https://clob.polymarket.com/prices-history"
STAKE = 100.0
START_EQUITY = 10_000.0
MIN_SCORE = 70
HOLD_H = 24
MIN_HALF_SPREAD = 0.005
# Live rule v2 (set 2026-09-30 after the first backtest lost -14.5%, $287 of $319 of it to the spread):
# only liquid, mid-priced markets where the news is at least a clear change.
LIVE_PRICE_BAND = (0.10, 0.90)   # price of the side we'd buy
LIVE_MAX_SPREAD = 0.02
LIVE_MIN_VOLUME_24H = 10_000
LIVE_MIN_MAGNITUDE = 0.7
BACKTEST_EXITS_MIN = [60, 360, None]  # None = mark to the latest price

_http = httpx.Client(timeout=20)
_market_cache: dict[str, dict] = {}


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def market_info(market_id: str, fresh: bool = False) -> dict:
    """YES token id, live mid, half spread, resolution state."""
    if not fresh and market_id in _market_cache:
        return _market_cache[market_id]
    m = _http.get(f"{GAMMA}/{market_id.split(':', 1)[1]}").json()
    prices = json.loads(m.get("outcomePrices") or "[]")
    info = {"token": json.loads(m.get("clobTokenIds") or "[null]")[0],
            "yes": float(prices[0]) if prices else None,
            "half_spread": max(MIN_HALF_SPREAD, float(m.get("spread") or 0) / 2),
            "closed": bool(m.get("closed")), "question": m.get("question")}
    _market_cache[market_id] = info
    return info


def history(token: str, start: int, end: int) -> list[tuple[int, float]]:
    r = _http.get(CLOB_HISTORY, params={"market": token, "startTs": start, "endTs": end, "fidelity": 1})
    return [(p["t"], float(p["p"])) for p in r.json().get("history", [])]


def price_at(hist: list[tuple[int, float]], t: int) -> float | None:
    before = [p for ts, p in hist if ts <= t]
    if before:
        return before[-1]
    after = [p for ts, p in hist if ts > t]
    return after[0] if after else None


def picks(conn, hours: float, min_score: int, since_iso: str | None = None) -> list[dict]:
    """#1 market per real headline, by opportunity score, above the threshold."""
    rows = conn.execute(
        "SELECT j.*, h.title, h.fetched_at AS h_at, m.question, m.url, m.yes_price AS p_now, m.volume_24h, m.is_game,"
        " a.price_at_alert FROM judgments j JOIN headlines h ON h.id=j.headline_id JOIN markets m ON m.id=j.market_id"
        " LEFT JOIN alerts a ON a.judgment_id=j.id WHERE h.source<>'paste' AND j.relevant>=0.7"
        " AND h.fetched_at > datetime('now', ?) AND (? IS NULL OR h.fetched_at > ?)",
        (f"-{hours} hours", since_iso, since_iso)).fetchall()
    best: dict[int, dict] = {}
    for r in rows:
        r = dict(r)
        p_at = r["price_at_alert"] if r["price_at_alert"] is not None else r["yes_price_at"]
        op = opportunity(r, p_at, p_at, r["volume_24h"], bool(r["is_game"]))  # scored as of the headline
        if not op or op["score"] < min_score:
            continue
        if r["headline_id"] not in best or op["score"] > best[r["headline_id"]]["op"]["score"]:
            best[r["headline_id"]] = {**r, "op": op, "p_at": p_at}
    return sorted(best.values(), key=lambda x: x["h_at"])


def _pnl(side: str, entry_yes: float, exit_yes: float, half_spread: float) -> tuple[float, float, float]:
    """Returns (entry price paid for our side, exit price received, P&L on STAKE)."""
    entry = (entry_yes if side == "YES" else 1 - entry_yes) + half_spread
    exit_ = (exit_yes if side == "YES" else 1 - exit_yes) - half_spread
    entry = min(max(entry, 0.01), 0.99)
    exit_ = min(max(exit_, 0.0), 1.0)
    return entry, exit_, STAKE / entry * exit_ - STAKE


def backtest(conn, hours: float = 24, min_score: int = 60) -> dict:
    trades, end = [], int(time.time())
    for p in picks(conn, hours, min_score):
        try:
            info = market_info(p["market_id"])
            t0 = _ts(p["h_at"])
            hist = history(info["token"], t0 - 3600, end) if info["token"] else []
        except Exception as e:
            print(f"[paper] {p['market_id']}: {e}")
            continue
        entry_yes = price_at(hist, t0) if hist else p["p_at"]
        if entry_yes is None:
            continue
        row = {"headline": p["title"], "question": p["question"], "url": p["url"], "side": p["op"]["favours"],
               "score": p["op"]["score"], "at": p["h_at"], "entry_yes": entry_yes, "exits": {}}
        for mins in BACKTEST_EXITS_MIN:
            t = end if mins is None else t0 + mins * 60
            if t > end:
                continue  # not enough time has passed for this exit rule yet
            x = price_at(hist, t) if hist else None
            if x is None:
                continue
            e, xr, pnl = _pnl(row["side"], entry_yes, x, info["half_spread"])
            row["exits"]["now" if mins is None else f"{mins}m"] = {"exit_yes": x, "pnl": round(pnl, 2)}
        row["entry_paid"] = round(_pnl(row["side"], entry_yes, entry_yes, info["half_spread"])[0], 4)
        trades.append(row)
    summary = {}
    for k in ["60m", "360m", "now"]:
        rs = [t["exits"][k]["pnl"] for t in trades if k in t["exits"]]
        if rs:
            summary[k] = {"trades": len(rs), "wins": sum(r > 0 for r in rs), "flat": sum(r == 0 for r in rs),
                          "pnl": round(sum(rs), 2), "return_pct": round(100 * sum(rs) / (STAKE * len(rs)), 1)}
    spread_cost = sum(STAKE / t["entry_paid"] * (2 * (t["entry_yes"] if t["side"] == "YES" else 1 - t["entry_yes"])
                                                   - t["entry_paid"]) - STAKE for t in trades)
    out = {"generated_at": now(), "hours": hours, "min_score": min_score, "stake": STAKE,
           "spread_cost": round(spread_cost, 2),
           "summary": summary, "trades": trades}
    conn.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('backtest', ?)", (json.dumps(out),))
    conn.commit()
    return out


# ---- live paper account -------------------------------------------------------------------------------------
def _started(conn) -> str:
    r = conn.execute("SELECT v FROM kv WHERE k='paper_started'").fetchone()
    if r:
        return r[0]
    conn.execute("INSERT INTO kv (k, v) VALUES ('paper_started', ?)", (now(),))
    conn.commit()
    return now()


def step(conn) -> None:
    started = _started(conn)
    have = {r[0] for r in conn.execute("SELECT headline_id FROM paper_trades")}
    for p in picks(conn, 48, MIN_SCORE, since_iso=started):
        if p["headline_id"] in have:
            continue
        if (p.get("magnitude") or 0) < LIVE_MIN_MAGNITUDE or (p.get("volume_24h") or 0) < LIVE_MIN_VOLUME_24H:
            continue
        info = market_info(p["market_id"], fresh=True)
        if info["closed"] or info["yes"] is None or info["half_spread"] * 2 > LIVE_MAX_SPREAD:
            continue
        side_px = info["yes"] if p["op"]["favours"] == "YES" else 1 - info["yes"]
        if not LIVE_PRICE_BAND[0] <= side_px <= LIVE_PRICE_BAND[1]:
            continue
        entry, _, _ = _pnl(p["op"]["favours"], info["yes"], info["yes"], info["half_spread"])
        conn.execute(
            "INSERT INTO paper_trades (headline_id, judgment_id, market_id, side, score, stake, entry_price, entry_at,"
            " half_spread, last_price, status) VALUES (?,?,?,?,?,?,?,?,?,?, 'open')",
            (p["headline_id"], p["id"], p["market_id"], p["op"]["favours"], p["op"]["score"], STAKE, entry, now(),
             info["half_spread"], entry))
        print(f"[paper] open {p['op']['favours']} {p['question'][:60]} @ {entry:.3f}", flush=True)
    for t in conn.execute("SELECT * FROM paper_trades WHERE status='open'").fetchall():
        info = market_info(t["market_id"], fresh=True)
        if info["yes"] is None:
            continue
        side_px = info["yes"] if t["side"] == "YES" else 1 - info["yes"]
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(t["entry_at"])).total_seconds() / 3600
        if info["closed"] and side_px in (0.0, 1.0):
            exit_px, reason = side_px, "resolved"
        elif age_h >= HOLD_H:
            exit_px, reason = max(side_px - t["half_spread"], 0.0), f"sold after {HOLD_H}h"
        else:
            conn.execute("UPDATE paper_trades SET last_price=? WHERE id=?", (side_px, t["id"]))
            continue
        pnl = t["stake"] / t["entry_price"] * exit_px - t["stake"]
        conn.execute("UPDATE paper_trades SET status='closed', exit_price=?, exit_at=?, exit_reason=?, pnl=?, last_price=?"
                     " WHERE id=?", (exit_px, now(), reason, pnl, side_px, t["id"]))
        print(f"[paper] close #{t['id']} {reason} pnl {pnl:+.2f}", flush=True)
    realized = conn.execute("SELECT COALESCE(sum(pnl),0) FROM paper_trades WHERE status='closed'").fetchone()[0]
    unreal = conn.execute("SELECT COALESCE(sum(stake/entry_price*last_price - stake),0) FROM paper_trades"
                          " WHERE status='open'").fetchone()[0]
    conn.execute("INSERT INTO paper_equity (t, equity, realized, unrealized) VALUES (?,?,?,?)",
                 (now(), START_EQUITY + realized + unreal, realized, unreal))
    conn.commit()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--min-score", type=int, default=60)
    ap.add_argument("--loop", type=int)
    a = ap.parse_args()
    conn = connect()
    if a.backtest:
        out = backtest(conn, a.hours, a.min_score)
        print(json.dumps(out["summary"], indent=1))
        for t in out["trades"]:
            print(f"{t['score']:>3} {t['side']:<3} {t['entry_paid']:.3f} {json.dumps({k: v['pnl'] for k, v in t['exits'].items()})}  {t['question'][:55]}")
    else:
        while True:
            try:
                step(conn)
            except Exception as e:
                print(f"[paper] step failed: {e}", flush=True)
            if not a.loop:
                break
            time.sleep(a.loop)
