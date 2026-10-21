import asyncio
import logging
from uuid import UUID

import asyncpg
import httpx

from app.config import Settings
from app.services.tasks import handle_dispatch_failure, mark_sent

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, pool: asyncpg.Pool, http: httpx.AsyncClient, settings: Settings) -> None:
        self._pool = pool
        self._http = http
        self._settings = settings
        self._running: set[asyncio.Task] = set()

    def spawn(self, attempt_id: UUID) -> None:
        job = asyncio.create_task(self.run(attempt_id))
        self._running.add(job)
        job.add_done_callback(self._running.discard)

    async def run(self, attempt_id: UUID) -> None:
        current: UUID | None = attempt_id
        while current is not None:
            try:
                current = await self._send(current)
            except Exception:
                logger.exception("dispatch of attempt %s failed", current)
                return

    async def close(self) -> None:
        for job in list(self._running):
            job.cancel()
        await asyncio.gather(*self._running, return_exceptions=True)

    async def _send(self, attempt_id: UUID) -> UUID | None:
        row = await self._pool.fetchrow(
            """
            SELECT a.provider, a.status, t.prompt, t.ref_images
            FROM attempts a JOIN tasks t ON t.id = a.task_id
            WHERE a.id = $1
            """,
            attempt_id,
        )
        if row is None or row["status"] != "sending":
            return None

        base_url = self._settings.provider_urls.get(row["provider"])
        if base_url is None:
            return await self._fail(attempt_id, "failed", f"unknown provider {row['provider']}")

        body = {
            "reference": str(attempt_id),
            "prompt": row["prompt"],
            "ref_images": row["ref_images"],
            "callback_url": f"{self._settings.public_url}/webhooks/providers/{row['provider']}",
        }
        try:
            response = await self._http.post(
                f"{base_url}/tasks", json=body, timeout=self._settings.dispatch_timeout
            )
        except httpx.TimeoutException:
            return await self._fail(attempt_id, "timed_out", "dispatch timed out")
        except httpx.HTTPError as exc:
            return await self._fail(attempt_id, "failed", f"dispatch error: {type(exc).__name__}")

        if response.status_code != 202:
            return await self._fail(attempt_id, "failed", f"dispatch rejected with HTTP {response.status_code}")

        try:
            external_id = response.json().get("external_id")
        except ValueError:
            external_id = None
        await mark_sent(self._pool, attempt_id, external_id)
        return None

    async def _fail(self, attempt_id: UUID, status: str, detail: str) -> UUID | None:
        logger.warning("attempt %s: %s", attempt_id, detail)
        return await handle_dispatch_failure(self._pool, self._settings, attempt_id, status, detail)
