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


def test_token_issue_then_check(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("CRA_LIBRARY_PATH=/c\nCRA_MCP_TOKEN_SECRET=s3cret\n")
    assert main(["--env-file", str(env), "token", "issue", "a-colleague"]) == 0
    printed = capsys.readouterr()
    token = printed.out.strip()
    assert "a-colleague" in printed.err

    assert main(["--env-file", str(env), "token", "check", token]) == 0
    assert "a-colleague" in capsys.readouterr().out
    assert main(["--env-file", str(env), "token", "check", "nonsense"]) == 1


def test_a_token_needs_a_configured_secret(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("CRA_LIBRARY_PATH=/c\n")
    assert main(["--env-file", str(env), "token", "issue", "someone"]) == 1
    assert "secret" in capsys.readouterr().err
