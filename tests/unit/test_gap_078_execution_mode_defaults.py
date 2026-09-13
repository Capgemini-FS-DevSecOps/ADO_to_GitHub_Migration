"""GAP-078: every remaining `ExecutionMode` parameter defaults to dry-run.

Ten signatures beyond the two GAP-068 records still declared
`mode: ExecutionMode = ExecutionMode.LIVE`, so a caller that omitted the argument
got a live wave, a live phase batch, a live workflow push, a live ADO cleanup or
a `wave_runs` row recorded as live. All of them now default to
`ExecutionMode.DRY_RUN`; every shipped call site that must stay live passes
`mode=` explicitly.

Approved by operator instruction, 2026-09-13 (operator-decisions.md § 1 settles
the same CA-001 policy for GAP-018, GAP-068 and GAP-078).

`mark_wave_run` is checked on both backends and in both modes, because the
persisted `dry_run` column must keep its meaning exactly: the default changes
what an omitted argument records, never how a stated one is serialised.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from ado2gh.core.ado_cleanup import ADOCleanup
from ado2gh.core.scopes.base import ScopeContext
from ado2gh.core.wave_runner import WaveRunner
from ado2gh.models import ExecutionMode, RepoConfig, WaveConfig
from ado2gh.phase.batch_executor import BatchExecutor
from ado2gh.pipelines.inventory import PipelineInventoryBuilder
from ado2gh.pipelines.push_workflows import push_repo_workflows, push_workflows_for_repos
from ado2gh.state.base import StateDBBase
from ado2gh.state.postgres_db import PostgresStateDB
from ado2gh.state.sqlite_db import SQLiteStateDB

# Every signature GAP-078 lists, as (callable, parameter name).
DEFAULTED = [
    (ADOCleanup.__init__, "mode"),
    (WaveRunner.run_wave, "mode"),
    (BatchExecutor.execute_phase, "mode"),
    (BatchExecutor.execute_wave, "mode"),
    (PipelineInventoryBuilder.__init__, "mode"),
    (push_repo_workflows, "mode"),
    (push_workflows_for_repos, "mode"),
    (StateDBBase.mark_wave_run, "mode"),
    (SQLiteStateDB.mark_wave_run, "mode"),
    (PostgresStateDB.mark_wave_run, "mode"),
]


@pytest.mark.parametrize(
    ("func", "param"), DEFAULTED, ids=[f.__qualname__ for f, _ in DEFAULTED],
)
def test_signature_defaults_to_dry_run(func, param):
    assert inspect.signature(func).parameters[param].default is ExecutionMode.DRY_RUN


def test_scope_context_defaults_to_dry_run():
    ctx = ScopeContext(global_cfg={}, ado=MagicMock(), gh=MagicMock(), db=MagicMock())

    assert ctx.mode is ExecutionMode.DRY_RUN


def test_ado_cleanup_without_mode_disables_nothing():
    """The default cleanup previews and never calls an ADO write."""
    ado = MagicMock()
    ado.list_build_definitions.return_value = [{"id": 1, "name": "ci"}]
    repo = RepoConfig(ado_project="P", ado_repo="r1", gh_org="o", gh_repo="r1")

    ADOCleanup(ado).cleanup_repos([repo], archive_repo=True)

    ado.disable_build_definition.assert_not_called()
    ado.update_repo.assert_not_called()


def test_wave_runner_without_mode_forwards_dry_run():
    runner = WaveRunner(MagicMock(), MagicMock())
    runner._executor = MagicMock()
    wave = WaveConfig(wave_id=1, name="w1", description="", repos=[])

    runner.run_wave(wave)

    runner._executor.execute_wave.assert_called_once_with(
        wave, mode=ExecutionMode.DRY_RUN,
    )


def test_inventory_builder_without_mode_writes_no_rows(tmp_path):
    """A scan that is not asked to persist leaves pipeline_inventory empty."""
    db = SQLiteStateDB(str(tmp_path / "inv.db"))
    ado = MagicMock()
    ado.list_build_definitions.return_value = [{"id": 1, "name": "ci", "path": "\\"}]
    ado.list_release_definitions.return_value = []

    PipelineInventoryBuilder(ado, db, parallel=1).build_for_projects(["P"])

    assert db.inventory_count() == 0


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(ExecutionMode.DRY_RUN, 1), (ExecutionMode.LIVE, 0)],
)
def test_mark_wave_run_persists_the_mode_it_is_given(tmp_path, mode, expected):
    """The stored `dry_run` column keeps its polarity for an explicit mode."""
    db = SQLiteStateDB(str(tmp_path / "waves.db"))

    run_id = db.mark_wave_run(1, "started", mode)

    with db._conn() as conn:
        row = conn.execute(
            "SELECT dry_run FROM wave_runs WHERE id=?", (run_id,),
        ).fetchone()
    assert row["dry_run"] == expected


def test_mark_wave_run_without_mode_records_a_dry_run(tmp_path):
    db = SQLiteStateDB(str(tmp_path / "waves.db"))

    run_id = db.mark_wave_run(1, "started")

    with db._conn() as conn:
        row = conn.execute(
            "SELECT dry_run FROM wave_runs WHERE id=?", (run_id,),
        ).fetchone()
    assert row["dry_run"] == 1


def test_push_workflows_without_mode_pushes_nothing(tmp_path):
    """The default multi-repo push creates no branch and opens no pull request."""
    gh = MagicMock()
    repo = RepoConfig(ado_project="P", ado_repo="r1", gh_org="o", gh_repo="r1")
    (tmp_path / "r1").mkdir()
    (tmp_path / "r1" / "ci.yml").write_text("on: push\n", encoding="utf-8")

    push_workflows_for_repos(gh, [repo], str(tmp_path))

    gh.create_branch.assert_not_called()
    gh.create_pull_request.assert_not_called()
