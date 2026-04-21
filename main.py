import asyncio
import logging
import traceback

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import uvicorn

import config  # noqa: F401 — loads env and configures logging
from storage.database import init_db
from data.fetcher import get_transactions, get_accounts
from notifications.telegram import send_startup_message, send_message
import storage.repository as repo
from scheduler.jobs import (
    job_daily_digest,
    job_anomaly_check,
    job_weekly_report,
    job_monthly_review,
    job_sync_transactions,
)
import web.api as web_api

logger = logging.getLogger(__name__)

TZ = pytz.timezone("America/New_York")


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

    # 3. Build account summary for startup message
    summary_lines = []
    try:
        accounts = await get_accounts()
        total_assets = sum(a.balance for a in accounts if a.balance > 0)
        total_liabilities = abs(sum(a.balance for a in accounts if a.balance < 0))
        net_worth = total_assets - total_liabilities
        summary_lines.append(f"💳 Accounts synced: {len(accounts)}")
        summary_lines.append(f"🏦 Total assets: ${total_assets:,.2f}")
        summary_lines.append(f"💳 Total liabilities: ${total_liabilities:,.2f}")
        summary_lines.append(f"📈 Net worth: ${net_worth:,.2f}")
    except Exception as e:
        logger.error(f"Failed to fetch account summary: {e}")
        summary_lines.append("_(Could not fetch account summary)_")

    # 4. Send startup notification
    await send_startup_message("\n".join(summary_lines))
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
