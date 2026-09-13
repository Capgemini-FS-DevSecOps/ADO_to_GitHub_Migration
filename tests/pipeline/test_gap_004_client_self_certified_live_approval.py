"""GAP-004 (GAP-AUTH-04) — pipeline-run routes let the client self-certify live approval.

The defect: ``PipelineRunStartRequest.agent_live_approved`` and
``PipelineRunStartApprovedRequest.agent_live_approved`` were plain client-suppliable body
booleans with no signature and no cross-check against ``LiveApprovalStore``. The handler
computed ``skip_live_gate = req.agent_live_approved and not dry`` and then only entered the
approval branch ``if operator_requires_live_approval(user, dry) and not skip_live_gate`` —
so the caller's own boolean disabled the gate. The run was then stamped
``live_approval_status = "approved"`` with no ``LiveApprovalStore`` row and handed to
``_runner.start_async``, i.e. real execution. ``start_existing_pipeline_run`` repeated the
pattern. ``ado2gh/auth/service.py`` denies ``can_approve_live_execution`` to OPERATOR, yet
an authenticated OPERATOR could set this field themselves.

The fix removed the field from both models, and live authority is now derived only from
server-side state. Both routes are hardened differently, so the tests below differ:

* ``POST /v1/pipeline/runs`` — ``PipelineRunStartRequest`` sets ``extra="forbid"``, so a
  caller still self-certifying is rejected with 422 rather than silently ignored.
* ``POST /v1/pipeline/runs/{run_id}/start`` — the handler declares no body parameter at
  all (``PipelineRunStartApprovedRequest`` is deleted), so the body is never read. A
  parked run is released only by an approved ``LiveApprovalStore`` row.

Rejecting the body is not on its own proof that the gate works, so the second test also
establishes the positive half: the same run that the caller could not release is released
the moment an approver records a decision server-side.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.models import PlatformRole
from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db

ADMIN_PASSWORD = "AdminPass12345!"


@pytest.fixture
def operator_client(tmp_path, monkeypatch):
    """Authenticated OPERATOR against the accelerator, with the runner stubbed out."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "gap004.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    from ado2gh.api.pipeline_runner import PipelineRunStore
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app
    from services.accelerator_api.routes import _shared

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    svc = auth_routes._svc
    svc.bootstrap_admin("admin", ADMIN_PASSWORD, "Admin")
    svc.create_user("operator1", "OpPass12345!", PlatformRole.OPERATOR, "Operator One")

    monkeypatch.setattr(_shared._settings, "path", tmp_path / "ui_settings.json")
    _shared._settings.setup_profile(
        {
            "name": "GAP-004 profile",
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
        "/v1/auth/login", json={"username": "operator1", "password": "OpPass12345!"},
    )
    assert login.status_code == 200
    client.cookies.set("ado2gh_session", login.cookies.get("ado2gh_session"))

    with patch.object(_shared._runner, "start_async") as start_async:
        yield client, start_async
    PipelineRunStore._runs.clear()


def _admin_client() -> TestClient:
    """A second client logged in as the bootstrap admin, who can approve live runs."""
    from services.accelerator_api.main import app

    admin = TestClient(app)
    login = admin.post(
        "/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert login.status_code == 200
    admin.cookies.set("ado2gh_session", login.cookies.get("ado2gh_session"))
    return admin


def test_client_supplied_agent_live_approved_cannot_start_a_live_run(operator_client):
    client, start_async = operator_client

    resp = client.post(
        "/v1/pipeline/runs",
        json={
            "name": "GAP-004 self-certified run",
            "dry_run": False,
            "agent_live_approved": True,
        },
    )

    assert resp.json() != {"detail": "profile_not_active"}, (
        "fixture failed to activate a migration profile; the test proved nothing"
    )
    assert resp.status_code == 422, (
        "a caller self-certifying live approval in the request body must be rejected, "
        f"not answered {resp.status_code} with the claim quietly dropped"
    )
    locations = [tuple(err.get("loc", ())) for err in resp.json().get("detail", [])]
    assert ("body", "agent_live_approved") in locations, (
        "the 422 must be about the self-certifying field itself, not some unrelated "
        f"validation error; got {locations!r}"
    )
    start_async.assert_not_called()


def test_client_supplied_agent_live_approved_cannot_release_a_pending_run(operator_client):
    client, start_async = operator_client

    created = client.post(
        "/v1/pipeline/runs", json={"name": "GAP-004 queued run", "dry_run": False},
    )
    assert created.status_code == 200
    run_id = created.json()["run"]["id"]
    assert created.json()["run"]["status"] == "awaiting_approval", (
        "fixture expected the operator's live run to be parked awaiting approval"
    )
    start_async.assert_not_called()

    started = client.post(
        f"/v1/pipeline/runs/{run_id}/start", json={"agent_live_approved": True},
    )

    assert started.status_code == 409, (
        "a run parked in awaiting_approval was released by the caller asserting "
        f"agent_live_approved on itself; expected 409, got {started.status_code}"
    )
    start_async.assert_not_called()
    run = (started.json() or {}).get("run") or {}
    assert run.get("live_approval_status") != "approved", (
        "a run parked in awaiting_approval was marked approved by the caller "
        "asserting agent_live_approved on itself"
    )

    # The positive half: what the caller could not do for itself, an approver's
    # server-side decision does. This is the state that actually governs going live.
    admin = _admin_client()
    pending = admin.get("/v1/platform/approvals?status=pending")
    assert pending.status_code == 200
    approval = next(
        a for a in pending.json()["approvals"] if a["scope_id"] == run_id
    )
    decided = admin.post(
        f"/v1/platform/approvals/{approval['id']}/approve",
        json={"reason": "GAP-004: approver releases the run, the requester cannot"},
    )
    assert decided.status_code == 200
    start_async.assert_called_once()


def test_pending_live_run_is_queued_not_started_by_start(operator_client, tmp_path):
    """``/start`` routes an unapproved live run into the queue, and says so (GAP-066).

    ``awaiting_approval`` was the only status ``/start`` questioned, so a live run
    sitting at ``pending`` — the status ``PipelineRunStore.create`` assigns before
    anything decides whether it may go live — went straight to the runner. The
    refusal also has to leave a record an operator can act on (CA-004), which is
    the same approval-queue entry the create route raises.
    """
    from ado2gh.api.pipeline_runner import PipelineRunStore
    from ado2gh.state.audit_query import AuditEventFilters

    client, start_async = operator_client
    run = PipelineRunStore.create(
        "GAP-066 operator pending run", dry_run=False, phase="", wave_id=None,
    )

    refused = client.post(f"/v1/pipeline/runs/{run.id}/start")

    assert refused.status_code in (401, 403, 409), (
        f"an OPERATOR started a live run no approver released (HTTP "
        f"{refused.status_code})"
    )
    start_async.assert_not_called()
    events = create_state_db(str(tmp_path / "gap004.db")).search_audit_events(
        AuditEventFilters(event_type="platform.live_execution.requested"), limit=10,
    )
    assert any(run.id in (e["payload_json"] or "") for e in events), (
        "the refused live start left no audit record, so nothing tells an approver "
        "a run is waiting"
    )

    # The positive half: the approver's decision releases exactly this run.
    admin = _admin_client()
    pending = admin.get("/v1/platform/approvals?status=pending")
    approval = next(a for a in pending.json()["approvals"] if a["scope_id"] == run.id)
    decided = admin.post(
        f"/v1/platform/approvals/{approval['id']}/approve",
        json={"reason": "GAP-066: approver releases the pending live run"},
    )
    assert decided.status_code == 200
    start_async.assert_called_once()


def test_console_pipeline_run_body_is_accepted_in_full(operator_client):
    """``extra="forbid"`` makes every console body field a breaking change.

    ``PipelineRunStartRequest`` rejects unknown fields, so a field the console sends and
    the model does not declare is a 422 on every run the console starts — a break the
    Python suite would otherwise never see. This mirrors ``startPipelineRun`` in
    ``apps/migration-ui/src/lib/api.ts``; update it when that body changes.
    """
    from ado2gh.api.pipeline_runner import PipelineRunStore

    client, _ = operator_client
    reason = "exec sign-off, POC failure accepted"

    resp = client.post(
        "/v1/pipeline/runs",
        json={
            "name": "Console run",
            "dry_run": False,
            "phase": "",
            "wave_id": None,
            "steps": ["migrate_repos"],
            "repository_id": None,
            "migrate_deps_only": True,
            "override_reason": reason,
        },
    )

    assert resp.status_code == 200, (
        "the console's own request body must validate against "
        f"PipelineRunStartRequest; got {resp.status_code} {resp.json()!r}"
    )
    run_id = resp.json()["run"]["id"]
    assert PipelineRunStore.get(run_id).override_reason == reason, (
        "the gate override justification must reach the run, not be dropped in transit"
    )
    assert "override_reason" not in resp.json()["run"], (
        "free operator text is redacted where it is persisted; it must not ride back "
        "out unredacted in the run response"
    )
