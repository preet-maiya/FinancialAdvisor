from storage.database import get_db


async def get_schedule_override(job_id: str) -> dict | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT minute, hour, day, month, day_of_week FROM job_schedule_overrides WHERE id = ?",
            [job_id],
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def upsert_schedule_override(job_id: str, fields: dict) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO job_schedule_overrides (id, minute, hour, day, month, day_of_week, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                minute=excluded.minute,
                hour=excluded.hour,
                day=excluded.day,
                month=excluded.month,
                day_of_week=excluded.day_of_week,
                updated_at=excluded.updated_at
            """,
            [
                job_id,
                fields.get("minute"),
                fields.get("hour"),
                fields.get("day"),
                fields.get("month"),
                fields.get("day_of_week"),
            ],
        )
        await db.commit()


async def get_prompt_override(job_id: str) -> str | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT system_prompt FROM prompt_overrides WHERE job_id = ?",
            [job_id],
        ) as cursor:
            row = await cursor.fetchone()
    return row["system_prompt"] if row else None


async def upsert_prompt_override(job_id: str, system_prompt: str) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO prompt_overrides (job_id, system_prompt, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(job_id) DO UPDATE SET
                system_prompt=excluded.system_prompt,
                updated_at=excluded.updated_at
            """,
            [job_id, system_prompt],
        )
        await db.commit()


async def delete_prompt_override(job_id: str) -> None:
    async with get_db() as db:
        await db.execute(
            "DELETE FROM prompt_overrides WHERE job_id = ?", [job_id]
        )
        await db.commit()
