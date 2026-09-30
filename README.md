# jev-market-radar
Headlines judged by Jev against every live prediction market's resolution rules. See PLAN.md and COORD.md.

    python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
    python -m radar.db            # creates data/radar.db
Keys go in the repo-root `.env` (TYPESAFE_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID).

## Deploy (Railway)
Railway runs `scripts/run_all.sh` (railway.json). Set variables:
`TYPESAFE_API_KEY`, `HOST=0.0.0.0`, `PUBLIC_MODE=1`, `RADAR_DB=/data/radar.db`, `RADAR_BACKFILL_ON_START=40`,
and attach a volume at `/data` so the database survives redeploys. Leave `NTFY_TOPIC` unset so only your Mac pushes to your phone.
Public mode caps pasted headlines (6 per IP per 10 min, 300 per day) because each one spends Jev credit.
