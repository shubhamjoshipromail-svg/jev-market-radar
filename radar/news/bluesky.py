"""Bluesky Jetstream listener for news-org accounts: real-time, free. python -m radar.news.bluesky"""
import asyncio
import json
from urllib.parse import urlencode

import websockets

from radar.db import connect
from radar.news import store

ACCOUNTS = {  # handle -> DID (resolved 2026-09-29 via com.atproto.identity.resolveHandle)
    "apnews.com": "did:plc:a67zdrt4nl2tv2qojpngogbq",
    "reuters.com": "did:plc:jbvnehrrdqoulco4rf5gxg5r",
    "nytimes.com": "did:plc:eclio37ymobqex2ncko63h4r",
    "bbcnews.bsky.social": "did:plc:ixvke777actf2fcveqlkdbp5",
    "washingtonpost.com": "did:plc:k5nskatzhyxersjilvtnz4lh",
    "cnn.com": "did:plc:dzezcmpb3fhcpns4n4xm4ur5",
    "politico.com": "did:plc:yf6hctt2ug3qyfty4in64yob",
    "axios.com": "did:plc:f6avy7jkujdhusski5n64joj",
    "bloomberg.com": "did:plc:uewxgchsjy4kmtu7dcxa77us",
    "theguardian.com": "did:plc:vovinwhtulbsx4mwfw26r5ni",
    "npr.org": "did:plc:ln72v57ivz2g46uqf4xxqiuh",
    "wsj.com": "did:plc:i3fhjvvkbmirhyu4aeihhrnv",
    "nbcnews.com": "did:plc:wmho6q2uiyktkam3jsvrms3s",
    "aljazeera.com": "did:plc:2lofqead276vtc5647ye7sl2",
    "espn.com": "did:plc:x7d6j54pm22ufehkes6jo4jf",
    "coindesk.com": "did:plc:wx2xiglkqnxmyshobdghoda7",
}
HANDLE = {v: k for k, v in ACCOUNTS.items()}
URL = "wss://jetstream2.us-east.bsky.network/subscribe?" + urlencode(
    [("wantedCollections", "app.bsky.feed.post")] + [("wantedDids", d) for d in ACCOUNTS.values()])


async def run():
    conn = connect()
    while True:
        try:
            async with websockets.connect(URL, max_size=2**20) as ws:
                print("[bluesky] connected", flush=True)
                async for raw in ws:
                    ev = json.loads(raw)
                    c = ev.get("commit") or {}
                    if ev.get("kind") != "commit" or c.get("operation") != "create":
                        continue
                    rec = c.get("record") or {}
                    if rec.get("reply"):
                        continue  # replies are chatter, not headlines
                    ext = ((rec.get("embed") or {}).get("external") or {})
                    text = ext.get("title") or rec.get("text") or ""
                    handle = HANDLE.get(ev["did"], ev["did"])
                    url = ext.get("uri") or f"https://bsky.app/profile/{handle}/post/{c.get('rkey')}"
                    hid = store(conn, f"bluesky:{handle}", text.split("\n")[0], url,
                                rec.get("text", "")[:1500], rec.get("createdAt"))
                    if hid:
                        print(f"[bluesky] {handle}: {text[:90]}", flush=True)
        except Exception as e:
            print(f"[bluesky] reconnecting after: {e}", flush=True)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(run())
