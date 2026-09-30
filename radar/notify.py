"""Push alerts to your phone via ntfy.sh (no account; the secret topic name in .env is the only key).

python -m radar.notify            # loop: send new alerts, one notification per headline
python -m radar.notify --test     # send a test notification
Install the ntfy app and subscribe to the topic printed by: python -m radar.notify --where
"""
import argparse
import json
import time

import httpx

from radar.config import NTFY_SERVER, NTFY_TOPIC
from radar.db import connect

ARROW = {1: "⬆", -1: "⬇"}
EFFECT = {"resolves_yes": "resolves YES", "resolves_no": "resolves NO", "raises_yes": "YES more likely",
          "lowers_yes": "YES less likely"}
MAX_PER_MIN = 10  # ntfy.sh free tier rate-limits bursts


def publish(http: httpx.Client, title: str, message: str, click: str | None = None, priority: int = 3, tags=()):
    body = {"topic": NTFY_TOPIC, "title": title, "message": message, "priority": priority, "tags": list(tags)}
    if click:
        body["click"] = click
    r = http.post(NTFY_SERVER, content=json.dumps(body), headers={"Content-Type": "application/json"})
    r.raise_for_status()


def pending(conn):
    rows = conn.execute(
        "SELECT a.id, a.headline_id, a.kind, a.why, a.direction, a.price_at_alert, j.effect, j.strength,"
        " h.title, h.source, h.url AS h_url, m.question, m.url AS m_url"
        " FROM alerts a JOIN judgments j ON j.id=a.judgment_id JOIN headlines h ON h.id=a.headline_id"
        " JOIN markets m ON m.id=a.market_id WHERE a.notified=0 ORDER BY a.id").fetchall()
    by_headline = {}
    for r in rows:
        by_headline.setdefault(r["headline_id"], []).append(dict(r))
    return by_headline


def render(alerts: list[dict]) -> tuple[str, str, str, int]:
    alerts.sort(key=lambda a: (a["kind"] != "stale_price", -a["strength"]))
    top = alerts[0]
    stale = any(a["kind"] == "stale_price" for a in alerts)
    lines = [f"{ARROW[a['direction']]} {a['question']} — {EFFECT.get(a['effect'], a['effect'])}, "
             f"now {round((a['price_at_alert'] or 0) * 100)}¢{' · STALE' if a['kind'] == 'stale_price' else ''}"
             + (f"\n   {a['why']}" if a.get("why") else "") for a in alerts[:5]]
    if len(alerts) > 5:
        lines.append(f"+{len(alerts) - 5} more on the dashboard")
    lines.append(f"Source: {top['source'].split(':', 1)[-1]}")
    title = f"{'STALE PRICE: ' if stale else ''}{top['title']}"[:200]
    return title, "\n".join(lines), top["m_url"] or top["h_url"], 5 if stale else 3


def loop(poll: float = 5.0):
    conn = connect()
    # don't flood the phone with alerts created before notifications were switched on
    conn.execute("UPDATE alerts SET notified=1 WHERE notified=0 AND created_at < datetime('now','-10 minutes')")
    conn.commit()
    sent = []
    with httpx.Client(timeout=15) as http:
        while True:
            for hid, alerts in pending(conn).items():
                sent = [t for t in sent if t > time.time() - 60]
                if len(sent) >= MAX_PER_MIN:
                    break
                title, msg, click, prio = render(alerts)
                try:
                    publish(http, title, msg, click, prio, tags=["rotating_light"] if prio == 5 else ["chart_with_upwards_trend"])
                except Exception as e:
                    print(f"[notify] send failed: {e}", flush=True)
                    break
                conn.execute(f"UPDATE alerts SET notified=1 WHERE id IN ({','.join('?' * len(alerts))})",
                             [a["id"] for a in alerts])
                conn.commit()
                sent.append(time.time())
                print(f"[notify] sent: {title[:80]} ({len(alerts)} market(s))", flush=True)
            time.sleep(poll)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--where", action="store_true")
    a = ap.parse_args()
    if not NTFY_TOPIC:
        raise SystemExit("NTFY_TOPIC missing from .env")
    if a.where:
        print(f"Subscribe in the ntfy app to:\n  server: {NTFY_SERVER}\n  topic:  {NTFY_TOPIC}\n  or open: {NTFY_SERVER}/{NTFY_TOPIC}")
    elif a.test:
        with httpx.Client(timeout=15) as http:
            publish(http, "Market Radar is connected", "You'll get a notification like this when news moves a market.",
                    "http://127.0.0.1:8000", 3, ["white_check_mark"])
        print("sent")
    else:
        loop()
