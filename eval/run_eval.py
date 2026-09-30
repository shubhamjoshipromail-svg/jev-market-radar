"""Score the judge against eval/pairs_draft.jsonl (labels by Codex). python -m eval.run_eval"""
import asyncio
import json
from collections import Counter
from pathlib import Path

from radar import jev
from radar.alerts import DIRECTION
from radar.judge import judge_pair

PAIRS = Path(__file__).with_name("pairs_draft.jsonl")
OUT = Path(__file__).with_name("results.jsonl")


async def main():
    rows = [json.loads(l) for l in PAIRS.read_text().splitlines() if l.strip()]
    sem = asyncio.Semaphore(20)
    async with jev.client() as http:
        async def one(r):
            async with sem:
                j = await judge_pair(http, {"title": r["headline"], "source": "eval"},
                                     {"question": r["market_question"], "rules": r["market_rules"]})
                return {**r, **j}
        res = await asyncio.gather(*(one(r) for r in rows))
    OUT.write_text("\n".join(json.dumps(r) for r in res))

    print(f"n={len(res)}  mean latency {sum(r['latency_ms'] for r in res)/len(res):.0f}ms  cost ${sum(r['cost_usd'] for r in res):.5f}")
    for t in (0.5, 0.6, 0.7, 0.8):
        tp = sum(r["relevant"] >= t and r["expected_relevant"] for r in res)
        fp = sum(r["relevant"] >= t and not r["expected_relevant"] for r in res)
        fn = sum(r["relevant"] < t and r["expected_relevant"] for r in res)
        print(f"relevant>={t}: precision {tp/max(tp+fp,1):.2f} recall {tp/max(tp+fn,1):.2f}  (fp {fp}, fn {fn})")
    rel = [r for r in res if r["expected_relevant"]]
    exact = sum(r["effect"] == r["expected_effect"] for r in rel)
    sign = lambda e: DIRECTION.get(e, 0)
    dir_ok = sum(sign(r["effect"]) == sign(r["expected_effect"]) for r in rel)
    print(f"effect on relevant pairs: exact {exact}/{len(rel)}, direction {dir_ok}/{len(rel)}")
    print("confusion (expected -> got):", Counter((r["expected_effect"], r["effect"]) for r in rel if r["effect"] != r["expected_effect"]).most_common(6))
    s = [(r["expected_strength"], round(r["strength"])) for r in res if r["expected_relevant"]]
    print(f"strength within 0.5: {sum(abs(a - b) < 1 for a, b in s)}/{len(s)}")
    nm = [r for r in res if not r["expected_relevant"] and r.get("category", "") != "unrelated"]
    worst = sorted((r for r in res if not r["expected_relevant"]), key=lambda r: -r["relevant"])[:5]
    print("worst false positives:")
    for r in worst:
        print(f"  {r['relevant']:.2f} {r['effect']:<12} | {r['headline'][:70]} || {r['market_question'][:60]}")


if __name__ == "__main__":
    asyncio.run(main())
