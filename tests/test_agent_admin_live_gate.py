"""Admin users may start live PEV without the approval queue."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db
from services.agent.main import app


@pytest.fixture
def authed_admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    db_path = tmp_path / "agent_admin_live.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app as accel_app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    svc = auth_routes._svc
    svc.bootstrap_admin("admin", "AdminPass12345!", "Admin")

    agent = TestClient(app)
    accel = TestClient(accel_app)
    login = accel.post("/v1/auth/login", json={"username": "admin", "password": "AdminPass12345!"})
    assert login.status_code == 200
    cookie = login.cookies.get("ado2gh_session")
    agent.cookies.set("ado2gh_session", cookie)
    return agent


def test_admin_live_execute_pev_starts_without_approval(authed_admin_client):
    client = authed_admin_client
    with patch("services.agent.main._accel_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"repos": [], "repos_scanned": 0}
        with patch("services.agent.main._build_migration_plan", new_callable=AsyncMock) as mock_plan:
            mock_plan.return_value = {
                "phase": "poc",
                "repo_count": 1,
                "blocked": False,
                "dry_run": False,
                "narrative": "Plan ready",
                "pipeline_steps": ["connect", "migrate_repos", "validate"],
            }
            with patch("services.agent.main._start_pev_run") as start_pev:
                created = client.post(
                    "/v1/sessions",
                    json={
                        "profile_id": "lightweight",
                        "prompt": "plan poc migration",
                        "dry_run": False,
                    },
                )
                assert created.status_code == 200
                sid = created.json()["session_id"]
                assert created.json().get("pending_form", {}).get("form_id") == "plan_confirmation"
                start_pev.assert_not_called()

                confirm = client.post(
                    f"/v1/sessions/{sid}/form-submit",
                    json={"values": {"plan_confirmed": True, "confirm_execute": True}},
                )
                assert confirm.status_code == 200
                start_pev.assert_called_once()
                session = client.get(f"/v1/sessions/{sid}").json()
                assert session["execution_policy"]["can_execute_live_without_approval"] is True
                assert session["user_role"] == "admin"
