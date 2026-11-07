import pytest

import pysheets
from pysheets.cli import main


def test_version():
    assert pysheets.__version__ == "0.1.0"


def test_cli_runs(capsys):
    assert main([]) == 0
    assert "pysheets" in capsys.readouterr().out


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out
