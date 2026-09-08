"""GAP-005 (GAP-AUTH-05) — ``operator_requires_live_approval`` gates only OPERATOR.

``ado2gh/api/platform_rbac.py:49-52`` reads::

    if dry_run or not auth_enabled() or not user:
        return False
    return user.role == PlatformRole.OPERATOR

``permissions_for()`` (``ado2gh/auth/service.py:65-76``) grants ``can_operate`` to ADMIN,
COORDINATOR and OPERATOR (``:68-70``) but ``can_approve_live_execution`` only to ADMIN and
APPROVER (``:72``). COORDINATOR is therefore a role that can operate and cannot approve —
and is not the role the gate checks for. ``POST /v1/migrate``
(``services/accelerator_api/main.py:262-274``) and ``POST /v1/pipeline/runs`` both route
their approval decision through that one function.

Reproduction: an authenticated COORDINATOR, under the hardened
``ADO2GH_AUTH_ENABLED=true`` configuration, posts a live (``dry_run=false``) migration or
pipeline run. It is never routed through ``LiveApprovalStore``: no approval step, no queue
entry, no confirmation. Turning authentication on does not close this, and it was never
related to the client-supplied ``agent_live_approved`` field, which GAP-004 has since
removed from the request models altogether.

Both tests assert the security property: a role that lacks
``can_approve_live_execution`` must not be able to start live execution without an
approval, regardless of which single role name the gate happens to compare against.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.models import PlatformRole
from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db


@pytest.fixture
def coordinator_client(tmp_path, monkeypatch):
    """Authenticated COORDINATOR against the accelerator, with the runner stubbed out."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "gap005.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    from ado2gh.api.pipeline_runner import PipelineRunStore
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app
    from services.accelerator_api.routes import _shared

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    svc = auth_routes._svc
    svc.bootstrap_admin("admin", "AdminPass12345!", "Admin")
    svc.create_user("coord1", "CoordPass12345!", PlatformRole.COORDINATOR, "Coordinator One")

    monkeypatch.setattr(_shared._settings, "path", tmp_path / "ui_settings.json")
    _shared._settings.setup_profile(
        {
            "name": "GAP-005 profile",
            "ado_org_url": "https://dev.azure.com/fake-org",
            "ado_pat": "fake-ado-pat",
            "gh_org": "fake-gh-org",
            "github_token": "fake-gh-token",
        },
        role="admin",
    )

    PipelineRunStore._runs.clear()
    client = TestClient(app)
    login = client.post(
        "/v1/auth/login", json={"username": "coord1", "password": "CoordPass12345!"},
    )
    assert login.status_code == 200
    client.cookies.set("ado2gh_session", login.cookies.get("ado2gh_session"))

    with patch.object(_shared._runner, "start_async") as start_async:
        yield client, start_async
    PipelineRunStore._runs.clear()


def test_coordinator_live_pipeline_run_is_routed_through_approval(coordinator_client):
    client, start_async = coordinator_client

    resp = client.post(
        "/v1/pipeline/runs",
        json={"name": "GAP-005 coordinator live run", "dry_run": False},
    )

    assert resp.json() != {"detail": "profile_not_active"}, (
        "fixture failed to activate a migration profile; the test proved nothing"
    )
    start_async.assert_not_called()
    run = resp.json().get("run") or {}
    assert run.get("live_approval_status") != "auto_approved", (
        "a COORDINATOR — a role denied can_approve_live_execution — started a live "
        "pipeline run that auto-approved itself, because the gate only compares "
        "against PlatformRole.OPERATOR"
    )


def test_coordinator_live_migrate_is_routed_through_approval(coordinator_client, tmp_path):
    client, _ = coordinator_client

    resp = client.post(
        "/v1/migrate",
        json={
            "config_path": str(tmp_path / "migration.yaml"),
            "db_path": str(tmp_path / "gap005.db"),
            "dry_run": False,
        },
    )

    assert resp.status_code == 403, (
        f"a COORDINATOR live migrate got past the approval gate (HTTP "
        f"{resp.status_code}); an OPERATOR making the same call is parked awaiting "
        f"approval"
    )
    assert "awaiting_approval" in str(resp.json()), resp.json()
