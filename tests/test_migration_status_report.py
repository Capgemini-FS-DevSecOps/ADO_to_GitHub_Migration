"""Tests for migration status reporting and agent intent routing."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ado2gh.agents.session_orchestrator import (
    _migration_intent,
    _migration_status_intent,
    process_user_message,
)
from ado2gh.agents.llm_provider import StubLLMProvider
from ado2gh.api.migration_status_report import (
    build_migration_status_report,
    format_migration_status_narrative,
)
from ado2gh.models import MigrationStatus, RepoConfig
from ado2gh.state.db import StateDB


def test_migration_status_intent_detects_which_repos_question():
    assert _migration_status_intent("Which repos have been migrated so far?")
    assert not _migration_intent("Which repos have been migrated so far?")


def test_migration_intent_still_detects_execute():
    assert _migration_intent("migrate live now")
    assert not _migration_status_intent("migrate live now")


def test_status_report_empty_state(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    report = build_migration_status_report(db)
    narrative = format_migration_status_narrative(report)
    assert "No repositories have been successfully migrated" in narrative
    assert report["summary"]["git_migrated_count"] == 0


def test_status_report_completed_git_migration(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    repo = RepoConfig(
        ado_project="azure-pipelines",
        ado_repo="azure-pipelines-script-migration",
        gh_org="ado-to-gh-migration",
        gh_repo="azure-pipelines-script-migration",
        phase="poc",
    )
    db.upsert_migration(1, repo, "repo", MigrationStatus.COMPLETED)
    report = build_migration_status_report(db)
    assert report["summary"]["git_migrated_count"] == 1
    narrative = format_migration_status_narrative(report)
    assert "azure-pipelines/azure-pipelines-script-migration" in narrative
    assert "ado-to-gh-migration/azure-pipelines-script-migration" in narrative


@pytest.mark.asyncio
async def test_process_user_message_status_query_short_circuits_plan():
    session = {
        "profile_id": "prof-1",
        "tasks": [],
        "migration_plan": {
            "narrative": "Migration plan for 1 repository is ready",
            "repo_count": 1,
        },
        "plan_approved": False,
        "dry_run": True,
        "messages": [],
    }
    accel_get = AsyncMock(return_value={
        "summary": {"git_migrated_count": 0},
        "migrated_repos": [],
        "failed_repos": [],
        "partial_repos": [],
        "recent_run_outcomes": [],
    })
    result = await process_user_message(
        session,
        "Which repos have been migrated so far?",
        llm=StubLLMProvider(),
        llm_degraded=True,
        accel_get=accel_get,
        build_plan=AsyncMock(),
        session_token="tok",
    )
    assert "No repositories have been successfully migrated" in result.reply
    assert "Migration plan for 1 repository" not in result.reply
    accel_get.assert_called_once_with("/v1/migration/status", session_token="tok")
