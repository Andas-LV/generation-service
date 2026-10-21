import asyncio
import logging

import asyncpg

from app.config import Settings
from app.services.dispatcher import Dispatcher
from app.services.tasks import expire_one

logger = logging.getLogger(__name__)


class Watchdog:
    def __init__(self, pool: asyncpg.Pool, settings: Settings, dispatcher: Dispatcher) -> None:
        self._pool = pool
        self._settings = settings
        self._dispatcher = dispatcher
        self._stop = asyncio.Event()
        self._job: asyncio.Task | None = None

    def start(self) -> None:
        self._job = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._job is not None:
            await self._job

    async def sweep(self) -> int:
        expired = 0
        while True:
            found, next_attempt = await expire_one(self._pool, self._settings)
            if not found:
                return expired
            expired += 1
            if next_attempt is not None:
                self._dispatcher.spawn(next_attempt)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                expired = await self.sweep()
                if expired:
                    logger.info("watchdog expired %d attempts", expired)
            except Exception:
                logger.exception("watchdog sweep failed")
            try:
                await asyncio.wait_for(self._stop.wait(), self._settings.watchdog_interval)
            except asyncio.TimeoutError:
                pass
