"""Agent chat sessions use tool orchestration — accelerator only after guarded tools."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from services.agent.main import app


@pytest.fixture(autouse=True)
def _agent_stub_llm(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "stub")


def test_create_session_chat_only_does_not_call_accelerator():
    client = TestClient(app)
    with patch("services.agent.routes.session_routes._accel_post", new_callable=AsyncMock) as mock_post, patch(
        "services.agent.routes.session_routes._accel_get", new_callable=AsyncMock,
    ) as mock_get:
        r = client.post(
            "/v1/sessions",
            json={"profile_id": "lightweight", "prompt": "Hello", "dry_run": True},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "idle"
    mock_post.assert_not_called()
    mock_get.assert_not_called()


def test_run_pev_requires_plan():
    client = TestClient(app)
    created = client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "Hello", "dry_run": True},
    )
    sid = created.json()["session_id"]
    run = client.post(f"/v1/sessions/{sid}/run-pev")
    assert run.status_code == 409
    assert "migration_plan_required" in run.json()["detail"]


@pytest.mark.skip(reason="Pending rewrite for LangGraph orchestrator — plan confirmation flow changed in spec 012")
def test_run_pev_requires_plan_confirmation():
    client = TestClient(app)
    plan_doc = {
        "phase": "poc",
        "repo_count": 1,
        "repos": ["P/r1"],
        "pipeline_steps": ["connect", "migrate", "validate"],
        "dry_run": True,
        "blocked": False,
        "narrative": "Plan ready",
    }
    with patch(
        "services.agent.routes.session_routes._build_migration_plan", new_callable=AsyncMock,
    ) as mock_build, patch(
        "services.agent.routes.session_routes._accel_get", new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"repos": [{"assigned_phase": "poc"}], "repos_scanned": 1}
        mock_build.return_value = plan_doc
        created = client.post(
            "/v1/sessions",
            json={"profile_id": "lightweight", "prompt": "Plan poc migration", "dry_run": True},
        )
        sid = created.json()["session_id"]
        assert created.json().get("pending_form", {}).get("form_id") == "plan_confirmation"

    run = client.post(f"/v1/sessions/{sid}/run-pev")
    assert run.status_code == 409
    assert "plan_not_confirmed" in run.json()["detail"]


@pytest.mark.skip(reason="Pending rewrite for LangGraph orchestrator — discovery/planning flow changed in spec 012")
def test_message_orchestrator_discovers_and_plans():
    client = TestClient(app)
    plan_doc = {
        "phase": "poc",
        "repo_count": 1,
        "repo_order": ["P/r1"],
        "pipeline_steps": ["connect", "migrate", "validate"],
        "dry_run": True,
        "narrative": "Migrate 1 repo in poc",
        "blocked": False,
    }
    discovery = {
        "repos": [{"assigned_phase": "poc", "project": "P", "name": "r1"}],
        "repos_scanned": 1,
    }
    with patch(
        "services.agent.routes.session_routes._build_migration_plan", new_callable=AsyncMock,
    ) as mock_build, patch(
        "services.agent.routes.session_routes._accel_get", new_callable=AsyncMock,
    ) as mock_get, patch(
        "services.agent.routes.session_routes._accel_post", new_callable=AsyncMock,
    ) as mock_post:
        mock_get.return_value = discovery
        mock_build.return_value = plan_doc
        mock_post.return_value = {"run": {"id": "run-1", "status": "running", "steps": []}}

        created = client.post(
            "/v1/sessions",
            json={"profile_id": "lightweight", "prompt": "Plan poc migration", "dry_run": True},
        )
        sid = created.json()["session_id"]
        body = created.json()
        assert body["status"] == "idle"
        assert body.get("migration_plan") or client.get(f"/v1/sessions/{sid}").json().get("migration_plan")
        mock_get.assert_awaited()
        mock_build.assert_awaited()


@pytest.mark.skip(reason="Pending rewrite for LangGraph orchestrator — PEV start flow changed in spec 012")
def test_message_execute_starts_pev_with_plan():
    client = TestClient(app)
    plan_doc = {
        "phase": "poc",
        "repo_count": 1,
        "repo_order": ["P/r1"],
        "pipeline_steps": ["connect", "migrate", "validate"],
        "dry_run": True,
        "blocked": False,
    }
    with patch(
        "services.agent.routes.session_routes._build_migration_plan", new_callable=AsyncMock,
    ) as mock_build, patch(
        "services.agent.routes.session_routes._accel_get", new_callable=AsyncMock,
    ) as mock_get, patch(
        "services.agent.routes.session_routes._accel_post", new_callable=AsyncMock,
    ) as mock_post:
        mock_get.side_effect = [
            {"repos": [{"assigned_phase": "poc"}], "repos_scanned": 1},
            {"run": {
                "id": "run-1",
                "status": "dry_run_complete",
                "dry_run": True,
                "steps": [
                    {"id": "migrate", "status": "completed", "message": "1 completed"},
                    {"id": "validate", "status": "skipped", "message": "dry run"},
                ],
            }},
        ]
        mock_build.return_value = plan_doc
        mock_post.return_value = {"run": {"id": "run-1", "status": "running", "steps": []}}

        created = client.post(
            "/v1/sessions",
            json={"profile_id": "lightweight", "prompt": "Migrate poc repos", "dry_run": True},
        )
        sid = created.json()["session_id"]
        assert created.json().get("pending_form", {}).get("form_id") == "plan_confirmation"

        approved = client.post(
            f"/v1/sessions/{sid}/form-submit",
            json={"values": {"plan_confirmed": True, "confirm_execute": False}},
        )
        assert approved.status_code == 200

        run_msg = client.post(f"/v1/sessions/{sid}/message", json={"message": "execute dry-run"})
        assert run_msg.status_code == 200
        assert run_msg.json()["status"] in ("planning", "executing", "validating", "completed")
        assert mock_post.await_count >= 1
