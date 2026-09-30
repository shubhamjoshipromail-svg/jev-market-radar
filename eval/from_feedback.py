"""Turn your Useful/Wrong votes and the tracker's price outcomes into new eval pairs.

python -m eval.from_feedback  -> eval/pairs_feedback.jsonl (review before merging into pairs_draft.jsonl)
Wrong-voted alerts become near-miss examples; useful ones become positive examples.
"""
import json
from pathlib import Path

from radar.db import connect

OUT = Path(__file__).with_name("pairs_feedback.jsonl")

rows = connect().execute(
    "SELECT h.title AS headline, m.question AS market_question, m.rules AS market_rules, j.effect, j.strength,"
    " f.vote, a.direction, a.price_at_alert, p.price AS price_10m FROM feedback f"
    " JOIN alerts a ON a.id=f.alert_id JOIN judgments j ON j.id=a.judgment_id"
    " JOIN headlines h ON h.id=a.headline_id JOIN markets m ON m.id=a.market_id"
    " LEFT JOIN price_checks p ON p.alert_id=a.id AND p.offset_min=10 GROUP BY f.alert_id").fetchall()
out = []
for r in rows:
    r = dict(r)
    ok = r.pop("vote") > 0
    out.append({**{k: r[k] for k in ("headline", "market_question", "market_rules")},
                "expected_relevant": ok, "expected_effect": r["effect"] if ok else "no_effect",
                "expected_strength": round(r["strength"]), "source": "feedback",
                "price_moved_as_predicted": None if r["price_10m"] is None
                else (r["price_10m"] - r["price_at_alert"]) * r["direction"] > 0})
OUT.write_text("\n".join(json.dumps(o) for o in out))
print(f"{len(out)} pairs -> {OUT}")
