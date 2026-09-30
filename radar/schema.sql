PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS markets (
  id            TEXT PRIMARY KEY,          -- "{venue}:{native_id}"
  venue         TEXT NOT NULL,             -- polymarket | kalshi
  slug          TEXT,
  url           TEXT,
  question      TEXT NOT NULL,
  rules         TEXT,                      -- full resolution text
  end_date      TEXT,                      -- ISO8601 UTC
  yes_price     REAL,                      -- 0..1
  volume        REAL,
  liquidity     REAL,
  outcomes      TEXT,                      -- JSON list; yes_price is the price of outcomes[0]
  tags          TEXT,                      -- JSON list of event tag slugs
  is_game       INTEGER NOT NULL DEFAULT 0, -- single-game sports market (moneyline/spread/total)
  active        INTEGER NOT NULL DEFAULT 1,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS headlines (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  source        TEXT NOT NULL,             -- e.g. rss:bbc, bluesky:@handle, paste
  url           TEXT,
  title         TEXT NOT NULL,
  body          TEXT,
  published_at  TEXT,
  fetched_at    TEXT NOT NULL,
  dedupe_key    TEXT UNIQUE,               -- normalized title hash; dupes are skipped
  status        TEXT NOT NULL DEFAULT 'new' -- new | processing | done | error
);

CREATE TABLE IF NOT EXISTS judgments (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  headline_id   INTEGER NOT NULL REFERENCES headlines(id),
  market_id     TEXT NOT NULL REFERENCES markets(id),
  prefilter_rank INTEGER,
  relevant      REAL,                      -- Noul P(yes)
  same_period   REAL,                      -- Noul: headline is about the market's meeting/window
  effect        TEXT,                      -- resolves_yes|resolves_no|raises_yes|lowers_yes|no_effect
  effect_probs  TEXT,                      -- JSON
  effect_conf   REAL,
  strength      REAL,                      -- 0..2
  strength_conf REAL,
  yes_price_at  REAL,                      -- market price when judged
  latency_ms    INTEGER,
  input_tokens  INTEGER,
  cost_usd      REAL,
  model         TEXT,
  created_at    TEXT NOT NULL,
  UNIQUE(headline_id, market_id)
);

CREATE TABLE IF NOT EXISTS alerts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  judgment_id   INTEGER NOT NULL REFERENCES judgments(id),
  headline_id   INTEGER NOT NULL,
  market_id     TEXT NOT NULL,
  kind          TEXT NOT NULL,             -- stale_price | mover
  direction     INTEGER NOT NULL,          -- +1 yes-ward, -1 no-ward
  price_at_alert REAL,
  created_at    TEXT NOT NULL,
  sent_telegram INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS price_checks (
  alert_id      INTEGER NOT NULL REFERENCES alerts(id),
  offset_min    INTEGER NOT NULL,          -- 1 | 5 | 10 | 60
  price         REAL,
  checked_at    TEXT NOT NULL,
  PRIMARY KEY (alert_id, offset_min)
);

CREATE TABLE IF NOT EXISTS feedback (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id      INTEGER NOT NULL REFERENCES alerts(id),
  vote          INTEGER NOT NULL,          -- 1 | -1
  created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_headlines_status ON headlines(status);
CREATE INDEX IF NOT EXISTS idx_judgments_headline ON judgments(headline_id);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);
