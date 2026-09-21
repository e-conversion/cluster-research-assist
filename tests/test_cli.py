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
