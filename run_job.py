"""
Manual job runner — used by Makefile targets to trigger jobs inside the container.

Usage:
    python run_job.py <job>

Jobs: digest | anomaly | weekly | monthly | sync
"""
import asyncio
import sys
import config  # noqa: F401

JOBS = {
    "digest": "agent.analyzer.daily_digest",
    "anomaly": "agent.analyzer.anomaly_check",
    "weekly": "agent.analyzer.weekly_report",
    "monthly": "agent.analyzer.monthly_review",
    "sync": None,  # handled separately below
}


async def run_sync():
    from data.fetcher import get_transactions
    import storage.repository as repo
    from storage.database import init_db

    await init_db()
    print("Syncing last 30 days of transactions...")
    txns = await get_transactions(days=30)
    count = await repo.upsert_transactions(txns)
    await repo.compute_and_update_baselines()
    print(f"Sync complete: {count} transactions upserted.")


async def run_analysis(module_path: str):
    from storage.database import init_db
    from notifications.telegram import send_digest

    await init_db()
    module, func = module_path.rsplit(".", 1)
    import importlib
    mod = importlib.import_module(module)
    result = await getattr(mod, func)()
    print(result.raw_response)
    await send_digest(result)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in JOBS:
        print(f"Usage: python run_job.py <{'|'.join(JOBS)}>\n")
        sys.exit(1)

    job = sys.argv[1]
    if job == "sync":
        asyncio.run(run_sync())
    else:
        asyncio.run(run_analysis(JOBS[job]))
