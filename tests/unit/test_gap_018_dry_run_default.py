"""GAP-018: ``run``, ``phase run`` and ``ado-cleanup`` default to dry-run.

Each command used to declare ``--dry-run`` as ``is_flag=True, default=False``, so
an operator who typed nothing migrated for real. The option is now the boolean
pair ``--dry-run/--live`` with ``default=True``: no flag means no write, and only
``--live`` executes. Approved by operator instruction, 2026-09-13 (plan.md
§ Approved contract changes, entry 9).

Every case fakes the accelerator or the ADO client, so a regression that restores
the live default fails here instead of touching Azure DevOps or GitHub.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import ado2gh.api.accelerator as accelerator_module
import ado2gh.core.ado_cleanup as cleanup_module
import ado2gh.core.config_loader as config_loader_module
import ado2gh.reporting.reporter as reporter_module
import ado2gh.state.factory as state_factory_module
from ado2gh.cli import misc
from ado2gh.cli.main import cli
from ado2gh.models import ExecutionMode


class _FakeReporter:
    def __init__(self, state):
        self.state = state

    def print_wave_status(self, wave_id):
        return None

    def print_pipeline_status(self, wave_id):
        return None


@pytest.fixture
def wave_requests(monkeypatch):
    """Capture every RunWaveRequest ``ado2gh run`` would send, writing nothing."""
    seen = []

    class FakeAccelerator:
        def __init__(self, *, db_path):
            self.db_path = db_path

        def run_wave(self, request):
            seen.append(request)
            return SimpleNamespace(completed=0, failed=0)

    monkeypatch.setattr(accelerator_module, "Accelerator", FakeAccelerator)
    monkeypatch.setattr(
        config_loader_module.ConfigLoader, "load",
        staticmethod(lambda _path: ({}, [SimpleNamespace(wave_id=1)])),
    )
    monkeypatch.setattr(reporter_module, "Reporter", _FakeReporter)
    monkeypatch.setattr(state_factory_module, "create_state_db", lambda *_a, **_kw: object())
    return seen


@pytest.fixture
def phase_requests(monkeypatch):
    """Capture every PhaseRunRequest ``ado2gh phase run`` would send."""
    seen = []

    class FakeAccelerator:
        def __init__(self, *, db_path):
            self.db_path = db_path

        def run_phase(self, request):
            seen.append(request)
            return "preview"

    monkeypatch.setattr(accelerator_module, "Accelerator", FakeAccelerator)
    return seen


@pytest.fixture
def cleanup_modes(monkeypatch):
    """Capture the ExecutionMode ``ado2gh ado-cleanup`` builds its cleanup with."""
    seen = []

    class FakeCleanup:
        def __init__(self, ado, *, mode):
            self.ado = ado
            self.mode = mode

        def cleanup_repos(self, repos, *, archive_repo):
            seen.append(self.mode)

    monkeypatch.setattr(
        config_loader_module.ConfigLoader, "load", staticmethod(lambda _path: ({}, [])),
    )
    monkeypatch.setattr(misc, "load_clients", lambda *_a, **_kw: (object(), object()))
    monkeypatch.setattr(misc, "load_repos", lambda *_a, **_kw: [])
    monkeypatch.setattr(cleanup_module, "ADOCleanup", FakeCleanup)
    return seen


@pytest.mark.parametrize(
    ("extra", "expected"),
    [([], True), (["--dry-run"], True), (["--live"], False)],
)
def test_run_dry_run_default(wave_requests, extra, expected):
    result = CliRunner().invoke(cli, ["run", "-c", "migration.yaml", *extra])

    assert result.exit_code == 0, result.output
    assert [r.dry_run for r in wave_requests] == [expected]


@pytest.mark.parametrize(
    ("extra", "expected"),
    [([], True), (["--dry-run"], True), (["--live"], False)],
)
def test_phase_run_dry_run_default(phase_requests, extra, expected):
    result = CliRunner().invoke(
        cli, ["phase", "run", "-c", "migration_phase.yaml", "-p", "poc", *extra],
    )

    assert result.exit_code == 0, result.output
    assert [r.dry_run for r in phase_requests] == [expected]


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ([], ExecutionMode.DRY_RUN),
        (["--dry-run"], ExecutionMode.DRY_RUN),
        (["--live"], ExecutionMode.LIVE),
    ],
)
def test_ado_cleanup_dry_run_default(cleanup_modes, extra, expected):
    result = CliRunner().invoke(cli, ["ado-cleanup", "-c", "migration.yaml", *extra])

    assert result.exit_code == 0, result.output
    assert cleanup_modes == [expected]


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--dry-run"],
        ["run", "--live"],
        ["phase", "run", "--dry-run"],
        ["phase", "run", "--live"],
        ["ado-cleanup", "--dry-run"],
        ["ado-cleanup", "--live"],
    ],
)
def test_both_spellings_still_parse(argv):
    """Neither half of the pair is rejected as an unknown option."""
    result = CliRunner().invoke(cli, argv)

    assert result.exit_code == 2, result.output
    assert "no such option" not in result.output.lower()
    assert "Missing option" in result.output
