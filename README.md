# jev-market-radar
Headlines judged by Jev against every live prediction market's resolution rules. See PLAN.md and COORD.md.

    python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
    python -m radar.db            # creates data/radar.db
Keys go in the repo-root `.env` (TYPESAFE_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID).
