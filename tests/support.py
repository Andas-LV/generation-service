import asyncio
import json
import os
import random
import time
import uuid
from collections.abc import Callable

import asyncpg
import httpx

from app.signing import sign

API_URL = os.environ.get("API_URL", "http://localhost:8000")
PROVIDER_URLS = {
    "alpha": os.environ.get("ALPHA_URL", "http://localhost:9001"),
    "beta": os.environ.get("BETA_URL", "http://localhost:9002"),
}
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://gen:gen@localhost:5433/gen")
SECRET = os.environ.get("WEBHOOK_SECRET", "dev-secret")
PROVIDER_TIMEOUT = float(os.environ.get("PROVIDER_TIMEOUT", "60"))


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


async def signed_post(client: httpx.AsyncClient, path: str, payload: dict) -> httpx.Response:
    body = json.dumps(payload).encode()
    return await client.post(
        path,
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": sign(body, SECRET)},
    )


async def new_user(client: httpx.AsyncClient, tokens: int) -> int:
    user_id = random.randint(10**9, 9 * 10**12)
    response = await signed_post(
        client,
        "/webhooks/payments",
        {"payment_id": unique("pay"), "user_id": user_id, "tokens": tokens, "status": "succeeded"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["credited"] is True
    return user_id


async def set_scenario(provider: str, key: str, **behavior) -> None:
    async with httpx.AsyncClient(base_url=PROVIDER_URLS[provider], timeout=10) as client:
        response = await client.put(f"/scenarios/{key}", json=behavior)
        response.raise_for_status()


async def generate(client: httpx.AsyncClient, user_id: int, model_slug: str, prompt: str, refs: int = 0) -> str:
    response = await client.post(
        "/generate",
        json={
            "user_id": user_id,
            "model_slug": model_slug,
            "prompt": prompt,
            "ref_images": [f"https://refs.example/{i}.png" for i in range(refs)],
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["task_id"]


async def wait_for_task(
    client: httpx.AsyncClient, task_id: str, condition: Callable[[dict], bool], timeout: float
) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        response = await client.get(f"/tasks/{task_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if condition(task):
            return task
        if time.monotonic() > deadline:
            raise AssertionError(f"task did not reach expected state in {timeout}s: {task}")
        await asyncio.sleep(0.5)


async def balance(client: httpx.AsyncClient, user_id: int) -> dict:
    response = await client.get(f"/users/{user_id}/balance")
    assert response.status_code == 200, response.text
    return response.json()


async def assert_balance_matches_ledger(db: asyncpg.Connection, user_id: int) -> None:
    row = await db.fetchrow(
        """
        SELECT u.balance, COALESCE(SUM(l.amount), 0) AS total
        FROM users u LEFT JOIN ledger l ON l.user_id = u.id
        WHERE u.id = $1
        GROUP BY u.balance
        """,
        user_id,
    )
    assert row["balance"] == row["total"]


async def ledger_count(db: asyncpg.Connection, task_id: str, kind: str) -> int:
    return await db.fetchval(
        "SELECT count(*) FROM ledger WHERE task_id = $1 AND kind = $2",
        uuid.UUID(task_id),
        kind,
    )
