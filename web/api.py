import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent.prompts as prompts
from web.repository import (
    get_schedule_override,
    upsert_schedule_override,
    get_prompt_override,
    upsert_prompt_override,
    delete_prompt_override,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="FinancialAdvisor Job Manager")

STATIC_DIR = Path(__file__).parent / "static"

_scheduler = None


def set_scheduler(s) -> None:
    global _scheduler
    _scheduler = s


JOB_DEFAULTS = {
    "daily_digest":       {"minute": "0",  "hour": "7",    "day": "*", "month": "*", "day_of_week": "*"},
    "anomaly_check":      {"minute": "0",  "hour": "*/4",  "day": "*", "month": "*", "day_of_week": "*"},
    "weekly_report":      {"minute": "0",  "hour": "19",   "day": "*", "month": "*", "day_of_week": "sun"},
    "monthly_review":     {"minute": "0",  "hour": "8",    "day": "1", "month": "*", "day_of_week": "*"},
    "investment_tracker": {"minute": "0",  "hour": "8,13", "day": "*", "month": "*", "day_of_week": "*"},
    "snapshot_investments":{"minute": "30","hour": "13",   "day": "*", "month": "*", "day_of_week": "*"},
    "sync_transactions":  {"minute": "30", "hour": "*/6",  "day": "*", "month": "*", "day_of_week": "*"},
}

JOB_NAMES = {
    "daily_digest":        "Daily Digest",
    "anomaly_check":       "Anomaly Check",
    "weekly_report":       "Weekly Report",
    "monthly_review":      "Monthly Review",
    "investment_tracker":  "Investment Tracker",
    "snapshot_investments":"Snapshot Investments",
    "sync_transactions":   "Sync Transactions",
}

PROMPT_DEFAULTS = {
    "daily_digest":       prompts.DAILY_DIGEST_SYSTEM,
    "anomaly_check":      prompts.ANOMALY_CHECK_SYSTEM,
    "weekly_report":      prompts.WEEKLY_REPORT_SYSTEM,
    "monthly_review":     prompts.MONTHLY_REVIEW_SYSTEM,
    "investment_tracker": prompts.INVESTMENT_TRACKER_SYSTEM,
}


def _job_info(job_id: str) -> dict:
    job = _scheduler.get_job(job_id) if _scheduler else None
    next_run = None
    if job and job.next_run_time:
        next_run = job.next_run_time.isoformat()
    return {
        "id": job_id,
        "name": JOB_NAMES[job_id],
        "next_run_time": next_run,
        "has_prompt": job_id in PROMPT_DEFAULTS,
    }


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/job-runs")
async def list_job_runs(limit: int = 100):
    from storage.repository import get_job_runs
    return await get_job_runs(limit=limit)


@app.get("/api/jobs/running")
async def get_running_jobs():
    import job_state
    return job_state.get_running()


@app.get("/api/jobs/running/stream")
async def stream_running_jobs(request: Request):
    import job_state

    async def event_generator():
        q = job_state.subscribe()
        # Send current state immediately on connect
        yield f"data: {json.dumps(job_state.get_running())}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    snapshot = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {json.dumps(snapshot)}\n\n"
                except asyncio.TimeoutError:
                    # Send a keepalive comment so the connection stays alive
                    yield ": keepalive\n\n"
        finally:
            job_state.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs")
async def list_jobs():
    jobs = []
    for job_id in JOB_DEFAULTS:
        info = _job_info(job_id)
        override = await get_schedule_override(job_id)
        info["cron"] = override or JOB_DEFAULTS[job_id]
        jobs.append(info)
    return jobs


@app.get("/api/jobs/{job_id}/schedule")
async def get_schedule(job_id: str):
    if job_id not in JOB_DEFAULTS:
        raise HTTPException(status_code=404, detail="Job not found")
    override = await get_schedule_override(job_id)
    return {"job_id": job_id, "cron": override or JOB_DEFAULTS[job_id], "is_override": override is not None}


class ScheduleBody(BaseModel):
    minute: str
    hour: str
    day: str
    month: str
    day_of_week: str


