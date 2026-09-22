"""Regression check for register entry GAP-013 (GAP-ENG-01) — the workflow-integrity check must be able to report FAIL.

Reproduction from the gap register: the producer emits flat workflow paths of the
shape ``.github/workflows/<name>.yml`` (pinned by
``tests/contract/test_step_contracts.py:128``). In
``ado2gh/reporting/post_migration_validator.py:254-255`` the check strips the
``.github/workflows/`` prefix and then guards the target lookup with
``if len(parts) >= 2:``. Stripping the prefix off a flat path leaves exactly one
element, so the guard is never true, the Contents-API existence call at ``:257``
never runs, ``missing_files`` at ``:259`` can never be appended to, and the
integrity result stays PASS. Independently, the enclosing ``if gh_count >=
ado_count:`` branch at ``:266-269`` returns a literal PASS verdict regardless of
what the integrity result says.

The scenario below is a repo where GitHub reports enough workflows to satisfy the
count comparison, but the specific workflow file the migration generated is
genuinely absent at the target (its Contents-API read 404s). Post-migration
validation must report FAIL for that repo. On current code it reports PASS, so
these tests fail — which is the point.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from ado2gh.models import RepoConfig
from ado2gh.reporting.post_migration_validator import FAIL, PostMigrationValidator

# The real producer contract: one flat segment after the ".github/workflows/" prefix.
WORKFLOW_FILES = [
    {
        "repo": "org/repo",
        "output_path": ".github/workflows/build-ci.yml",
        "commit_sha": "abc123",
    }
]


def _repo() -> RepoConfig:
    # "pipelines" scope is what makes _validate_one call _check_workflows at all.
    return RepoConfig(
        ado_project="org",
        ado_repo="repo",
        gh_org="org",
        gh_repo="repo",
        scopes=["pipelines"],
    )


def _validator() -> PostMigrationValidator:
    """Validator whose every non-workflow check passes and whose target is missing
    the generated workflow file.

    ``list_workflows`` returns one unrelated workflow so that ``gh_count >=
    ado_count`` holds — the count comparison is satisfied while the file the
    migration actually produced is absent. ``_get`` raises for the Contents API,
    which is how a 404 surfaces through ``GHClient``.
    """
    ado = MagicMock()
    ado.get_repo.return_value = {"defaultBranch": "refs/heads/main", "id": "repo-id"}
    ado.get_repo_commits.return_value = [{"commitId": "abc123"}]
    ado.get_repo_stats.return_value = {"branch_count": 1}

    gh = MagicMock()
    gh.repo_exists.return_value = True
    gh.get_repo.return_value = {"default_branch": "main"}
    gh.list_branches.return_value = [{"name": "main", "commit": {"sha": "abc123"}}]
    gh.list_workflows.return_value = [{"name": "unrelated.yml"}]
    gh._get.side_effect = Exception("404 Not Found")

    db = MagicMock()
    db.inventory_count_for_repo.return_value = 1

    return PostMigrationValidator(ado=ado, gh=gh, db=db)


def test_workflow_check_reports_fail_when_generated_workflow_absent_at_target():
    """The workflow check itself must report FAIL when a generated workflow file
    is missing at the GitHub target, and must name the missing file."""
    result = _validator()._check_workflows(_repo(), WORKFLOW_FILES)

    integrity = result.get("workflow_integrity", {})
    assert integrity.get("verdict") == FAIL, (
        "workflow integrity reported "
        f"{integrity.get('verdict')!r} for a workflow file absent at the target"
    )
    assert ".github/workflows/build-ci.yml" in str(integrity), (
        "the missing workflow file is not named in the integrity result"
    )
    assert result["verdict"] == FAIL, (
        "the workflows check returned "
        f"{result['verdict']!r} even though its integrity result failed"
    )


def test_validation_overall_is_fail_when_generated_workflow_absent_at_target():
    """The operator-visible verdict (what CLI `report` and run_reporting show)
    must be FAIL for a repo whose generated workflow never landed at the target."""
    result = _validator()._validate_one(_repo(), workflow_files=WORKFLOW_FILES)

    assert result["overall"] == FAIL, (
        "post-migration validation reported "
        f"{result['overall']!r} for a repo with a missing workflow file"
    )
