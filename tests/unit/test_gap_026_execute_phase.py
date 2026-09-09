"""GAP-026 (GAP-PHASE-03) — ``BatchExecutor.execute_phase`` has no test coverage.

Reproduction, in plain English:

``execute_phase`` is the one function that fans a phase out into fixed-size
batches, checkpoints each batch into ``batch_checkpoints`` so an interrupted run
resumes after the last completed one, and keeps a dry run from persisting
anything a later live run would then skip. The phase test files cover risk
scoring, wave assignment and gate thresholds; the executor loop itself was never
called by a test, so a regression in batch placement, in the resume cursor or in
the dry-run guard would ship green.

The tests below assert the behaviour, not the spelling: batch boundaries are
derived from ``DEFAULT_PHASES[...].batch_size`` rather than hard-coded, and the
batches actually executed are read off the engine calls, not off the summary
counters, so a summary that lies about its own work still fails.

A note on the gate, because the register's blast_radius line is misleading:
``execute_phase`` does **not** evaluate a gate between batches. The gate is
evaluated once, ahead of the fan-out, by ``Accelerator.run_phase`` — it is the
*prior* phase's gate, and while it blocks, no batch of the next phase runs at
all. ``test_a_blocking_gate_stops_every_batch`` asserts that real property
through the real caller with a real ``BatchExecutor`` behind it.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from ado2gh.api import accelerator as accelerator_module
from ado2gh.api.accelerator import Accelerator
from ado2gh.api.contracts import PhaseRunRequest
from ado2gh.api.errors import ConfigurationError
from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.models import (
    DEFAULT_PHASES,
    BatchCheckpoint,
    ExecutionMode,
    MigrationStatus,
    PhaseType,
    RepoConfig,
    RiskScore,
    WaveConfig,
)
from ado2gh.phase.batch_executor import BatchExecutor
from ado2gh.phase.gate_checker import PhaseGateChecker
from ado2gh.phase.progress_tracker import ProgressTracker
from ado2gh.state.db import StateDB
from ado2gh.state.factory import create_state_db

BATCH_SIZE = DEFAULT_PHASES[PhaseType.POC].batch_size
REPO_COUNT = BATCH_SIZE * 2 + BATCH_SIZE // 2  # deliberately not a whole multiple
EXPECTED_BATCHES = 3


def _repos(count: int, prefix: str = "repo") -> list[RepoConfig]:
    """Build ``count`` repo configs named ``<prefix>NN``, in order."""
    return [
        RepoConfig(ado_project="Contoso", ado_repo=f"{prefix}{i:02d}",
                   gh_org="fake-org", gh_repo=f"{prefix}{i:02d}", scopes=["repo"])
        for i in range(count)
    ]


def _wave(wave_id: int, phase: PhaseType, repos: list[RepoConfig]) -> WaveConfig:
    """Build a wave assigned to ``phase``."""
    return WaveConfig(
        wave_id=wave_id, name=f"w{wave_id}", description="",
        repos=repos, parallel=2, pipeline_parallel=2, phase=phase.value,
    )


def _batches_seen(calls: list[tuple[int, str]]) -> list[set[str]]:
    """Group the repos handed to the engine by the wave id minted per batch.

    ``execute_phase`` numbers batch waves ``base_wave_id + batch_num``, so the
    sorted grouping is the batch sequence as executed — independent of the order
    the thread pool happens to finish repos in.
    """
    grouped: dict[int, set[str]] = {}
    for wave_id, repo in calls:
        grouped.setdefault(wave_id, set()).add(repo)
    return [grouped[key] for key in sorted(grouped)]


def _slices(repos: list[RepoConfig], size: int) -> list[set[str]]:
    """The repo names as contiguous slices of ``size``, in order."""
    return [{r.ado_repo for r in repos[i:i + size]} for i in range(0, len(repos), size)]


def _checkpoints(db: StateDB, phase: PhaseType) -> list[dict]:
    """Every ``batch_checkpoints`` row of a phase, ordered by batch number."""
    with db._conn() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM batch_checkpoints WHERE phase=? ORDER BY batch_num",
            (phase.value,),
        ).fetchall()]


def _wave_runs(db: StateDB) -> list[dict]:
    """Every ``wave_runs`` row — the live-effect a dry run must not leave behind."""
    with db._conn() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM wave_runs").fetchall()]


@pytest.fixture
def executor_env(tmp_path):
    """A real ``BatchExecutor`` on a temp SQLite DB with the migration engine stubbed.

    Every ``migrate_repo`` call is recorded as ``(wave_id, ado_repo)`` so a test
    can reconstruct which repos ran in which batch.
    """
    db = StateDB(str(tmp_path / "gap026.db"))
    calls: list[tuple[int, str]] = []
    lock = threading.Lock()

    def _migrate(wave_id, repo, *_args, **_kwargs):
        with lock:
            calls.append((wave_id, repo.ado_repo))
        return {"status": "completed", "scopes": {}, "errors": []}

    engine = MagicMock(spec=MigrationEngine)
    engine.migrate_repo.side_effect = _migrate
    executor = BatchExecutor(engine, db, ProgressTracker(REPO_COUNT, 1))
    return SimpleNamespace(db=db, engine=engine, calls=calls, executor=executor)


def test_phase_is_split_into_batches_of_the_configured_size(executor_env):
    """A wave larger than the batch size runs as consecutive batches of that size."""
    repos = _repos(REPO_COUNT)
    summary = executor_env.executor.execute_phase(
        PhaseType.POC, [_wave(1, PhaseType.POC, repos)])

    assert summary["batches_run"] == EXPECTED_BATCHES
    assert summary["batches_skipped"] == 0
    assert summary["completed"] == REPO_COUNT
    assert summary["failed"] == 0

    seen = _batches_seen(executor_env.calls)
    assert [len(batch) for batch in seen] == [BATCH_SIZE, BATCH_SIZE, BATCH_SIZE // 2]
    assert seen == _slices(repos, BATCH_SIZE), \
        "batch boundaries moved: repos are no longer batched in config order"


def test_only_waves_of_the_requested_phase_are_batched(executor_env):
    """Repos come from every wave of the phase, in wave order, and from no other phase."""
    poc = _repos(BATCH_SIZE + 2, "poc")
    pilot = _repos(3, "pilot")
    waves = [
        _wave(5, PhaseType.PILOT, pilot),
        _wave(2, PhaseType.POC, poc[:7]),
        _wave(3, PhaseType.POC, poc[7:]),
    ]

    summary = executor_env.executor.execute_phase(PhaseType.POC, waves)

    assert summary["batches_run"] == 2
    assert summary["completed"] == len(poc)
    assert _batches_seen(executor_env.calls) == _slices(poc, BATCH_SIZE)
    assert not any(name.startswith("pilot") for _, name in executor_env.calls), \
        "a wave belonging to another phase was executed"


def test_phase_with_no_waves_runs_nothing(executor_env):
    """A phase nothing is assigned to returns a zeroed summary and touches no repo."""
    summary = executor_env.executor.execute_phase(
        PhaseType.WAVE3, [_wave(1, PhaseType.POC, _repos(3))])

    assert summary == {"phase": "wave3", "completed": 0, "failed": 0,
                       "batches_run": 0, "batches_skipped": 0}
    assert executor_env.calls == []


def test_a_checkpoint_is_written_for_every_batch(executor_env):
    """Each batch leaves one completed checkpoint row, and the resume cursor moves with it."""
    repos = _repos(REPO_COUNT)
    executor_env.executor.execute_phase(PhaseType.POC, [_wave(1, PhaseType.POC, repos)])

    rows = _checkpoints(executor_env.db, PhaseType.POC)
    assert [row["batch_num"] for row in rows] == list(range(EXPECTED_BATCHES))
    assert [row["repos_total"] for row in rows] == [BATCH_SIZE, BATCH_SIZE, BATCH_SIZE // 2]
    assert [row["repos_done"] for row in rows] == [BATCH_SIZE, BATCH_SIZE, BATCH_SIZE // 2]
    assert {row["status"] for row in rows} == {"completed"}
    assert all(row["total_batches"] == EXPECTED_BATCHES for row in rows)
    assert all(row["started_at"] and row["completed_at"] for row in rows)
    assert executor_env.db.get_last_completed_batch(PhaseType.POC) == EXPECTED_BATCHES - 1


def test_resume_skips_the_batches_already_checkpointed(executor_env):
    """A run resuming after batch 0 migrates the later batches only, never batch 0 again."""
    repos = _repos(REPO_COUNT)
    executor_env.db.upsert_batch_checkpoint(BatchCheckpoint(
        phase=PhaseType.POC, batch_num=0, total_batches=EXPECTED_BATCHES,
        repos_done=BATCH_SIZE, repos_total=BATCH_SIZE, status="completed",
        started_at="2026-09-09T00:00:00+00:00", completed_at="2026-09-09T00:01:00+00:00",
    ))

    summary = executor_env.executor.execute_phase(
        PhaseType.POC, [_wave(1, PhaseType.POC, repos)])

    assert summary["batches_skipped"] == 1
    assert summary["batches_run"] == EXPECTED_BATCHES - 1
    assert summary["completed"] == REPO_COUNT - BATCH_SIZE
    assert _batches_seen(executor_env.calls) == _slices(repos, BATCH_SIZE)[1:]

    already_done = {r.ado_repo for r in repos[:BATCH_SIZE]}
    assert not any(name in already_done for _, name in executor_env.calls), \
        "a checkpointed batch was migrated a second time"

    # Re-running a phase whose every batch is checkpointed does no work at all.
    executor_env.calls.clear()
    again = executor_env.executor.execute_phase(
        PhaseType.POC, [_wave(1, PhaseType.POC, repos)])
    assert again["batches_run"] == 0
    assert again["batches_skipped"] == EXPECTED_BATCHES
    assert executor_env.calls == []


def test_dry_run_persists_nothing_and_leaves_the_live_run_all_its_work(executor_env):
    """DRY_RUN previews every batch but writes no checkpoint and opens no wave run."""
    repos = _repos(REPO_COUNT)
    waves = [_wave(1, PhaseType.POC, repos)]

    preview = executor_env.executor.execute_phase(
        PhaseType.POC, waves, mode=ExecutionMode.DRY_RUN)

    assert preview["batches_run"] == EXPECTED_BATCHES
    assert _checkpoints(executor_env.db, PhaseType.POC) == [], \
        "a dry run checkpointed batches, so a later live run would skip them"
    assert _wave_runs(executor_env.db) == [], "a dry run recorded a live wave run"
    assert executor_env.db.get_last_completed_batch(PhaseType.POC) == -1

    executor_env.calls.clear()
    live = executor_env.executor.execute_phase(PhaseType.POC, waves, mode=ExecutionMode.LIVE)

    assert live["batches_skipped"] == 0
    assert live["batches_run"] == EXPECTED_BATCHES
    assert _batches_seen(executor_env.calls) == _slices(repos, BATCH_SIZE)
    assert len(_checkpoints(executor_env.db, PhaseType.POC)) == EXPECTED_BATCHES
    assert _wave_runs(executor_env.db) != []


@pytest.fixture
def gated_phase(tmp_path, monkeypatch):
    """``Accelerator.run_phase`` over a real ``BatchExecutor``, with the POC gate failing.

    Only the outside world is stubbed — ADO and GitHub clients and the migration
    engine. The gate checker, the executor and the state DB are the real ones.
    """
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ADO2GH_SQLITE_PATH", raising=False)

    pilot_repos = _repos(3, "pilot")
    config_path = tmp_path / "migration.yaml"
    config_path.write_text(yaml.safe_dump({
        "global": {"gh_org": "fake-org"},
        "waves": [{
            "wave_id": 1, "name": "pilot-w1", "phase": "pilot",
            "repos": [{"ado_project": r.ado_project, "ado_repo": r.ado_repo}
                      for r in pilot_repos],
        }],
    }), encoding="utf-8")

    db_path = str(tmp_path / "gap026_gate.db")
    db = create_state_db(db_path)
    db.upsert_risk_score(RiskScore(
        project="Contoso", repo_name="payments", total_score=10,
        assigned_phase=PhaseType.POC, gh_org="fake-org", gh_repo="payments",
    ))
    db.upsert_migration(
        1,
        RepoConfig(ado_project="Contoso", ado_repo="payments",
                   gh_org="fake-org", gh_repo="payments"),
        "repo",
        MigrationStatus.FAILED,
    )

    engine_cls = MagicMock(name="MigrationEngine")
    engine_cls.return_value.migrate_repo.return_value = {
        "status": "completed", "scopes": {}, "errors": [],
    }
    monkeypatch.setattr(accelerator_module, "_build_ado_client", lambda *a, **k: MagicMock())
    monkeypatch.setattr(accelerator_module, "_build_gh_client", lambda *a, **k: MagicMock())
    monkeypatch.setattr(accelerator_module, "MigrationEngine", engine_cls)

    return SimpleNamespace(config=str(config_path), db_path=db_path, db=db,
                           engine=engine_cls.return_value, repo_count=len(pilot_repos))


def test_a_blocking_gate_stops_every_batch(gated_phase):
    """While the prior phase's gate blocks, no batch runs; an audited override releases them."""
    accel = Accelerator(db_path=gated_phase.db_path)
    request = PhaseRunRequest(
        config_path=gated_phase.config, phase="pilot",
        dry_run=False, force=False, db_path=gated_phase.db_path,
    )

    with pytest.raises(ConfigurationError):
        accel.run_phase(request)
    assert gated_phase.engine.migrate_repo.call_count == 0, \
        "repos were migrated even though the prior gate blocks"
    assert _checkpoints(gated_phase.db, PhaseType.PILOT) == []

    PhaseGateChecker(gated_phase.db).override(PhaseType.POC, "GAP-026: failures accepted")
    result = accel.run_phase(request)

    assert result.batches_run == 1
    assert result.completed == gated_phase.repo_count
    assert gated_phase.engine.migrate_repo.call_count == gated_phase.repo_count
    assert [row["batch_num"] for row in _checkpoints(gated_phase.db, PhaseType.PILOT)] == [0]
