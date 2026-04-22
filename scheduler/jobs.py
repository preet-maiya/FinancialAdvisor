import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from agent.analyzer import daily_digest, anomaly_check, weekly_report, monthly_review
from data.fetcher import get_transactions
from notifications.telegram import send_digest, send_alert
import storage.repository as repo
import job_state

logger = logging.getLogger(__name__)

JOB_NAMES = {
    "daily_digest": "Daily Digest",
    "anomaly_check": "Anomaly Check",
    "weekly_report": "Weekly Report",
    "monthly_review": "Monthly Review",
    "sync_transactions": "Sync Transactions",
}


def _run(coro):
    """Run an async coroutine from a sync scheduler job (background-thread safe)."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


def _run_job(job_id: str, coro_factory: Callable[[], Awaitable]) -> None:
    """Generic wrapper: tracks running state and persists the run record."""
    job_name = JOB_NAMES.get(job_id, job_id)
    started_at = datetime.now(timezone.utc)
    job_state.mark_started(job_id)

    async def _inner():
        status = "success"
        error = None
        try:
            logger.info("Running %s...", job_name)
            await coro_factory()
        except Exception as e:
            status = "error"
            error = str(e)
            logger.error("%s failed: %s", job_name, e)
        finally:
            finished_at = datetime.now(timezone.utc)
            duration = (finished_at - started_at).total_seconds()
            await repo.insert_job_run(
                job_id=job_id,
                job_name=job_name,
                started_at=started_at.isoformat(),
                finished_at=finished_at.isoformat(),
                duration_seconds=duration,
                status=status,
                error=error,
            )

    try:
        _run(_inner())
    finally:
        job_state.mark_done(job_id)


def job_daily_digest():
    async def _work():
        result = await daily_digest()
        await send_digest(result)
    _run_job("daily_digest", _work)


def job_anomaly_check():
    async def _work():
        result = await anomaly_check()
        if result.alerts:
            urgency = "critical" if any("🚨" in a for a in result.alerts) else "warning"
            await send_alert("Anomaly Detected", result.raw_response, urgency=urgency)
        else:
            logger.info("No anomalies found.")
    _run_job("anomaly_check", _work)


def job_weekly_report():
    async def _work():
        result = await weekly_report()
        await send_digest(result)
    _run_job("weekly_report", _work)


def job_monthly_review():
    async def _work():
        result = await monthly_review()
        await send_digest(result)
    _run_job("monthly_review", _work)


def job_sync_transactions():
    async def _work():
        transactions = await get_transactions(days=30)
        count = await repo.upsert_transactions(transactions)
        await repo.compute_and_update_baselines()
        logger.info("Sync complete: %d transactions upserted.", count)
    _run_job("sync_transactions", _work)
