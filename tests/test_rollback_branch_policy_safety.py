from __future__ import annotations

import pytest

from ado2gh.core.rollback import RollbackHandler
from ado2gh.models import MigrationScope, MigrationStatus, RepoConfig, WaveConfig


class NoRemoteWritesGH:
    def __init__(self):
        self.calls = []

    def get_repo(self, *args, **kwargs):
        self.calls.append(("get_repo", args, kwargs))
        raise AssertionError("branch-policy rollback must not read or mutate GitHub")

    def _delete(self, *args, **kwargs):
        self.calls.append(("delete", args, kwargs))
        raise AssertionError("branch-policy rollback must never delete protection")


class RecordingDB:
    def __init__(self):
        self.migration_writes = []
        self.wave_writes = []

    def upsert_migration(self, *args, **kwargs):
        self.migration_writes.append((args, kwargs))

    def get_wave_migrations(self, _wave_id):
        return [{
            "ado_project": "Payments",
            "ado_repo": "api",
            "gh_org": "octo",
            "gh_repo": "payments-api",
            "scope": MigrationScope.BRANCH_POLICIES.value,
            "status": MigrationStatus.NEEDS_REVIEW.value,
        }]

    def mark_wave_run(self, wave_id, status):
        self.wave_writes.append((wave_id, status))


def _repo():
    return RepoConfig(
        ado_project="Payments",
        ado_repo="api",
        gh_org="octo",
        gh_repo="payments-api",
        scopes=[MigrationScope.BRANCH_POLICIES.value],
    )


@pytest.mark.parametrize("dry_run", [False, True])
def test_branch_policy_rollback_always_refuses_without_artifact_fingerprints(
    dry_run,
):
    gh = NoRemoteWritesGH()
    handler = RollbackHandler(gh, RecordingDB())

    with pytest.raises(NotImplementedError) as exc_info:
        handler._rollback_branch_protection(
            "octo", "payments-api", dry_run, {}
        )

    message = str(exc_info.value)
    assert "exact agent-owned before/after fingerprints" in message
    assert "Manual remediation is required" in message
    assert "every affected branch" in message
    assert gh.calls == []


def test_repo_scope_rollback_records_error_without_false_rolled_back_receipt():
    gh = NoRemoteWritesGH()
    db = RecordingDB()
    handler = RollbackHandler(gh, db)

    result = handler.rollback_repos(
        [_repo()],
        wave_id=42,
        scopes=[MigrationScope.BRANCH_POLICIES.value],
        dry_run=False,
    )

    assert result == {
        "repos_deleted": 0,
        "scopes_rolled_back": 0,
        "errors": 1,
    }
    assert db.migration_writes == []
    assert gh.calls == []


def test_wave_rollback_is_partial_and_does_not_mark_policy_rolled_back():
    gh = NoRemoteWritesGH()
    db = RecordingDB()
    handler = RollbackHandler(gh, db)
    wave = WaveConfig(
        wave_id=42, name="pilot", description="test wave", repos=[_repo()]
    )

    result = handler.rollback_wave(
        wave,
        scopes=[MigrationScope.BRANCH_POLICIES.value],
        dry_run=False,
    )

    assert result["errors"] == 1
    assert result["scopes_rolled_back"] == 0
    assert db.migration_writes == []
    assert db.wave_writes == [(42, "rollback_partial")]
    assert gh.calls == []
