from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from cra.app.history import migrate
from cra.app.history.tables import Base
from cra.cli import main


def _diff(connection):
    return compare_metadata(MigrationContext.configure(connection), Base.metadata)


async def test_head_migration_matches_the_orm_schema(engine):
    async with engine.connect() as conn:
        assert await conn.run_sync(_diff) == []
    assert await migrate.current_revision(engine) == migrate.head_revision()


async def test_downgrade_to_base_drops_everything(engine):
    await migrate.downgrade(engine, "base")
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert set(tables) <= {"alembic_version"}


def test_cli_db_upgrade_and_current(tmp_path, capsys):
    env = tmp_path / "e"
    env.write_text(
        f"CRA_LIBRARY_PATH=/c\nCRA_HISTORY_URL=sqlite+aiosqlite:///{tmp_path}/x.sqlite\n"
    )
    assert main(["--env-file", str(env), "db", "current"]) == 1
    assert main(["--env-file", str(env), "db", "upgrade"]) == 0
    assert "database at revision" in capsys.readouterr().out
    assert main(["--env-file", str(env), "db", "current"]) == 0
