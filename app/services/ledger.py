from uuid import UUID

import asyncpg


async def post_entry(
    conn: asyncpg.Connection,
    *,
    user_id: int,
    amount: int,
    kind: str,
    task_id: UUID | None = None,
    payment_id: str | None = None,
) -> bool:
    row = await conn.fetchrow(
        """
        INSERT INTO ledger (user_id, amount, kind, task_id, payment_id)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        user_id,
        amount,
        kind,
        task_id,
        payment_id,
    )
    return row is not None


async def balance_with_tail(pool: asyncpg.Pool, user_id: int, limit: int) -> dict | None:
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id, balance FROM users WHERE id = $1", user_id)
        if user is None:
            return None
        rows = await conn.fetch(
            """
            SELECT id, amount, kind, task_id, payment_id, created_at
            FROM ledger
            WHERE user_id = $1
            ORDER BY id DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
    return {
        "user_id": user["id"],
        "balance": user["balance"],
        "ledger": [dict(row) for row in rows],
    }
