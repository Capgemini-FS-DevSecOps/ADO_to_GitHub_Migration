"""Tests for the migration status dashboard report builder."""
from __future__ import annotations

from ado2gh.api.migration_status_report import (
    _collect_repo_scope_rows,
    _rollup_repo_status,
    build_migration_status_report,
    extract_outcomes_from_pipeline_runs,
)


class _FakeDB:
    """Minimal StateStore stand-in exposing only the two methods read here."""

    def __init__(self, rows=None, counts=None):
        self._rows = rows or []
        self._counts = counts or {}

    def get_all_migrations(self):
        return self._rows

    def get_migration_repo_counts(self):
        return self._counts


class _FakeRun:
    """Pipeline run object exposing ``to_dict()``, as the real run record does."""

    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


# ── _collect_repo_scope_rows ────────────────────────────────────────────────


def test_collect_repo_scope_rows_keeps_latest_row_per_scope():
    rows = [
        {"id": 1, "ado_project": "proj", "ado_repo": "repoA", "scope": "repo",
         "status": "in_progress", "gh_org": "acme", "gh_repo": "repoA",
         "completed_at": None, "error_message": None},
        {"id": 2, "ado_project": "proj", "ado_repo": "repoA", "scope": "repo",
         "status": "completed", "gh_org": "acme", "gh_repo": "repoA",
         "completed_at": "2026-01-01T00:00:00Z", "error_message": None},
    ]
    scoped = _collect_repo_scope_rows(_FakeDB(rows))
    entry = scoped["proj/repoA"]
    assert entry["gh_repo"] == "acme/repoA"
    assert entry["scopes"]["repo"]["status"] == "completed"
    assert entry["errors"] == []


def test_collect_repo_scope_rows_strips_missing_gh_org_and_collects_errors():
    rows = [
        {"id": 1, "ado_project": "proj", "ado_repo": "repoC", "scope": "repo",
         "status": "failed", "gh_org": "", "gh_repo": "repoC",
         "completed_at": None, "error_message": "clone failed"},
    ]
    scoped = _collect_repo_scope_rows(_FakeDB(rows))
    entry = scoped["proj/repoC"]
    assert entry["gh_repo"] == "repoC"
    assert entry["scopes"]["repo"]["error"] == "clone failed"
    assert entry["errors"] == ["clone failed"]


# ── _rollup_repo_status ─────────────────────────────────────────────────────


def test_rollup_repo_status_no_scopes_is_not_started():
    assert _rollup_repo_status({}) == "not_started"


def test_rollup_repo_status_any_failure_wins():
    scopes = {"repo": {"status": "completed"}, "pipelines": {"status": "failed"}}
    assert _rollup_repo_status(scopes) == "failed"


def test_rollup_repo_status_git_and_all_scopes_completed():
    scopes = {"repo": {"status": "completed"}, "pipelines": {"status": "completed"}}
    assert _rollup_repo_status(scopes) == "completed"


def test_rollup_repo_status_git_completed_but_others_pending_is_partial():
    scopes = {"repo": {"status": "completed"}, "pipelines": {"status": "pending"}}
    assert _rollup_repo_status(scopes) == "partial"


def test_rollup_repo_status_accepts_git_key_alias():
    assert _rollup_repo_status({"git": {"status": "completed"}}) == "completed"


def test_rollup_repo_status_no_git_scope_but_some_completed_is_partial():
    scopes = {"pipelines": {"status": "completed"}, "secrets": {"status": "pending"}}
    assert _rollup_repo_status(scopes) == "partial"


def test_rollup_repo_status_running_scope_is_in_progress():
    assert _rollup_repo_status({"pipelines": {"status": "running"}}) == "in_progress"


def test_rollup_repo_status_only_pending_falls_through_to_not_started():
    assert _rollup_repo_status({"pipelines": {"status": "pending"}}) == "not_started"


# ── extract_outcomes_from_pipeline_runs ─────────────────────────────────────


def test_extract_outcomes_reads_repo_details_from_dict_run():
    runs = [{
        "id": 1, "name": "run1", "status": "completed", "dry_run": False, "phase": "poc",
        "steps": [{"id": "migrate_repos", "result": {"repo_details": [
            {"repo": "repoA", "status": "completed", "summary": "ok", "errors": []},
        ]}}],
    }]
    outcomes = extract_outcomes_from_pipeline_runs(runs)
    assert len(outcomes) == 1
    assert outcomes[0]["repo"] == "repoA"
    assert outcomes[0]["run_name"] == "run1"
    assert outcomes[0]["step"] == "migrate_repos"


