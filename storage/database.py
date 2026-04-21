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
"""


async def init_db() -> None:
    db_dir = os.path.dirname(config.DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()
    logger.info(f"Database initialized at {config.DB_PATH}")


@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db
