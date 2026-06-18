"""Tests for pipeline run result formatting."""
from ado2gh.api.run_reporting import (
    migrate_repo_detail,
    validation_message,
    validation_repo_detail,
)


def test_migrate_repo_detail_scope_failure():
    res = {
        "status": "partial",
        "scopes": {
            "repo": {"status": "completed", "detail": {"dry_run": True}},
            "pipelines": {"status": "failed", "detail": {"failed": 2, "total": 5}},
        },
        "errors": [],
    }
    detail = migrate_repo_detail("proj/my-repo", res)
    assert detail["status"] == "partial"
    assert "pipelines" in detail["summary"]
    assert detail["errors"]


def test_validation_repo_detail_fail_reason():
    row = {
        "ado_project": "P",
        "ado_repo": "R",
        "gh_target": "org/R",
        "overall": "FAIL",
        "checks": {
            "repo_exists": {"verdict": "FAIL", "detail": "Repository not found on GitHub"},
            "head_commit": {"verdict": "PASS", "detail": "match"},
        },
    }
    detail = validation_repo_detail(row)
    assert detail["primary_reason"] == "Repository not found on GitHub"
    assert len(detail["checks"]) == 2


def test_validation_repo_detail_pass_summary():
    row = {
        "ado_project": "azure-pipelines",
        "ado_repo": "hybrid-pipeline-template-migration",
        "gh_target": "org/hybrid-pipeline-template-migration",
        "overall": "PASS",
        "checks": {
            "repo_exists": {"verdict": "PASS", "detail": "Repository exists"},
            "head_commit": {"verdict": "PASS", "detail": "HEAD SHA match (abc123)"},
        },
    }
    detail = validation_repo_detail(row)
    assert detail["project"] == "azure-pipelines"
    assert detail["repo"] == "hybrid-pipeline-template-migration"
    assert "HEAD SHA match" in detail["primary_reason"]
    assert detail["message"] == detail["primary_reason"]


def test_validation_message_lists_failures():
    results = [
        {"ado_project": "A", "ado_repo": "r1", "overall": "PASS"},
        {"ado_project": "B", "ado_repo": "r2", "overall": "FAIL"},
    ]
    msg = validation_message(results)
    assert "1/2 passed" in msg
    assert "B/r2" in msg
