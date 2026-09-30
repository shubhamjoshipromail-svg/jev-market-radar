"""Build T7's independent draft eval set from exact market rows.

Market questions and rules come from ../data/radar.db (retrieved 2026-09-29).
Headlines and labels are synthetic evaluation fixtures authored by Codex.
Each market contributes four relevant examples, three same-topic near-misses,
and three unrelated examples, in that order.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "radar.db"
OUTPUT_PATH = Path(__file__).with_name("pairs_draft.jsonl")

MARKET_IDS = [
    "polymarket:559698",
    "polymarket:2362934",
    "polymarket:2696770",
    "polymarket:4570491",
    "polymarket:1163699",
    "polymarket:1090033",
    "polymarket:4061171",
    "polymarket:1447978",
    "polymarket:948957",
    "polymarket:568117",
]

# Tuple shape: headline, expected_relevant, expected_effect, expected_strength.
# Strength rubric: 0 rumor/opinion, 1 credible report, 2 official confirmation.
CASES: dict[str, list[tuple[str, bool, str, int]]] = {
    "polymarket:559698": [
        ("Democratic convention officially nominates Hakeem Jeffries, who accepts the 2028 presidential nomination", True, "resolves_yes", 2),
        ("Democratic Party officially nominates Gretchen Whitmer for president in 2028", True, "resolves_no", 2),
        ("AP: Jeffries secures a decisive majority of pledged delegates ahead of Democratic convention", True, "raises_yes", 1),
        ("Major outlets report Hakeem Jeffries has ended his 2028 presidential campaign", True, "lowers_yes", 1),
        ("House Democrats re-elect Hakeem Jeffries as caucus leader", False, "no_effect", 2),
        ("Hakeem Jeffries accepts invitation to keynote the 2028 Democratic convention", False, "no_effect", 1),
        ("Hakeem Jeffries accepts Democratic nomination for his New York House seat", False, "no_effect", 2),
        ("NASA confirms successful landing of lunar sample-return capsule", False, "no_effect", 2),
        ("Reuters: Global wheat harvest forecast rises after favorable weather", False, "no_effect", 1),
        ("Rumor claims a major game studio will delay its fantasy sequel", False, "no_effect", 0),
    ],
    "polymarket:2362934": [
        ("Ritual officially launches its transferable token, now trading publicly on exchanges", True, "resolves_yes", 2),
        ("September 30 deadline passes with no publicly tradable Ritual token", True, "resolves_no", 2),
        ("CoinDesk: Exchanges prepare to open spot trading for Ritual's official token", True, "raises_yes", 1),
        ("The Block: Ritual delays its tradable token launch until October 2026", True, "lowers_yes", 1),
        ("Ritual announces a future token but says trading is not yet available", False, "no_effect", 2),
        ("Ritual launches a dollar-pegged stablecoin", False, "no_effect", 2),
        ("Ritual releases non-transferable testnet reward points", False, "no_effect", 1),
        ("European Central Bank leaves its benchmark interest rate unchanged", False, "no_effect", 2),
        ("BBC reports record attendance at international film festival", False, "no_effect", 1),
        ("Online rumor says a pop star may announce a stadium tour", False, "no_effect", 0),
    ],
    "polymarket:2696770": [
        ("KAST officially launches its token with public transfers and live exchange trading", True, "resolves_yes", 2),
        ("KAST token deadline expires without an actively tradable official token", True, "resolves_no", 2),
        ("Bloomberg: KAST token deposits open as exchanges prepare public spot markets", True, "raises_yes", 1),
        ("KAST team postpones public token trading until after September 30, 2026", True, "lowers_yes", 2),
        ("KAST publishes tokenomics and promises a later launch", False, "no_effect", 2),
        ("KAST introduces a synthetic dollar product", False, "no_effect", 2),
        ("KAST distributes locked, non-transferable loyalty points", False, "no_effect", 1),
        ("US Geological Survey records a minor earthquake offshore", False, "no_effect", 2),
        ("Financial Times: Airline passenger demand reaches a quarterly record", False, "no_effect", 1),
        ("Forum rumor claims a phone maker is testing a foldable tablet", False, "no_effect", 0),
    ],
    "polymarket:4570491": [
        ("White House publishes Donald Trump's signed presidential pardon for an individual", True, "resolves_yes", 2),
        ("September 30 ends with no Trump pardon, commutation, or reprieve issued during the market window", True, "resolves_no", 2),
        ("Reuters: Trump has signed a pardon that is awaiting formal publication", True, "raises_yes", 1),
        ("White House says Trump has ruled out clemency actions before September 30", True, "lowers_yes", 2),
        ("State governor grants a prisoner a pardon", False, "no_effect", 2),
        ("Justice Department dismisses charges against a defendant", False, "no_effect", 2),
        ("Trump endorses legislation to reform the federal pardon process", False, "no_effect", 1),
        ("National Weather Service issues a hurricane watch for the Gulf Coast", False, "no_effect", 2),
        ("ESPN reports veteran goalkeeper will retire after the season", False, "no_effect", 1),
        ("Anonymous post claims a streaming service will raise prices", False, "no_effect", 0),
    ],
    "polymarket:1163699": [
        ("President signs H.R.3633 after both chambers pass the Digital Asset Market Clarity Act", True, "resolves_yes", 2),
        ("Congress.gov shows H.R.3633 unsigned as the December 31, 2026 deadline expires", True, "resolves_no", 2),
        ("Senate passes H.R.3633 after House approval, sending the Clarity Act to the president", True, "raises_yes", 2),
        ("White House announces the president will veto H.R.3633", True, "lowers_yes", 2),
        ("House committee schedules another hearing on H.R.3633", False, "no_effect", 2),
        ("California governor signs a state bill also called the Digital Asset Clarity Act", False, "no_effect", 2),
        ("President signs an executive order on digital-asset market structure", False, "no_effect", 2),
        ("World Health Organization certifies elimination of a tropical disease in one country", False, "no_effect", 2),
        ("Associated Press: Museum unveils newly restored Renaissance painting", False, "no_effect", 1),
        ("Podcast host predicts a surprise album release this weekend", False, "no_effect", 0),
    ],
    "polymarket:1090033": [
        ("UFC lists Leon Edwards as official welterweight champion at noon ET on December 31, 2026", True, "resolves_yes", 2),
        ("UFC lists Shavkat Rakhmonov, not Leon Edwards, as welterweight champion at the market check time", True, "resolves_no", 2),
        ("Leon Edwards wins the undisputed UFC welterweight title before the December 31 check", True, "raises_yes", 2),
        ("Leon Edwards withdraws from welterweight title fight with a serious injury", True, "lowers_yes", 1),
        ("Leon Edwards is named interim UFC welterweight champion", False, "no_effect", 2),
        ("UFC rankings place Leon Edwards first among welterweight contenders", False, "no_effect", 2),
        ("Leon Edwards wins a non-title welterweight bout", False, "no_effect", 1),
        ("Federal Reserve publishes minutes from its latest policy meeting", False, "no_effect", 2),
        ("Reuters: Automaker opens a new electric-vehicle battery plant", False, "no_effect", 1),
        ("Fan account claims a science-fiction series may get a reboot", False, "no_effect", 0),
    ],
    "polymarket:4061171": [
        ("College Football Playoff officially selects Oklahoma State for the 2026 playoff field", True, "resolves_yes", 2),
        ("Final College Football Playoff bracket excludes Oklahoma State", True, "resolves_no", 2),
        ("Oklahoma State wins the Big 12 championship, strengthening its playoff case", True, "raises_yes", 1),
        ("Oklahoma State starting quarterback ruled out for the season after injury", True, "lowers_yes", 1),
        ("Oklahoma State accepts an invitation to a non-playoff bowl game", False, "no_effect", 2),
        ("Oklahoma State basketball team qualifies for the NCAA tournament", False, "no_effect", 2),
        ("Oklahoma State esports squad selected for a collegiate playoff", False, "no_effect", 1),
        ("Bank of Japan announces an adjustment to bond purchases", False, "no_effect", 2),
        ("BBC reports archaeologists found a Roman-era mosaic", False, "no_effect", 1),
        ("Message-board rumor says a console maker will reveal new hardware", False, "no_effect", 0),
    ],
    "polymarket:1447978": [
        ("NFL officially declares Minnesota Vikings the 2026 NFC North champions", True, "resolves_yes", 2),
        ("NFL officially declares Chicago Bears the 2026 NFC North champions", True, "resolves_no", 2),
        ("Vikings open a three-game NFC North lead with four games remaining", True, "raises_yes", 1),
        ("Vikings lose starting quarterback for the rest of the regular season", True, "lowers_yes", 1),
        ("Minnesota Vikings clinch an NFC wild-card berth", False, "no_effect", 2),
        ("Minnesota Vikings win the NFC Championship Game", False, "no_effect", 2),
        ("Minnesota women's flag-football team wins its regional division", False, "no_effect", 1),
        ("European Space Agency confirms launch date for a climate satellite", False, "no_effect", 2),
        ("Wall Street Journal: Coffee prices retreat after larger crop estimate", False, "no_effect", 1),
        ("Social-media rumor says a famous chef plans a new restaurant", False, "no_effect", 0),
    ],
    "polymarket:948957": [
        ("Binance BTC/USDT one-minute candle posts a final high above every previous Binance high", True, "resolves_yes", 2),
        ("September 30, 2026 ends without any qualifying Binance BTC/USDT one-minute all-time high", True, "resolves_no", 2),
        ("CoinDesk: Binance BTC/USDT trades within one percent of its prior one-minute-candle record", True, "raises_yes", 1),
        ("Bloomberg: Bitcoin tumbles sharply on Binance, moving far below its record high", True, "lowers_yes", 1),
        ("Coinbase Bitcoin price reaches an all-time high while Binance remains below its record", False, "no_effect", 1),
        ("Binance BTC/USDT sets a record daily close but its one-minute high stays below the prior high", False, "no_effect", 2),
        ("Ethereum reaches an all-time high on Binance", False, "no_effect", 1),
        ("Supreme Court releases its opinion in a maritime dispute", False, "no_effect", 2),
        ("Reuters: Pharmaceutical company reports positive vaccine trial results", False, "no_effect", 1),
        ("Trader rumor predicts copper will rally next month", False, "no_effect", 0),
    ],
    "polymarket:568117": [
        ("Donald Trump officially announces he will resign the presidency before December 31, 2026", True, "resolves_yes", 2),
        ("Congress removes Trump from office before he announces a resignation", True, "resolves_no", 2),
        ("Reuters: Trump aides have drafted a presidential resignation announcement", True, "raises_yes", 1),
        ("White House says Trump will not resign and will serve through the deadline", True, "lowers_yes", 2),
        ("Trump temporarily transfers presidential powers during a medical procedure", False, "no_effect", 2),
        ("Donald Trump resigns from an executive role at the Trump Organization", False, "no_effect", 2),
        ("Senior Trump campaign adviser announces resignation", False, "no_effect", 1),
        ("UNESCO adds an ancient irrigation system to the World Heritage List", False, "no_effect", 2),
        ("Associated Press: Cargo ship traffic resumes after port repairs", False, "no_effect", 1),
        ("Anonymous rumor says a television host may leave a talent show", False, "no_effect", 0),
    ],
}


def main() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            f"SELECT id, question, rules FROM markets WHERE id IN ({','.join('?' for _ in MARKET_IDS)})",
            MARKET_IDS,
        ).fetchall()

    markets = {market_id: (question, rules) for market_id, question, rules in rows}
    missing = set(MARKET_IDS) - set(markets)
    if missing:
        raise RuntimeError(f"Missing source markets: {sorted(missing)}")

    output_rows: list[dict[str, object]] = []
    for market_id in MARKET_IDS:
        cases = CASES[market_id]
        if len(cases) != 10:
            raise RuntimeError(f"{market_id} has {len(cases)} cases, expected 10")
        question, rules = markets[market_id]
        for headline, relevant, effect, strength in cases:
            output_rows.append(
                {
                    "headline": headline,
                    "market_question": question,
                    "market_rules": rules,
                    "expected_relevant": relevant,
                    "expected_effect": effect,
                    "expected_strength": strength,
                }
            )

    relevant_count = sum(bool(row["expected_relevant"]) for row in output_rows)
    if len(output_rows) != 100 or relevant_count != 40:
        raise RuntimeError(
            f"Expected 100 rows and 40 relevant; got {len(output_rows)} and {relevant_count}"
        )

    OUTPUT_PATH.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output_rows),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
