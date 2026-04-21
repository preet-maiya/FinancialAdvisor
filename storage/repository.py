import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from storage.database import get_db
from data.models import Transaction, AnalysisResult

logger = logging.getLogger(__name__)


async def upsert_transactions(transactions: list[Transaction]) -> int:
    if not transactions:
        return 0
    async with get_db() as db:
        count = 0
        for t in transactions:
            await db.execute(
                """
                INSERT INTO transactions (id, date, merchant, amount, category, account, is_income, notes, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    date=excluded.date,
                    merchant=excluded.merchant,
                    amount=excluded.amount,
                    category=excluded.category,
                    account=excluded.account,
                    is_income=excluded.is_income,
                    notes=excluded.notes,
                    synced_at=excluded.synced_at
                """,
                (
                    t.id, str(t.date), t.merchant, t.amount,
                    t.category, t.account, int(t.is_income), t.notes,
                ),
            )
            count += 1
        await db.commit()
    logger.info(f"Upserted {count} transactions.")
    return count


async def get_transactions(days: int = 30, income_only: bool = False, expense_only: bool = False) -> list[dict]:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    async with get_db() as db:
        query = "SELECT * FROM transactions WHERE date >= ?"
        params: list = [since]
        if income_only:
            query += " AND is_income = 1"
        if expense_only:
            query += " AND is_income = 0"
        query += " ORDER BY date DESC"
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_spending_by_category(days: int = 30) -> list[dict]:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    async with get_db() as db:
        async with db.execute(
            """
            SELECT category, SUM(amount) as total, COUNT(*) as count
            FROM transactions
            WHERE date >= ? AND is_income = 0
            GROUP BY category
            ORDER BY total DESC
            """,
            [since],
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_top_merchants(limit: int = 10, days: int = 30) -> list[dict]:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    async with get_db() as db:
        async with db.execute(
            """
            SELECT merchant, SUM(amount) as total, COUNT(*) as count
            FROM transactions
            WHERE date >= ? AND is_income = 0
            GROUP BY merchant
            ORDER BY total DESC
            LIMIT ?
            """,
            [since, limit],
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_baseline(category: str) -> Optional[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM spending_baselines WHERE category = ?", [category]
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def update_baseline(category: str, monthly_avg: float, sample_months: int) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO spending_baselines (category, monthly_avg, sample_months, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(category) DO UPDATE SET
                monthly_avg=excluded.monthly_avg,
                sample_months=excluded.sample_months,
                updated_at=excluded.updated_at
            """,
            [category, monthly_avg, sample_months],
        )
        await db.commit()


async def get_all_baselines() -> list[dict]:
    async with get_db() as db:
        async with db.execute("SELECT * FROM spending_baselines") as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def save_analysis_result(result: AnalysisResult) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO analysis_results (timestamp, type, summary, alerts, score, raw_response)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                result.timestamp.isoformat(),
                result.type,
                result.summary,
                json.dumps(result.alerts),
                result.score,
                result.raw_response,
            ),
        )
        await db.commit()


async def save_detected_pattern(pattern_type: str, description: str, merchant: str = None,
                                category: str = None, amount: float = None) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO detected_patterns (pattern_type, merchant, category, description, amount)
            VALUES (?, ?, ?, ?, ?)
            """,
            [pattern_type, merchant, category, description, amount],
        )
        await db.commit()


async def get_detected_patterns(days: int = 30) -> list[dict]:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM detected_patterns WHERE detected_at >= ? ORDER BY detected_at DESC",
            [since],
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def was_alert_sent(alert_key: str, hours: int = 24) -> bool:
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM alerts_sent WHERE alert_key = ? AND sent_at >= ?",
            [alert_key, since],
        ) as cursor:
            row = await cursor.fetchone()
    return row is not None


async def log_alert_sent(alert_key: str) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO alerts_sent (alert_key) VALUES (?)", [alert_key]
        )
        await db.commit()


async def get_merchant_history(merchant: str, days: int = 90) -> list[dict]:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    async with get_db() as db:
        async with db.execute(
            """
            SELECT * FROM transactions
            WHERE merchant = ? AND date >= ? AND is_income = 0
            ORDER BY date DESC
            """,
            [merchant, since],
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def compute_and_update_baselines() -> None:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT category,
                   SUM(amount) / COUNT(DISTINCT substr(date, 1, 7)) as monthly_avg,
                   COUNT(DISTINCT substr(date, 1, 7)) as sample_months
            FROM transactions
            WHERE is_income = 0 AND date >= date('now', '-6 months')
            GROUP BY category
            """
        ) as cursor:
            rows = await cursor.fetchall()

    for row in rows:
        await update_baseline(row["category"], row["monthly_avg"], row["sample_months"])
    logger.info(f"Updated baselines for {len(rows)} categories.")