def test_extract_outcomes_accepts_run_objects_with_to_dict():
    run = _FakeRun({
        "id": 2, "name": "run2", "status": "completed", "dry_run": True, "phase": "wave1",
        "steps": [{"id": "validate", "result": {"repo_details": [
            {"repo": "repoB", "status": "failed", "summary": "sha mismatch", "errors": ["x"]},
        ]}}],
    })
    outcomes = extract_outcomes_from_pipeline_runs([run])
    assert outcomes[0]["repo"] == "repoB"
    assert outcomes[0]["dry_run"] is True


def test_extract_outcomes_ignores_unrelated_steps_and_missing_result():
    runs = [{
        "id": 3, "steps": [
            {"id": "some_other_step", "result": {"repo_details": [{"repo": "zzz"}]}},
            {"id": "validate"},
        ],
    }]
    assert extract_outcomes_from_pipeline_runs(runs) == []


def test_extract_outcomes_work_items_use_repo_fallback_and_blocker_text():
    runs = [{
        "id": 4, "name": "run4", "status": "running", "phase": "poc",
        "steps": [{"id": "convert_pipelines", "result": {"work_items": [
            {"ado_repo": "repoA", "status": "blocked", "blocker": "manual step needed"},
            {"repo": "repoB", "status": "done", "description": "fine"},
            {"status": "no-repo-here"},
        ]}}],
    }]
    outcomes = extract_outcomes_from_pipeline_runs(runs)
    assert [o["repo"] for o in outcomes] == ["repoA", "repoB"]
    assert outcomes[0]["errors"] == ["manual step needed"]
    assert outcomes[0]["summary"] == "manual step needed"
    assert outcomes[1]["summary"] == "fine"
    assert outcomes[1]["errors"] == []


# ── build_migration_status_report ───────────────────────────────────────────


def _sample_rows():
    return [
        {"id": 1, "ado_project": "proj", "ado_repo": "repoA", "scope": "repo",
         "status": "completed", "gh_org": "acme", "gh_repo": "repoA",
         "completed_at": "2026-01-01T00:00:00Z", "error_message": None},
        {"id": 2, "ado_project": "proj", "ado_repo": "repoA", "scope": "pipelines",
         "status": "completed", "gh_org": "acme", "gh_repo": "repoA",
         "completed_at": "2026-01-01T00:05:00Z", "error_message": None},
        {"id": 3, "ado_project": "proj", "ado_repo": "repoB", "scope": "repo",
         "status": "failed", "gh_org": "acme", "gh_repo": "repoB",
         "completed_at": None, "error_message": "clone failed"},
        {"id": 4, "ado_project": "proj", "ado_repo": "repoC", "scope": "repo",
         "status": "completed", "gh_org": "", "gh_repo": "repoC",
         "completed_at": "2026-01-02T00:00:00Z", "error_message": None},
        {"id": 5, "ado_project": "proj", "ado_repo": "repoC", "scope": "pipelines",
         "status": "in_progress", "gh_org": "", "gh_repo": "repoC",
         "completed_at": None, "error_message": None},
    ]


def test_build_migration_status_report_summarizes_and_buckets_repos():
    db = _FakeDB(_sample_rows(), {"completed_repos": 1, "failed_repos": 1})
    report = build_migration_status_report(db)

    assert report["summary"] == {
        "total_tracked_repos": 3,
        "git_migrated_count": 2,
        "failed_count": 1,
        "partial_count": 1,
        "completed_repos": 1,
        "failed_repos": 1,
    }
    assert [r["ado_repo"] for r in report["migrated_repos"]] == ["proj/repoA", "proj/repoC"]
    assert [r["ado_repo"] for r in report["failed_repos"]] == ["proj/repoB"]
    assert [r["ado_repo"] for r in report["partial_repos"]] == ["proj/repoC"]
    assert report["recent_run_outcomes"] == []


def test_build_migration_status_report_includes_recent_run_outcomes():
    db = _FakeDB(_sample_rows(), {})
    runs = [{
        "id": 9, "name": "run9", "status": "completed", "dry_run": False, "phase": "poc",
        "steps": [{"id": "migrate_repos", "result": {"repo_details": [
            {"repo": "repoA", "status": "completed", "summary": "ok", "errors": []},
        ]}}],
    }]
    report = build_migration_status_report(db, pipeline_runs=runs)
    assert len(report["recent_run_outcomes"]) == 1
    assert report["recent_run_outcomes"][0]["repo"] == "repoA"


def test_build_migration_status_report_truncates_run_outcomes_to_30():
    db = _FakeDB([], {})
    repo_details = [{"repo": f"repo{i}", "status": "completed"} for i in range(40)]
    runs = [{"id": 1, "steps": [{"id": "validate", "result": {"repo_details": repo_details}}]}]
    report = build_migration_status_report(db, pipeline_runs=runs)
    assert len(report["recent_run_outcomes"]) == 30
