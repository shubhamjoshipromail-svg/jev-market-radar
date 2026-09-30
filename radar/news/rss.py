"""RSS poller. python -m radar.news.rss [--loop 60] [--backfill]
First run only marks existing items as seen (status='seed') so we don't judge hours-old news; use --backfill to judge them."""
import argparse
import os
import html
import re
import time
from datetime import datetime, timezone

import feedparser
import httpx

from radar.db import connect
from radar.news import store

FEEDS = {
    "bbc_world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "bbc_business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "guardian_world": "https://www.theguardian.com/world/rss",
    "aljazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "cnbc_top": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "npr": "https://feeds.npr.org/1001/rss.xml",
    "gnews_top": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    "politico": "https://rss.politico.com/politics-news.xml",
    "espn": "https://www.espn.com/espn/rss/news",
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "foxnews": "https://moxie.foxnews.com/google-publisher/latest.xml",
    "nyt_home": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "wsj_world": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
}
UA = {"User-Agent": "Mozilla/5.0 (jev-market-radar; personal research)"}


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def _published(e) -> str | None:
    t = e.get("published_parsed") or e.get("updated_parsed")
    return datetime(*t[:6], tzinfo=timezone.utc).isoformat() if t else None


def poll_once(conn, http: httpx.Client, seed: bool = False) -> int:
    new = 0
    for name, url in FEEDS.items():
        try:
            r = http.get(url)
            feed = feedparser.parse(r.content)
        except Exception as e:
            print(f"[rss] {name}: {e}")
            continue
        for e in feed.entries:
            hid = store(conn, f"rss:{name}", _clean(e.get("title")), e.get("link"),
                        _clean(e.get("summary"))[:1500], _published(e))
            if hid:
                new += 1
                if seed:
                    conn.execute("UPDATE headlines SET status='seed' WHERE id=?", (hid,))
    conn.commit()
    return new


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int)
    ap.add_argument("--backfill", action="store_true")
    a = ap.parse_args()
    conn = connect()
    with httpx.Client(timeout=20, follow_redirects=True, headers=UA) as http:
        first = conn.execute("SELECT count(*) FROM headlines WHERE source LIKE 'rss:%'").fetchone()[0] == 0
        while True:
            n = poll_once(conn, http, seed=first and not a.backfill)
            print(f"[rss] {n} new{' (seeded, not judged)' if first and not a.backfill else ''}", flush=True)
            if first and not a.backfill:
                # fresh deploy: judge the newest few so the board isn't empty on day one
                k = int(os.getenv("RADAR_BACKFILL_ON_START", "0"))
                if k:
                    conn.execute("UPDATE headlines SET status='new' WHERE id IN (SELECT id FROM headlines"
                                 " WHERE status='seed' ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?)", (k,))
                    conn.commit()
            first = False
            if not a.loop:
                break
            time.sleep(a.loop)
