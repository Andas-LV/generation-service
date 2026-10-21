import uuid
from uuid import UUID

import asyncpg

from app.config import Settings
from app.errors import ServiceError
from app.schemas import ProviderWebhook
from app.services.ledger import post_entry

OPEN_STATUSES = ("sending", "waiting")


async def create_task(
    pool: asyncpg.Pool,
    settings: Settings,
    *,
    user_id: int,
    model_slug: str,
    prompt: str,
    ref_images: list[str],
) -> tuple[UUID, UUID]:
    async with pool.acquire() as conn:
        model = await conn.fetchrow("SELECT id, price FROM models WHERE slug = $1", model_slug)
        if model is None:
            raise ServiceError(404, "model not found")
        leg = await conn.fetchrow(
            """
            SELECT * FROM legs
            WHERE model_id = $1 AND max_ref_images >= $2
            ORDER BY priority
            LIMIT 1
            """,
            model["id"],
            len(ref_images),
        )
        if leg is None:
            raise ServiceError(400, "no leg accepts this number of reference images")

        async with conn.transaction():
            user = await conn.fetchrow("SELECT balance FROM users WHERE id = $1 FOR UPDATE", user_id)
            if user is None:
                raise ServiceError(404, "user not found")
            if user["balance"] < model["price"]:
                raise ServiceError(402, "insufficient tokens")
            active = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE user_id = $1 AND status = 'processing'",
                user_id,
            )
            if active >= settings.max_active_tasks:
                raise ServiceError(429, "too many active tasks")

            task_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO tasks (id, user_id, model_id, prompt, ref_images, price, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'processing')
                """,
                task_id,
                user_id,
                model["id"],
                prompt,
                ref_images,
                model["price"],
            )
            await post_entry(conn, user_id=user_id, amount=-model["price"], kind="charge", task_id=task_id)
            attempt_id = await open_attempt(conn, task_id, leg, settings.provider_timeout)
    return task_id, attempt_id


async def open_attempt(conn: asyncpg.Connection, task_id: UUID, leg: asyncpg.Record, timeout: float) -> UUID:
    attempt_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO attempts (id, task_id, leg_id, provider, priority, status, deadline_at)
        VALUES ($1, $2, $3, $4, $5, 'sending', now() + make_interval(secs => $6::float8))
        """,
        attempt_id,
        task_id,
        leg["id"],
        leg["provider"],
        leg["priority"],
        timeout,
    )
    await conn.execute(
        "UPDATE tasks SET current_attempt_id = $2, updated_at = now() WHERE id = $1",
        task_id,
        attempt_id,
    )
    return attempt_id


async def lock_task(conn: asyncpg.Connection, task_id: UUID) -> asyncpg.Record:
    return await conn.fetchrow("SELECT * FROM tasks WHERE id = $1 FOR UPDATE", task_id)


async def fetch_attempt(conn: asyncpg.Connection, attempt_id: UUID) -> asyncpg.Record:
    return await conn.fetchrow("SELECT * FROM attempts WHERE id = $1", attempt_id)


async def next_leg(conn: asyncpg.Connection, task: asyncpg.Record, attempt: asyncpg.Record) -> asyncpg.Record | None:
    moderated = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM attempts WHERE task_id = $1 AND error_type = 'moderation')",
        task["id"],
    )
    return await conn.fetchrow(
        """
        SELECT * FROM legs
        WHERE model_id = $1
          AND priority > $2
          AND max_ref_images >= $3
          AND NOT (skip_on_moderation AND $4)
        ORDER BY priority
        LIMIT 1
        """,
        task["model_id"],
        attempt["priority"],
        len(task["ref_images"]),
        moderated,
    )


async def fail_task(conn: asyncpg.Connection, task: asyncpg.Record, error: str) -> None:
    await conn.execute(
        "UPDATE tasks SET status = 'failed', error = $2, updated_at = now() WHERE id = $1",
        task["id"],
        error,
    )
    await post_entry(conn, user_id=task["user_id"], amount=task["price"], kind="refund", task_id=task["id"])


async def register_failure(
    conn: asyncpg.Connection,
    settings: Settings,
    task: asyncpg.Record,
    attempt: asyncpg.Record,
    *,
    status: str,
    error_type: str,
    detail: str | None,
) -> UUID | None:
    await conn.execute(
        """
        UPDATE attempts
        SET status = $2, error_type = $3, error_detail = $4, finished_at = now()
        WHERE id = $1
        """,
        attempt["id"],
        status,
        error_type,
        detail,
    )
    if error_type == "payload":
        await fail_task(conn, task, "payload rejected by provider")
        return None
    leg = await next_leg(conn, task, attempt)
    if leg is None:
        await fail_task(conn, task, f"all legs failed, last error: {error_type}")
        return None
    return await open_attempt(conn, task["id"], leg, settings.provider_timeout)


async def complete_task(conn: asyncpg.Connection, task: asyncpg.Record, attempt: asyncpg.Record, result_url: str) -> None:
    await conn.execute(
        "UPDATE attempts SET status = 'completed', result_url = $2, finished_at = now() WHERE id = $1",
        attempt["id"],
        result_url,
    )
    await conn.execute(
        "UPDATE tasks SET status = 'completed', result_url = $2, updated_at = now() WHERE id = $1",
        task["id"],
        result_url,
    )


