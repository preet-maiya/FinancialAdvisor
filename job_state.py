"""
Shared in-memory state for tracking currently-running scheduled jobs.
Uses a threading.Lock because scheduler jobs run in background threads.
"""
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_running: dict[str, str] = {}  # job_id -> ISO-8601 start time (UTC)


def mark_started(job_id: str) -> None:
    with _lock:
        _running[job_id] = datetime.now(timezone.utc).isoformat()


def mark_done(job_id: str) -> None:
    with _lock:
        _running.pop(job_id, None)


def get_running() -> dict[str, str]:
    with _lock:
        return dict(_running)
