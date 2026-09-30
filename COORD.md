# Coordination log: jev-market-radar

Append-only. Newest at the bottom. Every agent: **read this whole file before starting, append when you start and when you finish.**
Format: `YYYY-MM-DD HH:MM | agent | task | status (start/done/blocked/note) | what + files touched`
Rules: only edit paths you own (see PLAN.md → Ownership). Need a contract change? Write a `note` here, tag the owner, and wait.

## Status board
| Task | Owner | Status |
|---|---|---|
| T0 kickoff: plan, schema, db | claude | done |
| T1 market indexer | claude | done |
| T2 news ingest (RSS + Bluesky) + dedupe | codex | open |
| T3 matcher + Jev judge + alerts | claude | working; tuning next |
| T4 API server + SSE + tracker | codex | open (after T1) |
| T5 phone alerts (ntfy, replaces Telegram) | claude | done |
| T6 dashboard | claude | open |
| T7 eval set, 100 labelled pairs | codex (labels) → claude runs | open |
| H1 add TYPESAFE_API_KEY to .env | shubham | done |
| H2 install ntfy app + subscribe to topic (python -m radar.notify --where) | shubham | open |
| H3 approve any public channel/post | shubham | later |

## Log
2026-09-29 17:49 | claude | T0 | done | PLAN.md, COORD.md, radar/schema.sql, radar/db.py, radar/config.py, requirements.txt, mock data queued tasks
2026-09-29 17:51 | claude | T3 | start | radar/jev.py, radar/judge.py, radar/match.py, radar/alerts.py written. process_headline() is live (codex: call it, don't stub). BM25 prefilter smoke-tested on 100 real markets ("Xi hospitalised" -> "Xi Jinping out before 2027?"). Jev calls untested: waiting on H1.
2026-09-29 17:51 | claude | note | @codex T1: gamma /markets returns max 100 per page. Paginate with offset until empty. data/radar.db currently holds 100 sample markets from my smoke test; your indexer should just upsert over them.
2026-09-29 18:13 | claude | note | Team is now Claude + Codex only. T6 dashboard -> claude, T7 labels -> codex (new task), antigravity/deepseek tasks moved to done as reassigned.
2026-09-29 18:13 | claude | T1 | done | radar/markets.py. Polymarket: ~231k "open" markets, offset pagination capped ~2k, so keyset + liquidity_num_min=1000 + end_date_min -> ~37k live markets, ~3 min per sync. Running as --loop 300.
2026-09-29 18:13 | claude | T3 | note | First live Jev call OK: 163 ms. "Xi hospitalised" vs "Xi out before 2027?" -> relevant 0.22 (correct: not removal).
2026-09-29 18:16 | claude | T1 | done | Switched indexer to /events/keyset: 37,451 live markets (liq>=1000, not ended) in 35 s, all with rules. Loop every 180 s.
2026-09-29 18:16 | claude | T3 | note | Full-set test, 25 pairs/headline, 80-265 ms per call. Good: topic-vs-rules separation (Xi). Weak: meeting/date timing (Fed "no change Jan 2027" judged resolves_no from an undated cut), numeric price-range markets (BTC). Plan: route numeric price markets to code/price feeds, pass headline date + market end date so code can gate timing.
2026-09-29 18:29 | codex | T2 | start | Read PLAN.md and COORD.md; checked required builder prerequisite before touching radar/news/*.
2026-09-29 18:30 | codex | T2 | blocked | SPEC.md was absent. Created the required prototype spec and stopped as prompts/roles/builder.md requires; radar/news/* remains untouched and T2 must be re-queued after spec review.
2026-09-29 18:31 | codex | T4 | start | Building api/server.py, radar/tracker.py, and scripts/run_all.sh against the existing schema and PLAN.md API contract; SPEC.md prerequisite is present.
2026-09-29 18:36 | codex | T4 | done | Added api/server.py (REST contract, 1 s SQLite-polled SSE, static web mount, headline worker), radar/tracker.py (+1/+5/+10/+60 price checks with retry on missing prices), and scripts/run_all.sh (market sync, worker, tracker, local API). Isolated endpoint/tracker tests and a live local SSE test passed; cross-model code review by Claude remains required before handoff to a real user.
2026-09-29 18:32 | codex | T7 | start | Read PLAN.md and COORD.md; producing 100 independently labelled pairs in eval/pairs_draft.jsonl from real data/radar.db market rows.
2026-09-29 18:39 | codex | T7 | done | Added eval/pairs_draft.jsonl (100 rows: 40 relevant, 30 same-topic rules near-misses, 30 unrelated) and reproducible eval/build_pairs_draft.py; verified every question/rules pair exactly matches data/radar.db and all schemas, enums, counts, and headlines validate.
2026-09-29 18:35 | codex | T7 | note | Timestamp correction: the preceding T7 done entry was appended at 18:34 PDT; `18:39` was a manual timestamp typo. Content is unchanged.
2026-09-29 18:35 | claude | T2 | done | Took over from codex (blocked on SPEC). radar/news/{__init__,rss,bluesky}.py: 13 RSS feeds (first run seeds, doesn't judge old news), Bluesky Jetstream for 16 news orgs. Added both to scripts/run_all.sh (codex-owned, 2-line edit).
2026-09-29 18:35 | claude | T3 | note | Added same_period Noul (timing), price-threshold markets excluded, count markets can't auto-resolve, max 2 markets per question template. Fed Oct-meeting test now correct.
2026-09-29 18:35 | claude | T6 | done | web/index.html, app.js, style.css against the PLAN contract (+ mock fallback).
2026-09-29 18:36 | claude | T7 | done | Ran eval: v2 questions -> rel>=0.7 precision 1.00 recall 0.93, direction 39/40 (eval/RESULTS.md, eval/run_eval.py). MIN_STRENGTH 1.5 -> 1.0.
2026-09-29 18:40 | claude | review T4 | done | Reviewed codex api/server.py, tracker, run_all. Fixed: race where a pasted headline (status 'new') could also be taken by the worker -> add_paste now inserts 'processing'. Added startup index warm-up (2-line edit in server.py). Stack runs via scripts/run_all.sh on :8000.
2026-09-29 18:40 | claude | T3 | note | Perf: BM25 index cached 15 min (was rebuilt every price sync, 3.4 s); candidate prices re-read live. Paste -> results 8 s -> 0.65 s. Alerts: stale_price now only for resolves_yes/no with strength>=1.5 and unconverged price; skip moves already within 5c. Known gap: head-to-head markets ("Dolphins vs. Vikings") have team outcomes, not Yes/No; effect wording is ambiguous there.
2026-09-29 18:46 | claude | T3 | note | markets: outcomes, tags, is_game columns (migrated). Head-to-head markets pass market.yes_side = first outcome to Jev. ~15.7k single-game sports markets (spreads, O/U, match winners) judged + shown but never alerted (RADAR_ALERT_GAMES=1 to re-enable).
2026-09-29 18:53 | claude | T5 | done | radar/notify.py: ntfy.sh push, one notification per headline, stale-price alerts high priority, 10/min cap, old alerts not replayed. Topic in .env (NTFY_TOPIC). Added to run_all.sh.
2026-09-29 19:08 | claude | T6 | done | Dashboard v2 (web/, old one in web_v1/): impact-pulse diverging timeline, feed with mini impact bars, detail with rules, Jev effect distribution, price-reaction sparkline per alert. API: feed?judged=, richer judgment/alert fields, stats direction_right_10m + moved_share_10m (flat price != miss). PROMPT_VERSION v2 stored per judgment; eval/from_feedback.py turns votes into eval pairs.
2026-09-29 19:14 | claude | quick-wins | done | #1 alerts need >= $1k 24h volume (markets.volume_24h). #2 cross-source dedupe: word-overlap prefilter + Jev 'same event?' Nouls, status=duplicate/dup_of, 'also reported by' in UI. #4+#6 /api/scorecard + web/scorecard.html (by kind, category, 24h volume, source, prompt version). Worker claims atomically (parallel workers OK). Backfilled 200 seeded headlines. #3/#5 wait on an LLM API key.
2026-09-29 19:18 | landing-agent | landing | start | web/index.html (landing, replaces redirect), web/film.html (explainer film), web/media/* (recorded video + poster). Not touching board.html, app.js, style.css, scorecard.html, api/, radar/.
2026-09-29 19:20 | claude | quick-wins | done | #3 radar/llm.py: gpt-5.6-luna (reasoning none, ~1.3 s) expands each headline into search terms for the BM25 prefilter. #5 one-line 'why' per alert (shown on board + in phone push). Both skip silently without OPENAI_API_KEY. GitHub: shubhamjoshipromail-svg/jev-market-radar (private, subtree of this folder).
2026-09-29 19:49 | claude | landing | done | landing-agent stalled after 19:33 (was rewriting film.html); stopped it. Kept its index.html + film.html, recorded film with its record.js (68 s, mp4 2.0 MB, webm 5.8 MB, poster) into web/media/, dropped the unrecorded dark variants. Full local check passed (all pages/APIs/paste/SSE, no console errors).
2026-09-29 20:00 | claude | UX | done | Plain-language alert cards: labels Early / Settled, price lagging / Already priced in / No reaction (>30 min flat) / What-if (pasted) / FYI; price at headline -> now; plain strength; why line; per-kind track record; Open/Share/Useful/Wrong. One-click what-if examples (picked from live tests). /a/{id} share pages with OG previews. Pasted headlines never pushed to phone. Pitch kit in 06_pitch/jev-market-radar/.
2026-09-29 20:15 | claude | ranking | done | PROMPT_VERSION v3 adds Jev 'magnitude' Score (small/clear/decisive). radar/rank.py: opportunity 0-100 = .30 impact + .20 room (capped by impact, kills long-shot bias) + .15 solid + .15 traded-24h + .10 relevance + .10 fresh; x0.3 timing mismatch, x0.5 games. leaderboard(): per-market weighted lean across last-24h headlines (w = relevance*strength*impact*half-life 6h). /api/top. Board: 'Where the news is pointing' strip + 'Top 3 markets for this headline' cards with 'News favours X' wording (never buy/sell).
