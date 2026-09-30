# Jev Market Radar: build plan

**Goal:** every breaking headline gets checked against every live prediction market's *resolution rules*, and stale prices get alerted, with a public track record.
**Definition of demo-ready:** live feed on a dashboard + Telegram alerts + a stats page showing hit rate of alerts (price moved as predicted within 10 min).
**Idea card:** `02_ideas/_raw/2026-09-29_idea_jev-market-radar_claude.md`
**Team:** Claude + Codex only. Add another agent only for a real compute/speed need (human, 2026-09-29).
**Coordination:** read and append to `COORD.md` before and after every work session.

## Architecture
```
news sources ─► radar/news/* ─► headlines ─► radar/match.py (prefilter top-40) ─► radar/judge.py (Jev) ─► judgments
markets API ─► radar/markets.py ─► markets (rules, end date, price)                                      │
                                                                         radar/alerts.py (code rules) ◄─┘
                                                     ├─► api/server.py (REST + SSE) ─► web/ dashboard
                                                     ├─► radar/telegram.py (bot)
                                                     └─► radar/tracker.py (price @ +1/+5/+60 min) ─► stats
```
Stack: Python 3.11+, SQLite (WAL) at `data/radar.db`, httpx, FastAPI + Server-Sent Events, plain HTML/JS (no build step).
Single source of truth for data shapes: `radar/schema.sql`. Don't change it without logging in COORD.md.

## Ownership (one owner per path, so no merge fights)
| Path | Owner | Task |
|---|---|---|
| `radar/schema.sql`, `radar/db.py`, `radar/config.py` | Claude | T0 (done at kickoff) |
| `radar/jev.py`, `radar/judge.py`, `radar/match.py`, `radar/alerts.py` | Claude | T3 |
| `radar/markets.py` | Claude | T1 (done) |
| `radar/news/*` | Codex | T2 |
| `api/server.py`, `radar/tracker.py`, `radar/telegram.py`, `scripts/run_all.sh` | Codex | T4, T5 |
| `web/*` | Claude | T6 |
| `eval/*` | Codex (labels) + Claude (run) | T7 |
| Keys, Telegram bot token, go/no-go on public channel | Shubham | H1–H3 |

## Contracts
**DB:** see `radar/schema.sql`. Everyone uses `radar/db.py:connect()`.
**API (Codex builds, Antigravity consumes). Build to this exactly:**
- `GET /api/feed?limit=50` → `[{headline, judgments:[{market, relevant, effect, strength, alert}]}]` newest first
- `GET /api/stream` → SSE events `headline`, `judgment`, `alert`, each `data:` is the row as JSON
- `GET /api/stats` → `{headlines, judgments, alerts, cost_usd, p50_latency_ms, hit_rate_10m, by_kind:{...}}`
- `GET /api/markets?q=` → market rows
- `POST /api/headline {title, body?, url?}` → runs the pipeline on a pasted headline, returns the feed item
- `POST /api/feedback {alert_id, vote: 1|-1}`
**Pipeline entry point (Claude builds, Codex calls):** `radar.match.process_headline(conn, headline_id) -> list[judgment_id]`

## Phases
**P0 Kickoff (now):** plan, coord log, schema, db, tasks queued. ✅
**P1 Data in (parallel, ~2h):** T1 markets, T2 news, T6 dashboard against mock data, T7 eval set.
**P2 Brain (~2h):** T3 prefilter + Jev judge + alert rules, tuned on T7 eval set.
**P3 Surface (~2h):** T4 API + SSE + tracker, T5 Telegram bot, T6 wired to live API.
**P4 Proof (runs over days):** leave it running, track record accrues; analyse with PromptQL ($150 credit) once alerts ≥ 100.

## Jev design (T3)
Per (headline, market) pair, one call, state = `{headline:{title, body, source, published_at}, market:{question, rules}}`:
- `relevant` Noul: does the headline report on the specific event/condition described in `market.rules`?
- `effect` Choice: `resolves_yes` / `resolves_no` / `raises_yes` / `lowers_yes` / `no_effect`
- `strength` Score: rumour or opinion → credible report → officially confirmed
Code (not Jev) handles: market end dates vs headline dates, price comparisons, dedupe windows.
Alert rule v0: relevant ≥ 0.7 AND effect ≠ no_effect with confidence ≥ 0.6 AND strength ≥ 1.5 → alert.
`stale_price` if effect says YES-ward and yes_price < 0.85 (or NO-ward and yes_price > 0.15).
Rate limit 1,200 req/min, so ≤ 40 pairs per headline, and bounded concurrency (asyncio semaphore 20).

## Risks
- News latency: we won't beat pro feeds. We compete on rules-accuracy + long-tail coverage + receipts.
- Prompt injection in headlines: the state is untrusted. Never let judgment text drive actions beyond alerts.
- Not trading advice. Alerts are information. No automated trading.
