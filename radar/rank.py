"""Ranking in plain code over stored Jev answers, so weights can be tuned without re-asking Jev.

opportunity(): how worth looking at one (headline, market) pair is, 0-100.
leaderboard(): per market, the weighted YES/NO lean across every recent headline that touched it.
Direction words only ("news favours YES"); never "buy"/"sell". It's information, not advice.
"""
import math
from datetime import datetime, timezone

DIR = {"resolves_yes": 1, "raises_yes": 1, "resolves_no": -1, "lowers_yes": -1}

# Opportunity weights (sum to 1). Tune on the scorecard, then bump the comment date.
W = {"impact": 0.30, "room": 0.20, "solid": 0.15, "trade": 0.15, "relevance": 0.10, "fresh": 0.10}
LEAN_HALF_LIFE_H = 6  # a headline's weight in the market lean halves every 6 hours


def _impact(j: dict) -> float:
    mag = j.get("magnitude")
    impact = mag / 2 if mag is not None else (1.0 if j["effect"].startswith("resolves") else 0.5)
    return max(impact, 0.9) if j["effect"].startswith("resolves") else impact


def _room(direction: int, price: float | None, impact: float) -> float:
    """How far the price can plausibly travel toward the side the news favours. Capped by the size of the
    news, so a small nudge on a 3c long shot doesn't look like a 97c opportunity."""
    raw = 0.5 if price is None else (1 - price) if direction > 0 else price
    return min(raw, 0.1 + 0.6 * impact)


def _trade(volume_24h: float | None) -> float:
    """$10 -> 0.2, $1k -> 0.6, $100k+ -> 1. Can you actually get in and out?"""
    return 0.4 if volume_24h is None else min(1.0, math.log10(volume_24h + 1) / 5)


def opportunity(j: dict, price_now: float | None, price_at: float | None, volume_24h: float | None,
                is_game: bool = False) -> dict | None:
    d = DIR.get(j.get("effect"))
    if d is None:
        return None
    moved = (price_now - price_at) * d if price_now is not None and price_at is not None else 0.0
    parts = {
        "impact": _impact(j),
        "room": _room(d, price_now, _impact(j)),
        "solid": (j.get("strength") or 0) / 2,
        "trade": _trade(volume_24h),
        "relevance": j.get("relevant") or 0,
        "fresh": 1 - min(max(moved, 0) / 0.10, 1),  # 10c already moved our way = fully stale
    }
    score = 100 * sum(W[k] * parts[k] for k in W)
    if (j.get("same_period") if j.get("same_period") is not None else 1) < 0.5:
        score *= 0.3  # probably about a different meeting/date
    if is_game:
        score *= 0.5  # single games reprice live; news rarely beats the crowd there
    score = round(score)
    return {"score": score, "level": "High" if score >= 70 else "Medium" if score >= 50 else "Low",
            "favours": "YES" if d > 0 else "NO", "parts": {k: round(v, 2) for k, v in parts.items()}}


def leaderboard(conn, hours: int = 24, limit: int = 12) -> list[dict]:
    """Combine every recent headline's verdict on each market into one weighted lean (-1 NO .. +1 YES)."""
    rows = conn.execute(
        "SELECT j.*, h.title, h.source, h.fetched_at AS h_at, m.question, m.url, m.yes_price AS p_now,"
        " m.volume_24h, m.is_game, m.outcomes FROM judgments j JOIN headlines h ON h.id=j.headline_id"
        " JOIN markets m ON m.id=j.market_id WHERE h.source<>'paste' AND m.active=1 AND j.relevant>=0.7"
        " AND COALESCE(j.same_period,1)>=0.5 AND j.effect<>'no_effect' AND h.fetched_at > datetime('now', ?)",
        (f"-{hours} hours",)).fetchall()
    now = datetime.now(timezone.utc)
    agg: dict[str, dict] = {}
    for r in rows:
        r = dict(r)
        d = DIR.get(r["effect"])
        if d is None:
            continue
        age_h = (now - datetime.fromisoformat(r["h_at"])).total_seconds() / 3600
        w = r["relevant"] * ((r["strength"] or 0) / 2) * _impact(r) * 0.5 ** (age_h / LEAN_HALF_LIFE_H)
        a = agg.setdefault(r["market_id"], {"market": {"id": r["market_id"], "question": r["question"], "url": r["url"],
                                                        "yes_price": r["p_now"], "volume_24h": r["volume_24h"],
                                                        "outcomes": r["outcomes"]},
                                             "is_game": r["is_game"], "wsum": 0.0, "lean_num": 0.0, "imp": 0.0,
                                             "headlines": []})
        a["wsum"] += w
        a["imp"] += w * _impact(r)
        a["lean_num"] += w * d
        a["headlines"].append({"id": r["headline_id"], "title": r["title"], "source": r["source"], "effect": r["effect"],
                               "weight": round(w, 3), "at": r["h_at"]})
    out = []
    for a in agg.values():
        if a["wsum"] < 0.15:
            continue
        lean = a["lean_num"] / a["wsum"]
        d = 1 if lean > 0 else -1
        evidence = 1 - math.exp(-a["wsum"])  # more (and stronger) headlines -> closer to 1
        impact = a["imp"] / a["wsum"]
        score = 100 * abs(lean) * evidence * _room(d, a["market"]["yes_price"], impact) * _trade(a["market"]["volume_24h"])
        if a["is_game"]:
            score *= 0.5
        heads = sorted(a["headlines"], key=lambda h: -h["weight"])
        out.append({"market": a["market"], "lean": round(lean, 2), "favours": "YES" if d > 0 else "NO",
                    "agree": sum(1 for h in heads if (DIR[h["effect"]] > 0) == (d > 0)), "n": len(heads),
                    "evidence": round(evidence, 2), "score": round(score), "headlines": heads[:3]})
    return sorted(out, key=lambda x: -x["score"])[:limit]
