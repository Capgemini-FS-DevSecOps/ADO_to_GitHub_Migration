"""Regression check for register entry GAP-134 — a refused live-execution approval wrote no audit event.

The safeguard is that every real action AND every refusal is audited.
``require_approve_live_execution`` in ``ado2gh/api/platform_rbac.py`` refused an
unidentified caller with 401 and a caller who cannot approve live execution with
403, but neither branch wrote anything to the audit trail — the only record left
behind was a log line (CA-004 asks for more than that).

Both tests below call the guard directly with a stand-in request, exactly the
shape ``platform_user`` and the audit helper read off it (``request.state`` and
``request.url.path``), and check that the refusal leaves exactly one row in the
audit table naming the event, the reason and the capability.

Every identifier below is an obvious fake; no credential value appears (CA-003).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ado2gh.api.platform_rbac import (
    LIVE_APPROVAL_REFUSAL_EVENT,
    require_approve_live_execution,
)
from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.state.audit_query import AuditEventFilters
from ado2gh.state.factory import create_state_db


def _fake_request(path: str, user: PlatformUser | None) -> SimpleNamespace:
    """Build the minimal stand-in the guard and `platform_user` actually read."""
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        state=SimpleNamespace(platform_user=user),
    )


@pytest.fixture
def audit_env(tmp_path, monkeypatch):
    """Isolated state database and settings directory for one refusal check."""
    db_path = tmp_path / "gap134.db"
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    return str(db_path)


def _refusal_events(db_path: str) -> list[dict]:
    """Read the GAP-134 refusal events left in the audit table."""
    return create_state_db(db_path).search_audit_events(
        AuditEventFilters(event_type=LIVE_APPROVAL_REFUSAL_EVENT), limit=20,
    )


def test_unauthenticated_refusal_is_audited(audit_env):
    """A request with no identity is refused 401 and leaves one audit row."""
    request = _fake_request("/v1/platform/approvals/lve_1/decide", user=None)

    with pytest.raises(HTTPException) as exc:
        require_approve_live_execution(request)
    assert exc.value.status_code == 401

    events = _refusal_events(audit_env)
    assert len(events) == 1, f"expected exactly one refusal row, got {events!r}"
    payload = events[0]["payload_json"] or ""
    assert '"reason": "no_identity"' in payload
    assert '"capability": "can_approve_live_execution"' in payload
    assert "/v1/platform/approvals/lve_1/decide" in payload


def test_missing_capability_refusal_is_audited(audit_env):
    """A caller who cannot approve live execution is refused 403 and audited."""
    operator = PlatformUser("op-1", "operator1", PlatformRole.OPERATOR, "Operator One")
    request = _fake_request("/v1/platform/approvals/lve_2/decide", user=operator)

    with pytest.raises(HTTPException) as exc:
        require_approve_live_execution(request)
    assert exc.value.status_code == 403

    events = _refusal_events(audit_env)
    assert len(events) == 1, f"expected exactly one refusal row, got {events!r}"
    payload = events[0]["payload_json"] or ""
    assert '"reason": "missing_capability"' in payload
    assert '"capability": "can_approve_live_execution"' in payload
    assert events[0].get("actor") == "operator1"
