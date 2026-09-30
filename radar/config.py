"""Settings from env / repo-root .env. Import values from here, never read os.environ elsewhere."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# the IDEAS repo root when run locally; absent on a deploy (e.g. Railway's /app), where env vars are used instead
REPO = ROOT.parents[1] if len(ROOT.parents) > 1 else ROOT

def _load_env() -> None:
    for p in (ROOT / ".env", REPO / ".env"):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()

DB_PATH = Path(os.getenv("RADAR_DB", ROOT / "data" / "radar.db"))
TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY", "")
JEV_MODEL = os.getenv("JEV_MODEL", "jev-latest")
JEV_USD_PER_M_INPUT = float(os.getenv("JEV_USD_PER_M_INPUT", "0.042"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PREFILTER_K = int(os.getenv("RADAR_PREFILTER_K", "40"))
JEV_CONCURRENCY = int(os.getenv("RADAR_JEV_CONCURRENCY", "20"))
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
