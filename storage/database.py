import logging
import os
from contextlib import asynccontextmanager

import aiosqlite

import config

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    merchant TEXT NOT NULL,
    amount REAL NOT NULL,
    category TEXT NOT NULL,
    account TEXT NOT NULL,
    is_income INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    synced_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    type TEXT NOT NULL,
    summary TEXT NOT NULL,
    alerts TEXT NOT NULL DEFAULT '[]',
    score REAL,
    raw_response TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spending_baselines (
    category TEXT PRIMARY KEY,
    monthly_avg REAL NOT NULL,
    sample_months INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS detected_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT NOT NULL,
    merchant TEXT,
    category TEXT,
    description TEXT NOT NULL,
    amount REAL,
    detected_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_transactions_merchant ON transactions(merchant);
CREATE INDEX IF NOT EXISTS idx_alerts_sent_key ON alerts_sent(alert_key);

CREATE TABLE IF NOT EXISTS job_schedule_overrides (
    id TEXT PRIMARY KEY,
    minute TEXT, hour TEXT, day TEXT, month TEXT, day_of_week TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS prompt_overrides (
    job_id TEXT PRIMARY KEY,
    system_prompt TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    job_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_seconds REAL,
    status TEXT NOT NULL DEFAULT 'running',
    error TEXT,
    trace TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_runs_started_at ON job_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);

CREATE TABLE IF NOT EXISTS daily_investment_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    account TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    prev_close REAL NOT NULL,
    day_change REAL NOT NULL,
    day_change_pct REAL NOT NULL,
    position_value REAL NOT NULL,
    day_pnl REAL NOT NULL,
    snapshotted_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(date, symbol, account)
);

CREATE INDEX IF NOT EXISTS idx_investment_snapshots_date ON daily_investment_snapshots(date DESC);
"""


MIGRATIONS = [
    "ALTER TABLE job_runs ADD COLUMN trace TEXT",
    "ALTER TABLE analysis_results ADD COLUMN model TEXT",
    "ALTER TABLE analysis_results ADD COLUMN prompt_tokens INTEGER",
    "ALTER TABLE analysis_results ADD COLUMN completion_tokens INTEGER",
    "ALTER TABLE analysis_results ADD COLUMN tokens_per_sec REAL",
    "ALTER TABLE analysis_results ADD COLUMN latency_seconds REAL",
]


async def init_db() -> None:
    db_dir = os.path.dirname(config.DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executescript(SCHEMA)
        for migration in MIGRATIONS:
            try:
                await db.execute(migration)
            except Exception:
                pass  # column already exists
        await db.commit()
    logger.info(f"Database initialized at {config.DB_PATH}")


@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db
