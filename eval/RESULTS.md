# Judge eval (100 pairs, labels by Codex, run by Claude) — 2026-09-29

| Version | rel>=0.7 precision | recall | effect direction | strength ±0.5 |
|---|---|---|---|---|
| v1 "reports on the deciding condition" | 1.00 | 0.55 | 40/40 | 8/40 |
| v2 "would a trader of this market want to know" + settled/developing/speculation levels | **1.00** | **0.93** | 39/40 | 29/40 |

v1 missed indirect news (QB injury vs playoff market). Cost per 100 judgments ~$0.004, mean latency 127 ms.
Alert thresholds after v2: relevant >= 0.7, effect conf >= 0.6, strength >= 1.0, same_period >= 0.5.
Caveat: 100 synthetic pairs written by one model; real-feed precision must be checked from the alert log + feedback.