async def handle_late_result(
    conn: asyncpg.Connection, task: asyncpg.Record, attempt: asyncpg.Record, result_url: str
) -> str:
    if task["status"] != "processing":
        await conn.execute(
            "UPDATE attempts SET result_url = $2, late = true WHERE id = $1",
            attempt["id"],
            result_url,
        )
        return "late_discarded"
    await conn.execute(
        """
        UPDATE attempts SET status = 'cancelled', finished_at = now()
        WHERE task_id = $1 AND status IN ('sending', 'waiting')
        """,
        task["id"],
    )
    await conn.execute(
        "UPDATE attempts SET status = 'completed', result_url = $2, late = true WHERE id = $1",
        attempt["id"],
        result_url,
    )
    await conn.execute(
        """
        UPDATE tasks SET status = 'completed', result_url = $2, current_attempt_id = $3, updated_at = now()
        WHERE id = $1
        """,
        task["id"],
        result_url,
        attempt["id"],
    )
    return "late_accepted"


def _parse_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


async def handle_provider_webhook(
    pool: asyncpg.Pool, settings: Settings, provider: str, payload: ProviderWebhook
) -> tuple[str, UUID | None]:
    async with pool.acquire() as conn, conn.transaction():
        ref = await conn.fetchrow(
            """
            SELECT id, task_id FROM attempts
            WHERE provider = $1 AND (id = $2 OR external_id = $3)
            LIMIT 1
            """,
            provider,
            _parse_uuid(payload.reference),
            payload.external_id,
        )
        if ref is None:
            raise ServiceError(404, "attempt not found")

        task = await lock_task(conn, ref["task_id"])
        attempt = await fetch_attempt(conn, ref["id"])

        if payload.external_id and attempt["external_id"] is None:
            await conn.execute(
                "UPDATE attempts SET external_id = $2 WHERE id = $1",
                attempt["id"],
                payload.external_id,
            )

        if attempt["status"] in OPEN_STATUSES:
            if payload.status == "completed":
                await complete_task(conn, task, attempt, payload.result_url)
                return "completed", None
            next_attempt = await register_failure(
                conn,
                settings,
                task,
                attempt,
                status="failed",
                error_type=payload.error_type,
                detail="reported by provider",
            )
            return ("next_leg" if next_attempt else "failed"), next_attempt

        if payload.status == "completed" and attempt["status"] == "timed_out":
            return await handle_late_result(conn, task, attempt, payload.result_url), None

        return "ignored", None


async def handle_dispatch_failure(
    pool: asyncpg.Pool, settings: Settings, attempt_id: UUID, status: str, detail: str
) -> UUID | None:
    async with pool.acquire() as conn, conn.transaction():
        task_id = await conn.fetchval("SELECT task_id FROM attempts WHERE id = $1", attempt_id)
        task = await lock_task(conn, task_id)
        attempt = await fetch_attempt(conn, attempt_id)
        if attempt["status"] != "sending":
            return None
        return await register_failure(
            conn, settings, task, attempt, status=status, error_type="technical", detail=detail
        )


async def mark_sent(pool: asyncpg.Pool, attempt_id: UUID, external_id: str | None) -> None:
    await pool.execute(
        """
        UPDATE attempts
        SET external_id = COALESCE(external_id, $2),
            status = CASE WHEN status = 'sending' THEN 'waiting' ELSE status END
        WHERE id = $1
        """,
        attempt_id,
        external_id,
    )


async def expire_one(pool: asyncpg.Pool, settings: Settings) -> tuple[bool, UUID | None]:
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT a.id, a.task_id
            FROM attempts a
            JOIN tasks t ON t.id = a.task_id
            WHERE a.status IN ('sending', 'waiting') AND a.deadline_at < now()
            ORDER BY a.deadline_at
            LIMIT 1
            FOR UPDATE OF t SKIP LOCKED
            """
        )
        if row is None:
            return False, None
        task = await lock_task(conn, row["task_id"])
        attempt = await fetch_attempt(conn, row["id"])
        if attempt["status"] not in OPEN_STATUSES:
            return True, None
        next_attempt = await register_failure(
            conn,
            settings,
            task,
            attempt,
            status="timed_out",
            error_type="technical",
            detail="provider did not respond in time",
        )
        return True, next_attempt


async def get_task(pool: asyncpg.Pool, task_id: UUID) -> dict | None:
    async with pool.acquire() as conn:
        task = await conn.fetchrow(
            """
            SELECT t.*, m.slug AS model_slug
            FROM tasks t JOIN models m ON m.id = t.model_id
            WHERE t.id = $1
            """,
            task_id,
        )
        if task is None:
            return None
        attempts = await conn.fetch(
            """
            SELECT id, provider, priority, status, external_id, error_type, error_detail,
                   result_url, late, started_at, deadline_at, finished_at
            FROM attempts
            WHERE task_id = $1
            ORDER BY started_at, priority
            """,
            task_id,
        )
    history = [dict(row) for row in attempts]
    current = next((item for item in history if item["id"] == task["current_attempt_id"]), None)
    return {
        "id": task["id"],
        "user_id": task["user_id"],
        "model_slug": task["model_slug"],
        "prompt": task["prompt"],
        "ref_images": task["ref_images"],
        "price": task["price"],
        "status": task["status"],
        "result_url": task["result_url"],
        "error": task["error"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
        "current_leg": current,
        "attempts": history,
    }
