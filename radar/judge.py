"""The Jev questions asked for each (headline, market) pair. Judgment lives here; policy lives in alerts.py."""
import json

from radar import jev

# Bump on any change to QUESTIONS; stored per judgment so the alert log can be split by prompt version.
PROMPT_VERSION = "v3"  # v3: + magnitude

EFFECTS = ["resolves_yes", "resolves_no", "raises_yes", "lowers_yes", "no_effect"]

QUESTIONS = {
    "relevant": {
        "type": "noul",
        "instructions": (
            "Would a trader holding this market want to know about `headline`? True if the news changes the chance "
            "that `market.rules` resolves Yes, either directly (it meets or rules out the deciding condition) or "
            "indirectly (it changes the situation of the specific people, teams, companies, or events the rules "
            "depend on). False if it only shares a topic or a name without changing that chance."
        ),
        "criteria": {
            "true": "The news moves the odds of this specific market, directly or indirectly.",
            "false": "Unrelated, or same topic/name but no real bearing on how this market resolves.",
        },
    },
    "effect": {
        "type": "choice",
        "instructions": (
            "Assume `headline` is accurate. Reading `market.rules` literally, what does this news do to "
            "the chance that the market resolves to `market.yes_side`? 'yes' below always means `market.yes_side`."
        ),
        "criteria": {
            "resolves_yes": "The headline reports that the Yes condition in `market.rules` has now been met.",
            "resolves_no": "The headline reports that the Yes condition can no longer be met.",
            "raises_yes": "Makes Yes more likely but does not by itself meet the condition.",
            "lowers_yes": "Makes Yes less likely but does not by itself rule it out.",
            "no_effect": "No meaningful bearing on how this market resolves.",
        },
    },
    "same_period": {
        "type": "noul",
        "instructions": (
            "Does the event in `headline` fall within the time window, meeting, deadline, or edition that "
            "`market.rules` names? `headline.published_at` is when the news was published and "
            "`market.end_date` is when the market closes. Answer false if the headline is about a different "
            "meeting, month, season, or year than the one the market asks about."
        ),
        "criteria": {
            "true": "Same meeting / window / deadline as the market, or the market names no specific period.",
            "false": "The headline concerns a different meeting, date window, or edition.",
        },
    },
    "magnitude": {
        "type": "score",
        "instructions": (
            "Assume `headline` is accurate. How big a change does it make to the chance that the market resolves "
            "to `market.yes_side`, reading `market.rules` literally? Judge only the size of the change, not its "
            "direction: a big move toward NO is as large as a big move toward YES."
        ),
        "criteria": [
            "Small: background or one minor factor among many; the outcome is about as open as it was before.",
            "Clear: a meaningful development a trader would adjust for, but the outcome is still genuinely open.",
            "Decisive: the deciding condition in `market.rules` is now met, ruled out, or all but certain.",
        ],
    },
    "strength": {
        "type": "score",
        "instructions": "How settled is what `headline` reports? Judge the wording of the headline, not the topic.",
        "criteria": [
            "Speculation: a rumour, opinion, forecast, analysis, or unnamed-source claim about what might happen.",
            "Developing: something is reported as underway, planned, or likely, but not final or complete.",
            "Settled fact: it reports a completed, verifiable event as done (a result, vote, official decision, "
            "announcement, filing, withdrawal, injury ruling, or a price/level already reached).",
        ],
    },
}


def _yes_side(market: dict) -> str:
    """'Yes' for binary markets; for head-to-head markets ("Dolphins vs. Vikings") the first outcome."""
    try:
        first = json.loads(market.get("outcomes") or "[]")[0]
    except (ValueError, IndexError):
        return "Yes"
    return "Yes" if str(first).lower() == "yes" else f"{first} (the market's first outcome)"


def state_for(headline: dict, market: dict) -> dict:
    return {
        "headline": {
            "title": headline["title"],
            "body": (headline.get("body") or "")[:1500],
            "source": headline.get("source"),
            "published_at": (headline.get("published_at") or headline.get("fetched_at") or "")[:10],
        },
        "market": {
            "question": market["question"],
            "rules": (market.get("rules") or "")[:3000],
            "end_date": (market.get("end_date") or "")[:10],
            "yes_side": _yes_side(market),
        },
    }


async def judge_pair(http, headline: dict, market: dict) -> dict:
    res = await jev.ask(http, state_for(headline, market), QUESTIONS)
    a = res["answers"]
    return {
        "relevant": a["relevant"]["noul"],
        "same_period": a["same_period"]["noul"],
        "effect": a["effect"]["choice"],
        "effect_probs": json.dumps(a["effect"].get("probabilities", {})),
        "effect_conf": a["effect"].get("confidence"),
        "strength": a["strength"]["score"],
        "magnitude": a["magnitude"]["score"],
        "strength_conf": a["strength"].get("confidence"),
        "latency_ms": res["latency_ms"],
        "input_tokens": res["usage"].get("input_tokens"),
        "cost_usd": res["cost_usd"],
        "model": f'{res["model"]}+{PROMPT_VERSION}',
    }
