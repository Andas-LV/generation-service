import asyncpg

from app.errors import ServiceError
from app.schemas import PaymentWebhook
from app.services.ledger import post_entry

CREDITED_STATUS = "succeeded"


async def handle_payment(pool: asyncpg.Pool, payment: PaymentWebhook) -> bool:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING", payment.user_id)
        stored = await conn.fetchrow(
            """
            INSERT INTO payments (payment_id, user_id, tokens, status)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (payment_id) DO UPDATE
                SET status = CASE
                        WHEN payments.status = $5 THEN payments.status
                        ELSE EXCLUDED.status
                    END,
                    updated_at = now()
            RETURNING user_id, tokens, status
            """,
            payment.payment_id,
            payment.user_id,
            payment.tokens,
            payment.status,
            CREDITED_STATUS,
        )
        if stored["user_id"] != payment.user_id or stored["tokens"] != payment.tokens:
            raise ServiceError(409, "payment conflicts with an already registered one")
        if stored["status"] != CREDITED_STATUS:
            return False
        return await post_entry(
            conn,
            user_id=payment.user_id,
            amount=payment.tokens,
            kind="payment",
            payment_id=payment.payment_id,
        )
