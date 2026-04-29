"""
Shared in-memory state for tracking currently-running scheduled jobs.
Uses a threading.Lock because scheduler jobs run in background threads.
SSE subscribers receive a snapshot of _running whenever it changes.

Each entry in _running is a dict:
  {
    "started_at": "<ISO-8601 UTC>",
    "stage":      "<current stage name or None>",
    "tool_calls": <int>,
  }
"""
import asyncio
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_running: dict[str, dict] = {}  # job_id -> progress dict

# asyncio queues for SSE subscribers (one per connected client)
_subscribers: list[asyncio.Queue] = []
_subscribers_lock = threading.Lock()

# Per-job cancellation events
_cancel_events: dict[str, threading.Event] = {}
_cancel_lock = threading.Lock()


def _notify() -> None:
    """Push a snapshot to all SSE subscribers (called while _lock is held)."""
    snapshot = {k: dict(v) for k, v in _running.items()}
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
    cancel_ev = threading.Event()
    with _cancel_lock:
        _cancel_events[job_id] = cancel_ev
    with _lock:
        _running[job_id] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "stage": None,
            "tool_calls": 0,
        }
        _notify()


def request_cancel(job_id: str) -> bool:
    """Signal a running job to stop. Returns True if the job was found."""
    with _cancel_lock:
        ev = _cancel_events.get(job_id)
    if ev is None:
        return False
    ev.set()
    return True


def get_cancel_event(job_id: str) -> "threading.Event | None":
    with _cancel_lock:
        return _cancel_events.get(job_id)


def update_stage(job_id: str, stage: str) -> None:
    with _lock:
        if job_id in _running:
            _running[job_id]["stage"] = stage
            _notify()


def increment_tool_calls(job_id: str) -> None:
    with _lock:
        if job_id in _running:
            _running[job_id]["tool_calls"] += 1
            _notify()


def mark_done(job_id: str) -> None:
    with _cancel_lock:
        _cancel_events.pop(job_id, None)
    with _lock:
        _running.pop(job_id, None)
        _notify()


def get_running() -> dict[str, dict]:
    with _lock:
        return {k: dict(v) for k, v in _running.items()}
