"""
Executable validation of specs/004-agent-pev-rbac/quickstart.md scenarios (T058).

Maps quickstart scenarios to API-level checks runnable in CI without a browser.
Manual-only steps (incognito UI, 30-minute soak, container stop) are documented in
specs/004-agent-pev-rbac/quickstart-validation.md.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db


@pytest.fixture
def accel(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db = tmp_path / "qs.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db)))
    return TestClient(app)


@pytest.fixture
def agent_client():
    from services.agent.main import app as agent_app
    return TestClient(agent_app)


def _bootstrap(accel: TestClient) -> None:
    accel.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )


def _user(accel: TestClient, username: str, role: str) -> TestClient:
    accel.post(
        "/v1/auth/users",
        json={
            "username": username,
            "password": "twelve-char-pass",
            "role": role,
            "display_name": username,
        },
    )
    c = TestClient(accel.app)
    c.post("/v1/auth/login", json={"username": username, "password": "twelve-char-pass"})
    return c


class TestQuickstartScenario1BootstrapAndUsers:
    """Scenario 1: Bootstrap admin + register operator/approver."""

    def test_independent_sessions(self, accel):
        _bootstrap(accel)
        op = _user(accel, "operator1", "operator")
        ap = _user(accel, "approver1", "approver")
        assert op.get("/v1/auth/session").json()["user"]["username"] == "operator1"
        assert ap.get("/v1/auth/session").json()["user"]["role"] == "approver"
        op.post("/v1/auth/logout")
        assert op.get("/v1/auth/session").status_code == 401
        assert ap.get("/v1/auth/session").status_code == 200


class TestQuickstartScenario2LlmOnboarding:
    """Scenario 2: Admin onboards OpenAI + Anthropic; secrets masked."""

    def test_dual_provider_onboard_and_mask(self, accel):
        _bootstrap(accel)
        for body in (
            {
                "display_name": "GPT-4o mini",
                "provider": "openai",
                "model_id": "gpt-4o-mini",
                "api_key": "sk-openai-test-key",
            },
            {
                "display_name": "Claude",
                "provider": "anthropic",
                "model_id": "claude-3-5-sonnet-20241022",
                "api_key": "sk-ant-test-key",
            },
        ):
            r = accel.post("/v1/settings/llm-models", json=body)
            assert r.status_code == 200
        listed = accel.get("/v1/settings/llm-models")
        assert listed.status_code == 200
        providers = {m["provider"] for m in listed.json()["models"]}
        assert "openai" in providers
        assert "anthropic" in providers
        raw = listed.text
        assert "sk-openai-test-key" not in raw
        assert "sk-ant-test-key" not in raw
        assert "***" in raw


class TestQuickstartScenario3OperatorDryRunPev:
    """Scenario 3: Operator dry-run PEV from Agent tab."""

    def test_dry_run_session_fields(self, agent_client):
        with patch("services.agent.main._accel_post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = [
                {"waves": []},
                {"auto": 0},
                [{"wave_id": 1, "status": "ok"}],
                {"total": 0, "matched": 0},
            ]
            r = agent_client.post(
                "/v1/sessions",
                json={
                    "profile_id": "lightweight",
                    "prompt": "Plan wave 1",
                    "dry_run": True,
                    "execute_pev": True,
                },
            )
        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
        assert "selected_model_id" in data
        assert data["status"] in ("planning", "executing", "validating", "completed")


class TestQuickstartScenario4OperatorReadOnlyProfiles:
    """Scenario 4: Operator read-only profiles; models blocked."""

    def test_operator_read_settings_block_mutations(self, accel):
        _bootstrap(accel)
        op = _user(accel, "operator1", "operator")
        assert op.get("/v1/settings").status_code == 200
        perms = op.get("/v1/auth/session").json()["permissions"]
        assert perms["can_operate"] is True
        assert perms["can_manage_settings"] is False
        assert op.post(
            "/v1/settings/llm-models",
            json={"display_name": "X", "provider": "openai", "model_id": "x", "api_key": "k"},
        ).status_code == 403
        assert op.put(
            "/v1/settings/profiles/fake-id",
            json={"name": "x", "ado_org_url": "https://dev.azure.com/x", "gh_org": "x"},
        ).status_code in (403, 404)


class TestQuickstartScenario5UnifiedLiveApproval:
    """Scenario 5: Unified live approval queue."""

    def test_operator_request_admin_approves(self, accel, agent_client):
        _bootstrap(accel)
        op = _user(accel, "operator1", "operator")
        created = op.post(
            "/v1/platform/approvals",
            json={
                "scope_type": "agent_session",
                "scope_id": "sess_qs5",
                "reason_request": "Ready for live",
            },
        )
        assert created.status_code == 200
        assert created.json()["status"] == "pending"
        approved = accel.post(
            f"/v1/platform/approvals/{created.json()['id']}/approve",
            json={"reason": "POC approved"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

    def test_operator_live_migrate_blocked(self, accel):
        _bootstrap(accel)
        op = _user(accel, "operator1", "operator")
        r = op.post(
            "/v1/migrate",
            json={"config_path": "migration.yaml", "dry_run": False, "wave_id": 1},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "awaiting_approval"

    def test_deny_path(self, accel):
        _bootstrap(accel)
        op = _user(accel, "operator1", "operator")
        created = op.post(
            "/v1/platform/approvals",
            json={"scope_type": "agent_session", "scope_id": "sess_deny", "reason_request": "live"},
        )
        denied = accel.post(
            f"/v1/platform/approvals/{created.json()['id']}/deny",
            json={"reason": "Not ready"},
        )
        assert denied.status_code == 200
        assert denied.json()["status"] == "denied"


class TestQuickstartScenario6SecretHygiene:
    """Scenario 6: No raw secrets after save."""

    def test_llm_and_audit_no_raw_keys(self, accel, tmp_path):
        _bootstrap(accel)
        secret = "sk-super-secret-key-12345"
        accel.post(
            "/v1/settings/llm-models",
            json={
                "display_name": "GPT",
                "provider": "openai",
                "model_id": "gpt-4o-mini",
                "api_key": secret,
            },
        )
        listed = accel.get("/v1/settings/llm-models")
        assert secret not in listed.text
        db = create_state_db(str(tmp_path / "qs.db"))
        events = db.list_audit_events(profile_id="_platform", limit=10)
        for ev in events:
            payload = ev.get("payload_json") or ""
            assert secret not in payload


class TestQuickstartScenario8Remediation:
    """Scenario 8: Agent health remediation when accelerator unreachable."""

    def test_health_includes_remediation(self, agent_client):
        with patch("services.agent.main._check_accelerator", new_callable=AsyncMock) as chk:
            chk.return_value = (False, "Connection refused")
            r = agent_client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["accelerator_reachable"] is False
        assert len(data.get("remediation_steps", [])) >= 1


class TestQuickstartScenario9DualAndGates:
    """Scenario 9: Platform approve then assignment gate blocks live."""

    def test_gate_blocks_after_platform_approval(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
        db_path = tmp_path / "gate.db"
        monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
        from ado2gh.api.live_approval_store import LiveApprovalStore
        from ado2gh.auth.models import PlatformRole, PlatformUser

        store = LiveApprovalStore(str(db_path))
        operator = PlatformUser("u1", "op1", PlatformRole.OPERATOR, "Op")
        approver = PlatformUser("u2", "admin", PlatformRole.ADMIN, "Admin")
        row = store.create_or_get_pending(
            operator, "agent_session", "sess_and", assignment_id="asgn_x", reason_request="live",
        )
        with patch("ado2gh.api.live_approval_store.enforce_live_gate") as gate:
            from fastapi import HTTPException
            gate.side_effect = HTTPException(status_code=409, detail={"message": "blocked"})
            with pytest.raises(HTTPException) as exc:
                store.approve(row["id"], approver, "approved anyway")
            assert exc.value.status_code == 409
