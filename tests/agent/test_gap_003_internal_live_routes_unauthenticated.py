"""Regression check for register entry GAP-003 (GAP-AUTH-02) — agent internal resume-live/deny-live routes are unauthorized.

``resume_live_internal`` (``services/agent/routes/execution_routes.py``) takes no
``Request`` parameter at all: it sets ``dry_run=False`` and
``live_approval_status="approved"`` with no in-handler authorization check and no audit
record, in contrast to ``approve_session`` on the same module which does call
``_audit.record``. ``deny_live_internal`` (``:497-498``) has the same shape.

The only guard is the agent auth middleware's shared secret in
``services/agent/main.py``: ``_INTERNAL_TOKEN`` comes from ``ADO2GH_INTERNAL_TOKEN``, and
in the reported defect an empty token let the request straight through. At the time the
gap was raised no shipped manifest supplied that token — not ``docker-compose.yml``, not
``docker-compose.prod.yml``, not ``deploy/kubernetes/configmap.yaml``, including the two
that harden the platform with ``ADO2GH_AUTH_ENABLED: "true"`` — ``.env.example`` shipped
it commented out, and ``docker-compose.yml`` publishes agent port ``8090`` to the host.
Unlike GAP-002 this was therefore *not* closed by turning authentication on.

Reproduction: with ``ADO2GH_AUTH_ENABLED=true`` and ``ADO2GH_INTERNAL_TOKEN`` unset,
anyone who can reach the agent port could POST to
``/v1/internal/sessions/{id}/resume-live`` to drive a session into live execution, or to
``/v1/internal/sessions/{id}/deny-live`` to silently overturn a legitimate approver's
decision — with no credentials and, on the resume path, no audit record.

The fix has two halves and these tests hold both in place: the range now fails closed on
an empty token, and the hardened manifests supply one. The empty token is pinned here
directly rather than relying on any manifest, so the tests keep proving the code-level
guard even though the deployment-level hole is closed too.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

SESSION_ID = "ses_gap003fixture"


@pytest.fixture
def agent_client(tmp_path, monkeypatch):
    """Agent service with authentication on and no internal token — the GAP-003 case."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "gap003.db"))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    import services.agent.main as agent_main
    from ado2gh.agents.migration_agent.session.lifecycle import new_isolated_agent_session

    # The token is read once at import time, so pin the module global rather than the
    # environment. Empty is the GAP-003 condition, and the guard must hold on its own
    # merits whatever the deployment manifests happen to supply.
    monkeypatch.setattr(agent_main, "_INTERNAL_TOKEN", "")

    agent_main._sessions[SESSION_ID] = new_isolated_agent_session(
        SESSION_ID, profile_id="lightweight", dry_run=True,
    )
    yield TestClient(agent_main.app)
    agent_main._sessions.clear()


def test_unauthenticated_caller_cannot_resume_a_session_into_live_execution(agent_client):
    from services.agent.main import _sessions

    resp = agent_client.post(f"/v1/internal/sessions/{SESSION_ID}/resume-live")

    assert resp.status_code in (401, 403), (
        f"a caller with no credentials resumed an agent session into live execution "
        f"(HTTP {resp.status_code}); /v1/internal/ must fail closed whenever "
        f"ADO2GH_INTERNAL_TOKEN is unset, not fall open"
    )
    session = _sessions[SESSION_ID]
    assert session.get("dry_run") is True
    assert session.get("live_approval_status") != "approved"


def test_unauthenticated_caller_cannot_deny_a_live_approval(agent_client):
    from services.agent.main import _sessions

    resp = agent_client.post(
        f"/v1/internal/sessions/{SESSION_ID}/deny-live", json={"reason": "not me"},
    )

    assert resp.status_code in (401, 403), (
        f"a caller with no credentials overturned a live-execution decision "
        f"(HTTP {resp.status_code})"
    )
    assert _sessions[SESSION_ID].get("live_approval_status") != "denied"


def test_resume_live_writes_an_audit_record(tmp_path, monkeypatch):
    """Even the authorized service-to-service resume leaves no audit trail today."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "gap003_audit.db"))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    import services.agent.main as agent_main
    from ado2gh.agents.migration_agent.session.lifecycle import new_isolated_agent_session

    monkeypatch.setattr(agent_main, "_INTERNAL_TOKEN", "fake-internal-token")
    agent_main._sessions[SESSION_ID] = new_isolated_agent_session(
        SESSION_ID, profile_id="lightweight", dry_run=True,
    )
    try:
        client = TestClient(agent_main.app)
        with patch(
            "services.agent.routes.execution_routes._audit", new=MagicMock(),
        ) as audit:
            resp = client.post(
                f"/v1/internal/sessions/{SESSION_ID}/resume-live",
                headers={agent_main.INTERNAL_TOKEN_HEADER: "fake-internal-token"},
            )
        assert resp.status_code == 200
        assert audit.record.called, (
            "resume-live drove a session into live execution without recording an "
            "audit event, unlike the operator-facing /approve route"
        )
    finally:
        agent_main._sessions.clear()
