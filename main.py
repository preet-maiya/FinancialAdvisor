import asyncio
import logging
import traceback

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import uvicorn

import config  # noqa: F401 — loads env and configures logging
from storage.database import init_db
from data.fetcher import get_transactions
import storage.repository as repo
from scheduler.jobs import (
    job_daily_digest,
    job_anomaly_check,
    job_weekly_report,
    job_monthly_review,
    job_investment_tracker,
    job_weekly_investment_tracker,
    job_snapshot_investments,
    job_sync_transactions,
)
import web.api as web_api

logger = logging.getLogger(__name__)

TZ = config.TZ


async def startup() -> None:
    logger.info("FinanceAdvisor starting up...")

    # 1. Init DB
    await init_db()

    # 2. Initial sync — last 90 days
    logger.info("Running initial sync (last 90 days)...")
    try:
        transactions = await get_transactions(days=90)
        count = await repo.upsert_transactions(transactions)
        await repo.compute_and_update_baselines()
        logger.info(f"Initial sync complete: {count} transactions.")
    except Exception as e:
        logger.error(f"Initial sync failed: {e}\n{traceback.format_exc()}")

    logger.info("Startup complete.")


def main():
    asyncio.run(startup())

    scheduler = BackgroundScheduler(timezone=TZ)

    # Daily digest at 07:00
    scheduler.add_job(
        job_daily_digest,
        CronTrigger(hour=7, minute=0, timezone=TZ),
        id="daily_digest",
        name="Daily Digest",
        replace_existing=True,
    )

    # Anomaly check every 4 hours
    scheduler.add_job(
        job_anomaly_check,
        CronTrigger(hour="*/4", minute=0, timezone=TZ),
        id="anomaly_check",
        name="Anomaly Check",
        replace_existing=True,
    )

    # Weekly report on Sundays at 19:00
    scheduler.add_job(
        job_weekly_report,
        CronTrigger(day_of_week="sun", hour=19, minute=0, timezone=TZ),
        id="weekly_report",
        name="Weekly Report",
        replace_existing=True,
    )

    # Monthly review on the 1st at 08:00
    scheduler.add_job(
        job_monthly_review,
        CronTrigger(day=1, hour=8, minute=0, timezone=TZ),
        id="monthly_review",
        name="Monthly Review",
        replace_existing=True,
    )

    # Investment tracker (daily P&L) — 8:00am and 4:00pm daily
    scheduler.add_job(
        job_investment_tracker,
        CronTrigger(hour="8,16", minute=0, timezone=TZ),
        id="investment_tracker",
        name="Investment Tracker",
        replace_existing=True,
    )

    # Weekly investment tracker (full portfolio view) — Sundays at 18:00
    scheduler.add_job(
        job_weekly_investment_tracker,
        CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=TZ),
        id="weekly_investment_tracker",
        name="Weekly Investment Tracker",
        replace_existing=True,
    )

    # Snapshot investments — 1:30pm daily (store day's prices to DB)
    scheduler.add_job(
        job_snapshot_investments,
        CronTrigger(hour=13, minute=30, timezone=TZ),
        id="snapshot_investments",
        name="Snapshot Investments",
        replace_existing=True,
    )

    # Transaction sync every 6 hours
    scheduler.add_job(
        job_sync_transactions,
        CronTrigger(hour="*/6", minute=30, timezone=TZ),
        id="sync_transactions",
        name="Sync Transactions",
        replace_existing=True,
    )

    web_api.set_scheduler(scheduler)

    scheduler.start()
    logger.info("Scheduler started. Jobs registered:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name}: {job.trigger}")

    try:
        uvicorn.run(web_api.app, host="0.0.0.0", port=config.WEB_PORT)
    except (KeyboardInterrupt, SystemExit):
        logger.info("FinanceAdvisor shutting down.")
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
