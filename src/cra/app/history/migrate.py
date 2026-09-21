"""Alembic driven from code so ``cra db`` and ``cra serve`` share one path.

The migration scripts receive an already-open synchronous connection through
``config.attributes["connection"]``; that is what lets them run inside the
application's event loop.
"""

from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


async def _run(engine: AsyncEngine, fn: Callable[[Connection], None]) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(fn)


async def upgrade(engine: AsyncEngine, revision: str = "head") -> None:
    await _run(engine, lambda c: command.upgrade(alembic_config(c), revision))


async def downgrade(engine: AsyncEngine, revision: str) -> None:
    await _run(engine, lambda c: command.downgrade(alembic_config(c), revision))


async def revision(engine: AsyncEngine, message: str) -> None:
    """Autogenerate a migration from the difference between schema and database."""
    await _run(
        engine,
        lambda c: command.revision(
            alembic_config(c), message=message, autogenerate=True
        ),
    )


async def current_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda c: MigrationContext.configure(c).get_current_revision()
        )
