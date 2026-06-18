"""Tests for chat-driven dry-run vs live execution mode."""
from __future__ import annotations

import pytest

from ado2gh.agents.execution_mode import (
    apply_execution_mode_from_message,
    parse_execution_mode_from_message,
)
from ado2gh.auth.models import PlatformRole


@pytest.mark.parametrize(
    "message,expected",
    [
        ("No dry run should be false", False),
        ("migrate poc live to github", False),
        ("execute live migration", False),
        ("dry_run=false", False),
        ("run dry-run for poc", True),
        ("plan poc dry run", True),
        ("migrate poc to github", None),
    ],
)
def test_parse_execution_mode(message, expected):
    assert parse_execution_mode_from_message(message) is expected


def test_apply_execution_mode_clears_stale_plan():
    session = {
        "dry_run": True,
        "migration_plan": {"phase": "poc", "dry_run": True},
    }
    changed = apply_execution_mode_from_message(session, "no dry run — migrate live")
    assert changed is True
    assert session["dry_run"] is False
    assert "migration_plan" not in session


@pytest.mark.asyncio
async def test_admin_live_message_rebuilds_plan_with_live_flag():
    from unittest.mock import AsyncMock

    from ado2gh.agents.llm_provider import StubLLMProvider
    from ado2gh.agents.session_orchestrator import process_user_message

    session = {
        "session_id": "ses_admin",
        "profile_id": "lightweight",
        "dry_run": True,
        "user_role": PlatformRole.ADMIN.value,
        "permissions": {"can_approve_live_execution": True},
        "plan_phase": "poc",
        "messages": [],
        "tasks": [],
        "discovery_snapshot": {
            "repos": [
                {"assigned_phase": "poc", "project": "P", "repo_name": "r1"},
            ],
        },
        "migration_plan": {
            "phase": "poc",
            "repo_count": 1,
            "blocked": False,
            "dry_run": True,
            "pipeline_steps": ["connect", "migrate", "validate"],
        },
    }

    async def mock_build(sess, token, phase="poc"):
        return {
            "phase": phase,
            "repo_count": 1,
            "blocked": False,
            "dry_run": sess.get("dry_run", True),
            "pipeline_steps": ["connect", "migrate", "validate"],
        }

    await process_user_message(
        session,
        "No dry run should be false — execute live migration for poc",
        llm=StubLLMProvider(),
        llm_degraded=True,
        accel_get=AsyncMock(),
        build_plan=mock_build,
        session_token=None,
    )

    assert session["dry_run"] is False
    assert session.get("migration_plan", {}).get("dry_run") is False
