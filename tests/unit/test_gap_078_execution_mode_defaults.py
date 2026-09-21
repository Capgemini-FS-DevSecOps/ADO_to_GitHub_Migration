"""Every remaining `ExecutionMode` parameter now defaults to a preview run rather than a real one.

Ten signatures beyond the two already fixed under an earlier, separate register
entry still declared `mode: ExecutionMode = ExecutionMode.LIVE`, so a caller
that omitted the argument got a live wave, a live phase batch, a live workflow
push, a live ADO cleanup or a `wave_runs` row recorded as live. All of them now
default to `ExecutionMode.DRY_RUN`; every shipped call site that must stay live
passes `mode=` explicitly.

Approved by operator instruction, 2026-09-13 (operator-decisions.md § 1 settles
the same policy — a preview run is the default and a real run needs an explicit
opt-in — across the register entries for the command-line default flip, the two
signatures fixed earlier and the ten fixed here; register cross-reference:
CA-001, GAP-018, GAP-068, GAP-078).

`mark_wave_run` is checked on both backends and in both modes, because the
persisted `dry_run` column must keep its meaning exactly: the default changes
what an omitted argument records, never how a stated one is serialised.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

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
from tests.integration.test_ado_cleanup_journey import REPO as CLEANUP_REPO
from tests.integration.test_ado_cleanup_journey import _ado, _mutating_calls
from tests.state.test_postgres_db_stub_cursor import FAKE_DSN, StubConnection, StubCursor

# Every signature this fix covers, as (callable, parameter name) (register
# cross-reference: GAP-078).
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
    """The default cleanup previews and makes no mutating ADO call: a preview
    run is the default and a real run needs an explicit opt-in (register
    cross-reference: CA-001).

    ``ado.disable_build_definition``/``update_repo`` are not the methods the
    production code calls (``_disable_pipelines`` PUTs, ``_add_redirect_readme``
    POSTs, ``_archive_repo`` PATCHes — all through ``ado.session``), so asserting
    on those two names never actually observed a write. ``_mutating_calls``
    checks the real session verbs instead.
    """
    ado = _ado()

    ADOCleanup(ado).cleanup_repos([CLEANUP_REPO], archive_repo=True)

    assert _mutating_calls(ado) == []


def test_ado_cleanup_with_live_mode_calls_ado():
    """Positive control: explicit LIVE reaches every mutating ADO call the default test proves absent."""
    ado = _ado()

    ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [CLEANUP_REPO], archive_repo=True,
    )

    assert sorted(_mutating_calls(ado)) == ["patch", "post", "put"]


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


def test_mark_wave_run_persists_the_mode_it_is_given_on_postgres():
    """The same `dry_run` polarity, pinned against the PostgreSQL backend's own SQL.

    ``psycopg2.connect`` stays patched for the whole test: ``PostgresStateDB``
    opens a fresh connection per call rather than caching one, so a patch that
    closed after construction would let ``mark_wave_run`` reach a real socket.
    """
    cursor = StubCursor()
    with patch("psycopg2.connect", return_value=StubConnection(cursor)):
        store = PostgresStateDB(FAKE_DSN)
        cursor.executed.clear()
        cursor.rows = [(42,)]

        run_id = store.mark_wave_run(1, "started", ExecutionMode.LIVE)

    assert run_id == 42
    _, params = cursor.statement("INSERT INTO wave_runs")
    assert params[-1] == 0


def test_mark_wave_run_without_mode_records_a_dry_run_on_postgres():
    cursor = StubCursor()
    with patch("psycopg2.connect", return_value=StubConnection(cursor)):
        store = PostgresStateDB(FAKE_DSN)
        cursor.executed.clear()
        cursor.rows = [(7,)]

        run_id = store.mark_wave_run(1, "started")

    assert run_id == 7
    _, params = cursor.statement("INSERT INTO wave_runs")
    assert params[-1] == 1


def test_push_workflows_without_mode_pushes_nothing(tmp_path):
    """The default multi-repo push creates no branch and opens no pull request.

    The fixture must sit where ``push_repo_workflows`` actually looks
    (``<workflows_dir>/<gh_org>/<gh_repo>/.github/workflows``, per
    ``push_workflows.py``) — a workflow file anywhere else makes ``wf_root``
    not exist and the function returns on that early guard, never reaching the
    ``DRY_RUN`` branch this test means to cover.
    """
    gh = MagicMock()
    repo = RepoConfig(ado_project="P", ado_repo="r1", gh_org="o", gh_repo="r1")
    wf_root = tmp_path / repo.gh_org / repo.gh_repo / ".github" / "workflows"
    wf_root.mkdir(parents=True)
    (wf_root / "ci.yml").write_text("on: push\n", encoding="utf-8")

    count = push_workflows_for_repos(gh, [repo], str(tmp_path))

    assert count == 0
    gh.create_branch.assert_not_called()
    gh.create_pull_request.assert_not_called()

    # Same default, called directly for the preview details a repo count can't show.
    preview = push_repo_workflows(gh, repo, str(tmp_path))
    assert preview["dry_run"] is True
    assert preview["pushed"] is False
    assert preview["workflow_files"] == ["ci.yml"]


def test_push_workflows_live_mode_pushes(tmp_path):
    """Positive control: explicit LIVE with clean readiness pushes for real."""
    gh = MagicMock()
    gh.get_default_branch.return_value = "main"
    gh.get_branch_sha.return_value = "abc1234"
    gh.create_pull_request.return_value = {"html_url": "https://github.example/o/r1/pull/1"}
    repo = RepoConfig(ado_project="P", ado_repo="r1", gh_org="o", gh_repo="r1")
    wf_root = tmp_path / repo.gh_org / repo.gh_repo / ".github" / "workflows"
    wf_root.mkdir(parents=True)
    (wf_root / "ci.yml").write_text("on: push\n", encoding="utf-8")
    # An empty inventory has no pipeline graded "manual", so readiness reports
    # no blockers and the live push is not gated (pipeline_readiness.py).
    db = SQLiteStateDB(str(tmp_path / "ready.db"))

    count = push_workflows_for_repos(
        gh, [repo], str(tmp_path), mode=ExecutionMode.LIVE, db=db,
    )

    assert count == 1
    gh.create_branch.assert_called_once()
    gh.create_pull_request.assert_called_once()
