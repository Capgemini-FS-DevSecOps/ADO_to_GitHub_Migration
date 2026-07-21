from click.testing import CliRunner
import pytest

from ado2gh.cli import cli


@pytest.mark.parametrize(
    "arguments",
    [
        ["run", "--config", "does-not-exist.yml"],
        ["phase", "run", "--config", "does-not-exist.yml", "--phase", "poc"],
        [
            "pipelines", "retry-failed", "--config", "does-not-exist.yml",
            "--wave", "1",
        ],
        ["push-workflows", "--config", "does-not-exist.yml"],
    ],
)
def test_legacy_live_mutators_cannot_bypass_pev(arguments):
    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code != 0
    assert "Live '" in result.output
    assert "ado2gh agent plan" in result.output

