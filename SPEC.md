---
slug: jev-market-radar
stage: prototype
builders: [claude, codex]
created: 2026-09-29
---

# Prototype: jev-market-radar

**The one job it must do:** Given a breaking-news headline, compare it with the resolution rules of active prediction markets and surface the markets whose odds the headline may change. The planned pipeline and demo target are documented in `05_prototypes/jev-market-radar/PLAN.md` (retrieved 2026-09-29).

**First user (named):** Shubham, using the private local demo to evaluate a pasted headline. This is the prototype operator named in the workspace instructions; actual user demand is unvalidated (`02_ideas/_raw/2026-09-29_idea_jev-market-radar_claude.md`, retrieved 2026-09-29).

**Deliberately NOT building:** Automated trading, trading advice, public deployment, payment handling, or a public Telegram channel. Those exclusions follow the prototype plan and the human-approval rule in `AGENTS.md` (retrieved 2026-09-29).

## Flow (max 5 steps from landing to value)

1. Shubham opens the local dashboard.
2. He pastes a breaking-news headline or watches headlines arrive from configured news sources.
3. The system prefilters active prediction markets and judges the headline against their resolution rules.
4. The dashboard ranks affected markets and shows direction, confidence, strength, current price, and alert status.
5. Shubham marks useful or bad alerts; the tracker records subsequent prices for an inspectable hit-rate history.

The flow and data fields come from `05_prototypes/jev-market-radar/PLAN.md` and `05_prototypes/jev-market-radar/radar/schema.sql` (retrieved 2026-09-29).

## Stack (boring and fast)

Python 3.11+, SQLite in WAL mode, `httpx`, `feedparser`, `websockets`, FastAPI with Server-Sent Events, and plain HTML/JavaScript. This is the committed stack in `05_prototypes/jev-market-radar/PLAN.md` and `05_prototypes/jev-market-radar/requirements.txt` (retrieved 2026-09-29).

## Done when

- [ ] Shubham completes the paste-headline flow locally using a real current headline.
- [ ] At least one returned market is manually checked against its displayed resolution rules.
- [ ] We capture Shubham's reaction as a quote.
- [ ] We record time-to-results for the completed flow.
- [ ] We record whether the resulting alert's market price moved in the predicted direction within 10 minutes, the demo's defined hit-rate window (`05_prototypes/jev-market-radar/PLAN.md`, retrieved 2026-09-29).

Until these boxes are checked, Gate 4 has not passed (`AGENTS.md`, retrieved 2026-09-29).
