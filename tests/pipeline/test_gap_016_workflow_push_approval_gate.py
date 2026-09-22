"""Regression check for register entry GAP-016 (GAP-PIPE-04) — live workflow-push approval gate is a no-op in both directions.

``push_repo_workflows`` guards every live push behind two booleans,
``readiness_ok`` and ``approver_ok``. Neither is wired to anything real, and the
two production callers disagree with the guard in opposite directions:

* **Engine direction.** ``PipelinesScopeHandler.migrate`` — the default
  ``phase run`` path — hands both call sites the literals
  ``readiness_ok=True, approver_ok=True``. The gate is therefore satisfied
  before it is asked, and the genuine auto/assisted/manual classification
  produced by ``PipelineReadinessReport`` is never consulted at push time. A
  pipeline with hard blockers (classified ``manual``) gets a branch and a pull
  request opened on the destination repo exactly like a clean ``auto`` one.

* **CLI direction.** The documented ``ado2gh push-workflows`` command exposes no
  flag for either parameter and calls ``push_workflows_for_repos`` without them,
  so the ``approver_ok: bool = False`` default applies and every *live*
  invocation returns ``"live workflow push requires approval"``. Nothing reads
  ``outcome["error"]``, so the operator is shown the success-shaped line
  ``"Pushed workflows for 0 repo(s)"`` and a zero exit code. The gate can never
  be satisfied and the real reason is discarded.

Both tests below assert the safety property, not the current plumbing: a live
push must either happen or explain itself, and a ``manual`` pipeline must not be
pushed live as though it had been reviewed and approved.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from ado2gh.cli.main import cli
from ado2gh.core.scopes.base import ScopeContext
from ado2gh.core.scopes.pipelines_scope import PipelinesScopeHandler
from ado2gh.models import (
    ExecutionMode,
    PipelineComplexity,
    PipelineMetadata,
    PipelineType,
    RepoConfig,
)
from ado2gh.reporting.pipeline_readiness import PipelineReadinessReport
from ado2gh.state.db import StateDB

WORKFLOW_YAML = "name: ci\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n"


def _repo() -> RepoConfig:
    return RepoConfig(
        ado_project="Proj",
        ado_repo="repo-a",
        gh_org="gh-org",
        gh_repo="repo-a",
        phase="poc",
        scopes=["pipelines"],
    )


def _manual_pipeline() -> PipelineMetadata:
    """A pipeline the real readiness classifier grades ``manual`` (hard blocker)."""
    meta = PipelineMetadata(
        project="Proj",
        pipeline_id=7,
        pipeline_name="deploy",
        pipeline_type=PipelineType.YAML,
        repo_name="repo-a",
        complexity=PipelineComplexity.COMPLEX,
    )
    meta.unsupported_tasks = ["AzureKeyVault@2"]
    return meta


def _gh_mock() -> MagicMock:
    gh = MagicMock()
    gh.get_default_branch.return_value = "main"
    gh.get_branch_sha.return_value = "0" * 40
    gh.get_file_sha.return_value = None
    gh.list_directory.return_value = []
    gh.create_pull_request.return_value = {
        "html_url": "https://github.com/gh-org/repo-a/pull/1",
    }
    return gh


def _seed_local_workflows(root):
    wf_dir = root / "gh-org" / "repo-a" / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "ci.yml").write_text(WORKFLOW_YAML)
    return wf_dir


def test_cli_push_workflows_default_run_labels_itself_as_preview(tmp_path):
    """CLI direction, preview (default) run: nothing pushed must read as a preview, not a silent no-op.

    Since the push-workflows opt-in change (commit a2d7a28), omitting ``--live``
    is a preview run by design — the zero-push, zero-exit-code outcome checked
    by the original GAP-016 report is now the intended default, so this test
    only has to confirm the output says so plainly instead of claiming success
    with no explanation. The paired test below,
    ``test_cli_push_workflows_live_does_not_report_blocked_push_as_zero_count_success``,
    passes ``--live`` to exercise the actual approval-gate bug GAP-016
    describes.
    """
    _seed_local_workflows(tmp_path / "workflows")
    gh = _gh_mock()

    runner = CliRunner()
    with (
        patch("ado2gh.cli.misc.load_clients", return_value=(MagicMock(), gh)),
        patch("ado2gh.cli.misc.load_repos", return_value=[_repo()]),
        patch(
            "ado2gh.core.config_loader.ConfigLoader.load",
            return_value=({}, []),
        ),
    ):
        result = runner.invoke(
            cli,
            [
                "push-workflows",
                "--config", str(tmp_path / "migration.yaml"),
                "--workflows-dir", str(tmp_path / "workflows"),
            ],
        )

    out = result.output
    pushed = gh.create_branch.called or gh.create_pull_request.called

    assert not pushed, (
        f"default push-workflows run must stay a preview: create_branch="
        f"{gh.create_branch.call_args_list} create_pull_request="
        f"{gh.create_pull_request.call_args_list}"
    )
    assert "dry run" in out.lower(), (
        "default push-workflows run must label itself as a preview so the "
        f"zero-push, zero-exit-code outcome is not read as a silent no-op: "
        f"exit_code={result.exit_code!r} output={out!r}"
    )


def test_cli_push_workflows_live_does_not_report_blocked_push_as_zero_count_success(
    tmp_path,
):
    """Same GAP-016 safety property, exercised through an explicit live run.

    A preview run is the default since the push-workflows opt-in change
    (commit a2d7a28), so this second test passes ``--live`` explicitly to
    reach the code path the approval gate actually guards; the first test
    above now covers the (separate, already-safe) preview-run default.
    """
    _seed_local_workflows(tmp_path / "workflows")
    gh = _gh_mock()

    runner = CliRunner()
    with (
        patch("ado2gh.cli.misc.load_clients", return_value=(MagicMock(), gh)),
        patch("ado2gh.cli.misc.load_repos", return_value=[_repo()]),
        patch(
            "ado2gh.core.config_loader.ConfigLoader.load",
            return_value=({}, []),
        ),
    ):
        result = runner.invoke(
            cli,
            [
                "push-workflows",
                "--config", str(tmp_path / "migration.yaml"),
                "--workflows-dir", str(tmp_path / "workflows"),
                "--live",
            ],
        )

    out = result.output
    pushed = gh.create_branch.called or gh.create_pull_request.called
    reason_visible = "approval" in out.lower() or "readiness" in out.lower()

    assert pushed or reason_visible or result.exit_code != 0, (
        "GAP-016: live `push-workflows` pushed nothing because of the "
        "approval gate, then reported a zero-count success and exit 0 without "
        f"naming the reason. exit_code={result.exit_code!r} output={out!r}"
    )


def test_manual_classified_pipeline_is_not_live_pushed_by_phase_run(tmp_path, monkeypatch):
    """Engine direction: a ``manual`` pipeline must not be live-pushed unreviewed.

    Encodes intended behaviour. The default ``phase run`` path hardcodes
    ``readiness_ok=True, approver_ok=True``, so today the branch and PR are
    created for a pipeline the readiness report grades ``manual``.
    """
    monkeypatch.setenv("ADO2GH_OUTPUT_DIR", str(tmp_path / "output"))
    _seed_local_workflows(tmp_path / "output" / "workflows")

    db = StateDB(str(tmp_path / "gap016.db"))
    meta = _manual_pipeline()
    db.upsert_pipeline_inventory(meta)

    # Ground truth from the real classifier, not an assumption.
    readiness = PipelineReadinessReport(db).generate()
    assert readiness["pipelines"][0]["classification"] == "manual"

    gh = _gh_mock()
    ctx = ScopeContext(global_cfg={}, ado=MagicMock(), gh=gh, db=db, mode=ExecutionMode.LIVE)

    with patch.object(
        PipelinesScopeHandler,
        "_do_transform",
        return_value={"pipeline_id": meta.pipeline_id, "status": "completed"},
    ):
        PipelinesScopeHandler().migrate(_repo(), ctx, wave_id=1)

    assert not gh.create_branch.called and not gh.create_pull_request.called, (
        "GAP-016: `phase run` opened a branch/PR on the destination repo for a "
        "pipeline the readiness report classifies as `manual`; the live-push "
        "gate is satisfied by hardcoded True literals rather than a real signal. "
        f"create_branch={gh.create_branch.call_args_list} "
        f"create_pull_request={gh.create_pull_request.call_args_list}"
    )
