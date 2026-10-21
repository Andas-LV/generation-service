import asyncpg
import httpx
import pytest

from tests.support import API_URL, DATABASE_URL


@pytest.fixture
async def api():
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=0)
    async with httpx.AsyncClient(base_url=API_URL, timeout=30, limits=limits) as client:
        yield client


@pytest.fixture
async def db():
    connection = await asyncpg.connect(DATABASE_URL)
    try:
        yield connection
    finally:
        await connection.close()
