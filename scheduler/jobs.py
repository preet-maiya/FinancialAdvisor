import asyncio
import logging

from agent.analyzer import daily_digest, anomaly_check, weekly_report, monthly_review
from data.fetcher import get_transactions
from notifications.telegram import send_digest, send_alert
import storage.repository as repo

logger = logging.getLogger(__name__)


def _run(coro):
    """Run an async coroutine from a sync scheduler job."""
    asyncio.get_event_loop().run_until_complete(coro)


def job_daily_digest():
    async def _inner():
        try:
            logger.info("Running daily digest job...")
            result = await daily_digest()
            await send_digest(result)
        except Exception as e:
            logger.error(f"daily_digest job failed: {e}")
    _run(_inner())


def job_anomaly_check():
    async def _inner():
        try:
            logger.info("Running anomaly check job...")
            result = await anomaly_check()
            if result.alerts:
                urgency = "critical" if any("🚨" in a for a in result.alerts) else "warning"
                await send_alert("Anomaly Detected", result.raw_response, urgency=urgency)
            else:
                logger.info("No anomalies found.")
        except Exception as e:
            logger.error(f"anomaly_check job failed: {e}")
    _run(_inner())


def job_weekly_report():
    async def _inner():
        try:
            logger.info("Running weekly report job...")
            result = await weekly_report()
            await send_digest(result)
        except Exception as e:
            logger.error(f"weekly_report job failed: {e}")
    _run(_inner())


def job_monthly_review():
    async def _inner():
        try:
            logger.info("Running monthly review job...")
            result = await monthly_review()
            await send_digest(result)
        except Exception as e:
            logger.error(f"monthly_review job failed: {e}")
    _run(_inner())


def job_sync_transactions():
    async def _inner():
        try:
            logger.info("Syncing transactions from Monarch Money...")
            transactions = await get_transactions(days=30)
            count = await repo.upsert_transactions(transactions)
            await repo.compute_and_update_baselines()
            logger.info(f"Sync complete: {count} transactions upserted.")
        except Exception as e:
            logger.error(f"sync_transactions job failed: {e}")
    _run(_inner())
