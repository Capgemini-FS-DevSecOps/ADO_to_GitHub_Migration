"""Session lifecycle isolation and cancel cleanup."""
from __future__ import annotations

import pytest

from ado2gh.agents.migration_agent.session.lifecycle import (
    collect_session_repo_ids,
    new_isolated_agent_session,
)
from ado2gh.agents.migration_agent.session.state import reset_session_for_new_migration
from ado2gh.models import MigrationStatus, RepoConfig
from ado2gh.state.db import StateDB


def test_new_isolated_agent_session_has_no_migration_context():
    session = new_isolated_agent_session(
        "ses_test",
        profile_id="prof1",
        dry_run=True,
    )
    assert "plan_repository_id" not in session
    assert "migration_plan" not in session
    assert "last_completed_repository_id" not in session
    assert "execution_mode_confirmed" not in session


def test_reset_session_for_new_migration_clears_repository_without_replacement():
    session = {
        "plan_repository_id": "proj/old-repo",
        "migration_plan": {"repos": [{"id": "proj/old-repo"}]},
        "plan_approved": True,
        "last_completed_repository_id": "proj/old-repo",
        "execution_mode_confirmed": True,
    }
    reset_session_for_new_migration(session)
    assert "plan_repository_id" not in session
    assert "migration_plan" not in session
    assert "last_completed_repository_id" not in session
    assert session["plan_approved"] is False


def test_collect_session_repo_ids_from_plan():
    session = {
        "plan_repository_id": "azure-pipelines/repo-a",
        "migration_plan": {
            "repository_id": "azure-pipelines/repo-b",
            "repos": [{"id": "azure-pipelines/repo-c"}],
            "work_items": [{"repo": "azure-pipelines/repo-d"}],
        },
    }
    ids = collect_session_repo_ids(session)
    assert "azure-pipelines/repo-a" in ids
    assert "azure-pipelines/repo-b" in ids
    assert "azure-pipelines/repo-c" in ids
    assert "azure-pipelines/repo-d" in ids


def test_two_sessions_do_not_share_migration_state():
    session_a = new_isolated_agent_session("ses_a", profile_id="prof1")
    session_b = new_isolated_agent_session("ses_b", profile_id="prof1")
    session_a["plan_repository_id"] = "proj/repo-a"
    session_a["migration_plan"] = {"repository_id": "proj/repo-a"}
    session_a["last_completed_repository_id"] = "proj/repo-a"

    assert "plan_repository_id" not in session_b
    assert "migration_plan" not in session_b
    assert "last_completed_repository_id" not in session_b
    assert session_a["plan_repository_id"] == "proj/repo-a"


@pytest.mark.asyncio
async def test_cancel_agent_session_clears_migration_state(tmp_path, monkeypatch):
    from ado2gh.agents.migration_agent.session.lifecycle import cancel_agent_session
    from ado2gh.state.factory import create_state_db

    db = StateDB(str(tmp_path / "cancel.db"))
    monkeypatch.setattr(
        "ado2gh.state.factory.create_state_db",
        lambda: db,
    )
    repo = RepoConfig(
        ado_project="azure-pipelines",
        ado_repo="build-migration",
        gh_org="gh",
        gh_repo="build-migration",
    )
    db.upsert_migration(1, repo, "repo", MigrationStatus.IN_PROGRESS)

    session = new_isolated_agent_session("ses_cancel", profile_id="prof1", dry_run=False)
    session["plan_repository_id"] = "azure-pipelines/build-migration"
    session["run_id"] = "run-123"

    result = await cancel_agent_session(session, action="stop")
    assert result["status"] == "idle"
    assert "plan_repository_id" not in session
    assert not db.has_repo_in_progress("azure-pipelines", "build-migration")
