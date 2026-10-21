import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import create_pool
from app.errors import ServiceError
from app.routes import generation, webhooks
from app.services.dispatcher import Dispatcher
from app.services.watchdog import Watchdog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    pool = await create_pool(settings.database_url, settings.db_pool_size)
    http = httpx.AsyncClient()
    dispatcher = Dispatcher(pool, http, settings)
    watchdog = Watchdog(pool, settings, dispatcher)
    app.state.pool = pool
    app.state.dispatcher = dispatcher
    watchdog.start()
    try:
        yield
    finally:
        await watchdog.stop()
        await dispatcher.close()
        await http.aclose()
        await pool.close()


app = FastAPI(title="Generation service", lifespan=lifespan)
app.include_router(generation.router)
app.include_router(webhooks.router)


@app.exception_handler(ServiceError)
async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
