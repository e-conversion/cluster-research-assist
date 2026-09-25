import os
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from sqlalchemy import text

from cra.app.auth import local
from cra.app.history import migrate
from cra.app.history.engine import make_engine, make_session_factory
from cra.app.history.repository import Repository
from cra.app.history.tables import Base
from cra.app.web.factory import create_app
from cra.config.settings import Settings

# read at import time: the autouse fixture below wipes CRA_ variables per test
POSTGRES_URL = os.environ.get("CRA_TEST_POSTGRES_URL", "")
# 19 real arXiv preprints; rebuilt by developer/make_toy_library.py, which needs
# the network and the build extras and so is not part of the suite
TOY_LIBRARY = Path(__file__).resolve().parent / "data" / "library"


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch, tmp_path):
    """No stray CRA_ variables or .env from the developer's shell reach a test."""
    for key in list(os.environ):
        if key.startswith("CRA_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("PYTHON_COLORS", "0")  # argparse colours help on a tty (3.14)
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _cheap_hashing(monkeypatch):
    """argon2 at its real cost would make every sign-in in the suite slow."""
    monkeypatch.setattr(
        local, "HASHER", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )
    monkeypatch.setattr(local, "_dummy_hash", None)


PASSWORD = "correct horse battery"


async def add_account(app, username: str, role: str = "user", **fields) -> str:
    """A password account with ``PASSWORD``; returns its user id."""
    repo = app.extensions["cra"].repo
    user = await local.create_user(
        repo, username, fields.get("name", username), fields.get("email", ""), role
    )
    await repo.set_password_hash(user.id, await local.hash_password(PASSWORD))
    return user.id


async def sign_in(
    app, client, username: str = "alice", role: str = "user", name: str = ""
):
    """Signs ``client`` in with a password, creating the account if needed."""
    repo = app.extensions["cra"].repo
    if await repo.get_credential_by_username(username) is None:
        await add_account(app, username, role, name=name or username)
    response = await client.post(
        "/auth/password", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, await response.get_data(as_text=True)
    return client


async def session_user(client) -> str | None:
    """The display name the session reports, or None while not signed in."""
    response = await client.get("/api/session")
    if response.status_code == 401:
        return None
    return (await response.get_json())["user"]


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "library_path": TOY_LIBRARY,
        "history_url": f"sqlite+aiosqlite:///{tmp_path}/cra.sqlite",
        "history_auto_migrate": True,
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
