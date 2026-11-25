"""M0 placeholder: proves the package imports and the CLI resolves."""

from typer.testing import CliRunner

from copilot import __version__
from copilot.cli import app


def test_version_string_is_set() -> None:
    assert __version__


def test_cli_version_flag() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
