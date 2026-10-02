from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str, pool_size: int = 5, max_overflow: int = 10) -> AsyncEngine:
    if url.startswith("sqlite"):
        engine = create_async_engine(url)
    else:
        # A turn holds no connection while it waits for the model: every
        # repository call opens and closes its own session. pre_ping replaces
        # connections a database restart left dead instead of failing a
        # request on them.
        engine = create_async_engine(
            url, pool_size=pool_size, max_overflow=max_overflow, pool_pre_ping=True
        )
    if engine.dialect.name == "sqlite":
        # SQLite ignores ON DELETE clauses unless asked per connection.
        @event.listens_for(engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
