"""GAP-002 (GAP-AUTH-01) — live-execution approval is inert in the default configuration.

``auth_enabled()`` reads ``ADO2GH_AUTH_ENABLED`` and returns ``False`` when the variable
is unset (``ado2gh/auth/service.py:21-22``). That is the code-level default, not a
deployment choice, and it is also the value shipped in ``docker-compose.yml:53``. Every
platform RBAC guard short-circuits on that flag before any capability check
(``ado2gh/api/platform_rbac.py:16-17,25-26``), as do the agent route gates
(``services/agent/routes/_helpers.py:199,209``), the agent live-execution
policies (``ado2gh/agents/migration_agent/policies.py:127-129,141-143``) and both HTTP
auth middlewares (``services/agent/main.py:94-95``,
``services/accelerator_api/main.py:106-107``).

Reproduction: with ``ADO2GH_AUTH_ENABLED`` unset, a client that presents no session
cookie and carries no role can

1. flip an agent session from dry-run to live execution via
   ``PATCH /v1/sessions/{id}/execution-mode``, and
2. start a live (``dry_run=false``) pipeline run on the accelerator that is dispatched
   straight to the runner and stamped as approved,

in neither case creating an approval-queue entry or a role-derived audit actor.

Both tests assert the same security property: a request carrying no credentials must
never reach a live-execution transition, whatever the value of ``ADO2GH_AUTH_ENABLED``.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.state.factory import create_state_db


@pytest.fixture
def anon_agent_client(tmp_path, monkeypatch):
    """Agent service reached with no cookie, in the shipped default auth configuration."""
    monkeypatch.delenv("ADO2GH_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "gap002_agent.db"))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.agent.main import _sessions, app

    yield TestClient(app)
    _sessions.clear()


@pytest.fixture
def anon_accel_client(tmp_path, monkeypatch):
    """Accelerator reached with no cookie, with an active profile so governance passes."""
    monkeypatch.delenv("ADO2GH_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "gap002_accel.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    create_state_db(str(db_path))

    from services.accelerator_api.main import app
    from services.accelerator_api.routes import _shared

    monkeypatch.setattr(_shared._settings, "path", tmp_path / "ui_settings.json")
    _shared._settings.setup_profile(
        {
            "name": "GAP-002 profile",
            "ado_org_url": "https://dev.azure.com/fake-org",
            "ado_pat": "fake-ado-pat",
            "gh_org": "fake-gh-org",
            "github_token": "fake-gh-token",
        },
        role="admin",
    )
    from ado2gh.api.pipeline_runner import PipelineRunStore

    PipelineRunStore._runs.clear()
    with patch.object(_shared._runner, "start_async") as start_async:
        yield TestClient(app), start_async
    PipelineRunStore._runs.clear()


def _anonymous_live_flip(client: TestClient) -> tuple[int, dict | None]:
    """Create a dry-run agent session anonymously and try to switch it to live.

    Returns the status code of whichever step the anonymous caller was stopped at,
    plus the resulting in-memory session (``None`` if it was never created).
    """
    from services.agent.main import _sessions

    created = client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "Plan a POC migration", "dry_run": True},
    )
    if created.status_code != 200:
        return created.status_code, None
    session_id = created.json()["session_id"]
    flipped = client.patch(
        f"/v1/sessions/{session_id}/execution-mode", json={"dry_run": False},
    )
    return flipped.status_code, _sessions.get(session_id)


def test_anonymous_caller_cannot_switch_agent_session_to_live(anon_agent_client):
    status, session = _anonymous_live_flip(anon_agent_client)

    assert status in (401, 403), (
        f"a caller with no credentials switched an agent session to live execution "
        f"(HTTP {status}); the live-execution gate is inert when ADO2GH_AUTH_ENABLED "
        f"is unset"
    )
    assert session is None or session.get("dry_run") is True, (
        "agent session was left in live mode after an unauthenticated request"
    )


def test_anonymous_caller_cannot_start_a_live_pipeline_run(anon_accel_client):
    client, start_async = anon_accel_client

    resp = client.post(
        "/v1/pipeline/runs",
        json={"name": "GAP-002 live run", "dry_run": False},
    )

    assert resp.json() != {"detail": "profile_not_active"}, (
        "fixture failed to activate a migration profile; the test proved nothing"
    )
    start_async.assert_not_called()
    run = resp.json().get("run") or {}
    assert run.get("live_approval_status") != "approved", (
        "a live pipeline run started by an unauthenticated caller was recorded as approved"
    )
