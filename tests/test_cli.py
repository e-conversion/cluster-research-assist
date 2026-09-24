import asyncio

import pytest

from cra import __version__
from cra.cli import main


def test_version_flag_prints_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"cra {__version__}"


def test_no_command_prints_help_and_fails(capsys):
    assert main([]) == 2
    assert "usage: cra" in capsys.readouterr().out


def test_check_config_prints_redacted_settings(tmp_path, capsys):
    env = tmp_path / "my.env"
    env.write_text("CRA_LIBRARY_PATH=/c\nCRA_LLM_API_KEY=secret\n")
    assert main(["--env-file", str(env), "check-config"]) == 0
    out = capsys.readouterr().out
    assert "CRA_LIBRARY_PATH=/c" in out
    assert "CRA_LLM_API_KEY=***" in out
    assert "secret" not in out


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("CRA_LIBRARY_PATH=/c\nCRA_AUTH_PROVIDER=oidc\n", "CRA_OIDC_ISSUER"),
        (
            "CRA_LIBRARY_PATH=/c\nCRA_LLM_MODLE=x\n",
            "unknown configuration keys: CRA_LLM_MODLE",
        ),
    ],
    ids=["invalid", "typo"],
)
def test_check_config_fails_with_exit_2(tmp_path, capsys, content, message):
    env = tmp_path / ".env"
    env.write_text(content)
    with pytest.raises(SystemExit) as exc:
        main(["--env-file", str(env), "check-config"])
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_library_manifest_then_check(tmp_path, capsys):
    from library_builder import write_library

    directory = write_library(tmp_path / "c", with_manifest=False)
    env = tmp_path / "e"
    env.write_text(f"CRA_LIBRARY_PATH={directory}\n")
    assert main(["--env-file", str(env), "library", "check"]) == 1
    assert "manifest.json missing" in capsys.readouterr().err
    assert main(["--env-file", str(env), "library", "manifest"]) == 0
    assert main(["--env-file", str(env), "library", "check", str(directory)]) == 0
    assert "papers       3" in capsys.readouterr().out


def an_account(env, name: str) -> str:
    """A migrated database with one account in it, and that account's id."""
    from cra.app.history.engine import make_engine, make_session_factory
    from cra.app.history.repository import Repository

    assert main(["--env-file", str(env), "db", "upgrade"]) == 0

    async def create() -> str:
        engine = make_engine(f"sqlite+aiosqlite:///{env.parent}/cra.sqlite")
        user = await Repository(make_session_factory(engine)).create_user(name)
        await engine.dispose()
        return user.id

    return asyncio.run(create())


def test_a_token_issued_on_the_command_line_can_be_listed_and_revoked(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text(
        f"CRA_LIBRARY_PATH=/c\nCRA_HISTORY_URL=sqlite+aiosqlite:///{tmp_path}/cra.sqlite\n"
    )
    user_id = an_account(env, "Ada")
    capsys.readouterr()
    cli = ["--env-file", str(env), "token"]

    assert main([*cli, "issue", user_id, "--label", "lab laptop"]) == 0
    printed = capsys.readouterr()
    assert printed.out.startswith("cra1_")
    token_id = printed.err.split()[1]

    assert main([*cli, "list"]) == 0
    listed = capsys.readouterr().out
    assert token_id in listed
    assert "active" in listed
    assert printed.out.strip() not in listed

    assert main([*cli, "revoke", token_id]) == 0
    assert main([*cli, "list", user_id]) == 0
    assert "revoked" in capsys.readouterr().out
