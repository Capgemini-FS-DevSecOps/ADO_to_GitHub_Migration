"""Agent must not start live PEV for operators without approval."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.models import PlatformRole
from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db
from services.agent.main import app


@pytest.fixture
def authed_operator_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "agent_live_gate.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app as accel_app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    svc = auth_routes._svc
    svc.create_user("operator", "OperatorPass123!", PlatformRole.OPERATOR, "Operator")

    agent = TestClient(app)
    accel = TestClient(accel_app)
    login = accel.post("/v1/auth/login", json={"username": "operator", "password": "OperatorPass123!"})
    assert login.status_code == 200
    cookie = login.cookies.get("ado2gh_session")
    agent.cookies.set("ado2gh_session", cookie)
    return agent


def test_operator_live_execute_pev_blocked_without_approval(authed_operator_client):
    client = authed_operator_client
    with patch("services.agent.main._accel_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"repos": [], "repos_scanned": 0}
        with patch("services.agent.main._build_migration_plan", new_callable=AsyncMock) as mock_plan:
            mock_plan.return_value = {
                "phase": "poc",
                "repo_count": 1,
                "blocked": False,
                "narrative": "Plan ready",
                "pipeline_steps": ["connect", "migrate", "validate"],
            }
            with patch("services.agent.main._start_pev_run") as start_pev:
                created = client.post(
                    "/v1/sessions",
                    json={
                        "profile_id": "lightweight",
                        "prompt": "execute live migration for poc",
                        "dry_run": False,
                        "execute_pev": True,
                    },
                )
                assert created.status_code == 200
                body = created.json()
                assert body["status"] == "awaiting_approval"
                start_pev.assert_not_called()

                sid = body["session_id"]
                session = client.get(f"/v1/sessions/{sid}").json()
                assert session["execution_policy"]["requires_live_approval"] is True
                assert any(
                    "approval" in (m.get("content") or "").lower()
                    for m in session.get("messages", [])
                )
