from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS symbols (
    symbol TEXT PRIMARY KEY,
    base_asset TEXT NOT NULL,
    chain TEXT,
    token_contract TEXT,
    coingecko_id TEXT,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    volume_5m REAL NOT NULL,
    oi REAL NOT NULL,
    funding_rate REAL NOT NULL,
    whale_long_short_ratio REAL,
    market_cap REAL,
    oi_mcap_ratio REAL,
    funding_interval_hours INTEGER DEFAULT 8
);
CREATE INDEX IF NOT EXISTS idx_metrics_symbol_ts ON metrics(symbol, ts);

CREATE TABLE IF NOT EXISTS unlocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    unlock_ts INTEGER NOT NULL,
    amount REAL NOT NULL,
    pct_circulating REAL,
    source TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_unlocks_symbol_ts ON unlocks(symbol, unlock_ts);

CREATE TABLE IF NOT EXISTS onchain_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    chain TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_address TEXT NOT NULL,
    to_address TEXT NOT NULL,
    amount REAL NOT NULL,
    amount_usd REAL,
    tx_hash TEXT UNIQUE,
    label TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_onchain_symbol_ts ON onchain_events(symbol, ts);

CREATE TABLE IF NOT EXISTS anomaly_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    anomaly_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    change_15m REAL NOT NULL,
    metrics_json TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    narrative TEXT NOT NULL,
    onchain_refs_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_anomaly_symbol_ts ON anomaly_events(symbol, detected_ts);
CREATE INDEX IF NOT EXISTS idx_anomaly_detected_ts ON anomaly_events(detected_ts);

CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    anomaly_type TEXT NOT NULL,
    sent_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_log_lookup ON alert_log(symbol, anomaly_type, sent_ts);

CREATE TABLE IF NOT EXISTS chain_scan_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token_metadata (
    base_asset TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    coingecko_id TEXT,
    chain TEXT,
    platform TEXT,
    token_contract TEXT,
    name TEXT,
    market_cap_rank INTEGER,
    resolved_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_token_metadata_symbol ON token_metadata(symbol);
CREATE INDEX IF NOT EXISTS idx_token_metadata_contract ON token_metadata(token_contract);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(metrics)")}
    if "funding_interval_hours" not in cols:
        conn.execute(
            "ALTER TABLE metrics ADD COLUMN funding_interval_hours INTEGER DEFAULT 8"
        )
