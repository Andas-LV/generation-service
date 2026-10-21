import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)


def database_url() -> str:
    return get_settings().database_url.replace("postgresql://", "postgresql+asyncpg://", 1)


def run_offline() -> None:
    context.configure(url=database_url(), literal_binds=True, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


def run_sync(connection) -> None:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(database_url())
    async with engine.connect() as connection:
        await connection.run_sync(run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
