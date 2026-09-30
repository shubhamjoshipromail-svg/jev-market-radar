"""Alert policy: pure code over stored judgments. Thresholds live here so they can be tuned without re-asking Jev."""
import re

import os

from radar.db import now

ALERT_GAMES = os.getenv("RADAR_ALERT_GAMES", "0") == "1"  # single-game markets: judged and shown, not alerted

MIN_RELEVANT = 0.70
MIN_EFFECT_CONF = 0.60
MIN_STRENGTH = 1.0          # 0 speculation, 1 developing, 2 settled fact (tuned on eval 2026-09-29)
MIN_SAME_PERIOD = 0.5       # Jev reads dates as text, so this only vetoes clear mismatches
STALE_YES = 0.90            # news says it resolved YES but price is still below this -> stale
STALE_NO = 0.10
PRICED_OUT = 0.05           # skip nudges on markets already within 5c of where the news points

DIRECTION = {"resolves_yes": 1, "raises_yes": 1, "resolves_no": -1, "lowers_yes": -1}


# "Will 3 Fed cuts happen", "exactly", "or more": resolving these needs counting, a Jev weak spot
COUNT_MARKET = re.compile(r"^will \d+ |\bhow many\b|\bexactly\b|\bor more\b|\bat least \d", re.I)


def decide(j: dict, yes_price: float | None, question: str = "") -> tuple[str, int] | None:
    """Returns (kind, direction) or None."""
    if COUNT_MARKET.search(question) and j["effect"] in ("resolves_yes", "resolves_no"):
        return None  # don't claim a count-market resolution; a human or a counter in code must
    direction = DIRECTION.get(j["effect"])
    if direction is None:
        return None
    if j["relevant"] < MIN_RELEVANT or (j["effect_conf"] or 0) < MIN_EFFECT_CONF or j["strength"] < MIN_STRENGTH:
        return None
    if (j.get("same_period") if j.get("same_period") is not None else 1) < MIN_SAME_PERIOD:
        return None
    if yes_price is not None:
        if (direction > 0 and yes_price > 1 - PRICED_OUT) or (direction < 0 and yes_price < PRICED_OUT):
            return None  # already priced in
        # stale = the news settles the market but the price hasn't caught up
        if j["strength"] >= 1.5 and (
            (j["effect"] == "resolves_yes" and yes_price < STALE_YES)
            or (j["effect"] == "resolves_no" and yes_price > STALE_NO)
        ):
            return "stale_price", direction
    return "mover", direction


def maybe_alert(conn, judgment_id: int, headline_id: int, market_id: str, j: dict, yes_price,
                question: str = "", is_game: int = 0) -> int | None:
    if is_game and not ALERT_GAMES:
        return None
    d = decide(j, yes_price, question)
    if not d:
        return None
    cur = conn.execute(
        "INSERT INTO alerts (judgment_id, headline_id, market_id, kind, direction, price_at_alert, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (judgment_id, headline_id, market_id, d[0], d[1], yes_price, now()),
    )
    return cur.lastrowid
