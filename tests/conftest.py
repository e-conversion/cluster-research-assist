import os
from pathlib import Path

import pytest
from sqlalchemy import text

from cra.app.history import migrate
from cra.app.history.engine import make_engine, make_session_factory
from cra.app.history.repository import Repository
from cra.app.history.tables import Base
from cra.app.web.factory import create_app
from cra.config.settings import Settings

# read at import time: the autouse fixture below wipes CRA_ variables per test
POSTGRES_URL = os.environ.get("CRA_TEST_POSTGRES_URL", "")


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch, tmp_path):
    """No stray CRA_ variables or .env from the developer's shell reach a test."""
    for key in list(os.environ):
        if key.startswith("CRA_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "corpus_path": tmp_path,
        "history_url": f"sqlite+aiosqlite:///{tmp_path}/cra.sqlite",
        "history_auto_migrate": True,
        "auth_provider": "dev",
        "auth_dev_user": "alice",
        "cookie_secure": False,
        "log_dir": tmp_path / "logs",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def db_url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite+aiosqlite:///{tmp_path}/cra.sqlite"
    if not POSTGRES_URL:
        pytest.skip("CRA_TEST_POSTGRES_URL not set")
    return POSTGRES_URL


@pytest.fixture
async def engine(db_url):
    engine = make_engine(db_url)
    await migrate.upgrade(engine)
    yield engine
    if engine.dialect.name != "sqlite":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await engine.dispose()


@pytest.fixture
def repo(engine):
    return Repository(make_session_factory(engine))


@pytest.fixture
async def app(tmp_path):
    app = create_app(make_settings(tmp_path))
    async with app.test_app():
        yield app


@pytest.fixture
def client(app):
    return app.test_client()
