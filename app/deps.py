import asyncpg
from fastapi import Header, HTTPException, Request

from app.config import Settings, get_settings
from app.services.dispatcher import Dispatcher
from app.signing import verify


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def get_dispatcher(request: Request) -> Dispatcher:
    return request.app.state.dispatcher


def settings() -> Settings:
    return get_settings()


async def signed_body(request: Request, x_signature: str | None = Header(default=None)) -> bytes:
    body = await request.body()
    if not verify(body, x_signature, get_settings().webhook_secret):
        raise HTTPException(status_code=401, detail="invalid signature")
    return body