@app.put("/api/jobs/{job_id}/schedule")
async def update_schedule(job_id: str, body: ScheduleBody):
    if job_id not in JOB_DEFAULTS:
        raise HTTPException(status_code=404, detail="Job not found")
    fields = body.model_dump()
    await upsert_schedule_override(job_id, fields)
    if _scheduler:
        from apscheduler.triggers.cron import CronTrigger
        import pytz
        tz = pytz.timezone("America/New_York")
        try:
            _scheduler.reschedule_job(
                job_id,
                trigger=CronTrigger(
                    minute=fields["minute"],
                    hour=fields["hour"],
                    day=fields["day"],
                    month=fields["month"],
                    day_of_week=fields["day_of_week"],
                    timezone=tz,
                ),
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid cron expression: {e}")
    job = _scheduler.get_job(job_id) if _scheduler else None
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    return {"ok": True, "next_run_time": next_run}


@app.get("/api/jobs/{job_id}/prompt")
async def get_prompt(job_id: str):
    if job_id not in PROMPT_DEFAULTS:
        raise HTTPException(status_code=404, detail="Job has no prompt")
    override = await get_prompt_override(job_id)
    return {
        "job_id": job_id,
        "system_prompt": override or PROMPT_DEFAULTS[job_id],
        "is_override": override is not None,
    }


class PromptBody(BaseModel):
    system_prompt: str


@app.put("/api/jobs/{job_id}/prompt")
async def update_prompt(job_id: str, body: PromptBody):
    if job_id not in PROMPT_DEFAULTS:
        raise HTTPException(status_code=404, detail="Job has no prompt")
    await upsert_prompt_override(job_id, body.system_prompt)
    return {"ok": True}


@app.delete("/api/jobs/{job_id}/prompt")
async def reset_prompt(job_id: str):
    if job_id not in PROMPT_DEFAULTS:
        raise HTTPException(status_code=404, detail="Job has no prompt")
    await delete_prompt_override(job_id)
    return {"ok": True, "system_prompt": PROMPT_DEFAULTS[job_id]}


CHAT_SYSTEM = (
    "You are a personal financial advisor with access to the user's real financial data. "
    "Answer questions about their spending, budgets, savings, net worth, subscriptions, and transactions. "
    "Be specific and use actual numbers from the data. Be concise and helpful."
)


class ChatBody(BaseModel):
    message: str
    session_id: int | None = None


@app.get("/api/chat/sessions")
async def list_chat_sessions():
    from storage.repository import get_chat_sessions
    return await get_chat_sessions()


@app.get("/api/chat/sessions/{session_id}")
async def get_session_messages(session_id: int):
    from storage.repository import get_chat_messages
    return await get_chat_messages(session_id)


@app.delete("/api/chat/sessions/{session_id}", status_code=204)
async def delete_session(session_id: int):
    from storage.repository import delete_chat_session
    await delete_chat_session(session_id)


@app.post("/api/chat/stream")
async def chat_stream(body: ChatBody, request: Request):
    from agent.react import run_react_stream
    from agent.llm import get_llm
    from agent.tools import ALL_TOOLS
    from storage.repository import create_chat_session, add_chat_message, get_chat_messages

    session_id = body.session_id
    if session_id is None:
        title = body.message[:60] + ("…" if len(body.message) > 60 else "")
        session_id = await create_chat_session(title)

    history = await get_chat_messages(session_id)
    await add_chat_message(session_id, "user", body.message)
    prior = [{"role": m["role"], "content": m["content"]} for m in history]

    async def event_generator():
        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
        reply_parts: list[str] = []
        try:
            async for event in run_react_stream(get_llm(), ALL_TOOLS, CHAT_SYSTEM, body.message, history=prior):
                if await request.is_disconnected():
                    break
                if event["type"] == "token":
                    reply_parts.append(event["text"])
                elif event["type"] == "reset":
                    reply_parts.clear()
                elif event["type"] == "done":
                    full_reply = event.get("reply") or "".join(reply_parts)
                    await add_chat_message(session_id, "assistant", full_reply)
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            logger.exception("Error in chat stream")
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat")
async def chat(body: ChatBody):
    from agent.react import run_react
    from agent.llm import get_llm
    from agent.tools import ALL_TOOLS
    from storage.repository import create_chat_session, add_chat_message, get_chat_messages

    session_id = body.session_id
    if session_id is None:
        title = body.message[:60] + ("…" if len(body.message) > 60 else "")
        session_id = await create_chat_session(title)

    history = await get_chat_messages(session_id)
    await add_chat_message(session_id, "user", body.message)

    prior = [{"role": m["role"], "content": m["content"]} for m in history]
    reply = await run_react(get_llm(), ALL_TOOLS, CHAT_SYSTEM, body.message, history=prior)
    await add_chat_message(session_id, "assistant", reply)

    return {"reply": reply, "session_id": session_id}


@app.post("/api/jobs/{job_id}/trigger", status_code=202)
async def trigger_job(job_id: str):
    if job_id not in JOB_DEFAULTS:
        raise HTTPException(status_code=404, detail="Job not found")
    if not _scheduler:
        raise HTTPException(status_code=503, detail="Scheduler not available")
    job = _scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found in scheduler")
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, job.func)
    return {"ok": True, "message": f"Job {job_id} triggered"}
