"""
Shared in-memory state for tracking currently-running scheduled jobs.
Uses a threading.Lock because scheduler jobs run in background threads.
SSE subscribers receive a snapshot of _running whenever it changes.
"""
import asyncio
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_running: dict[str, str] = {}  # job_id -> ISO-8601 start time (UTC)

# asyncio queues for SSE subscribers (one per connected client)
_subscribers: list[asyncio.Queue] = []
_subscribers_lock = threading.Lock()


def _notify() -> None:
    """Push a snapshot to all SSE subscribers (called while _lock is held)."""
    snapshot = dict(_running)
    with _subscribers_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(snapshot)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


def subscribe() -> asyncio.Queue:
    """Register a new SSE client; returns a queue that receives state snapshots."""
    q: asyncio.Queue = asyncio.Queue(maxsize=32)
    with _subscribers_lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _subscribers_lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def mark_started(job_id: str) -> None:
    with _lock:
        _running[job_id] = datetime.now(timezone.utc).isoformat()
        _notify()


def mark_done(job_id: str) -> None:
    with _lock:
        _running.pop(job_id, None)
        _notify()


def get_running() -> dict[str, str]:
    with _lock:
        return dict(_running)
