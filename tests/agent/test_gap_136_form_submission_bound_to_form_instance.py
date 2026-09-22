"""Regression check for register entry GAP-136 — a form submission must name the form
instance and plan revision it answers.

Before this fix, ``POST /v1/sessions/{id}/form-submit`` accepted any values for whatever
form happened to be pending, with no way to tell a stale browser tab's answer (to a form
the agent already replaced, or a plan the agent already rebuilt) from a fresh one. The fix
stamps every form built through ``sanitize_form`` with a fresh ``instance_id`` and, when a
plan exists, that plan's ``plan_revision``; a submission naming a mismatched instance id or
a form whose plan revision fell behind is refused with 409 ``stale_form`` before the pending
form is cleared, so the operator can resubmit against whatever is actually pending. Omitting
the instance id (an older console) is not treated as a mismatch by itself.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.agent.main import app
from services.agent.routes import _helpers, form_guard


class _FakeAuditBridge:
    """Captures every ``record`` call instead of writing to a database."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def record(self, action: str, **kwargs: Any) -> str:
        self.events.append((action, kwargs))
        return "aud_test"


@pytest.fixture
def fake_audit(monkeypatch):
    bridge = _FakeAuditBridge()
    monkeypatch.setattr(form_guard, "_audit", bridge)
    return bridge


@pytest.fixture
def agent_client(tmp_path, monkeypatch):
    monkeypatch.delenv("ADO2GH_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "gap136.db"))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    yield TestClient(app)
    _helpers._sessions.clear()
    _helpers._runs.clear()


def _seed_pending_plan_form(session_id: str, *, repo_count: int = 1) -> dict[str, Any]:
    """Put a real plan-review form (built the same way production does) on the session.

    Submitting ``plan_confirmed=True, confirm_execute=False`` against it answers with
    ``status: plan_ready`` and no further orchestrator/network work — the simplest real
    outcome the route supports, letting the test exercise the stale-form guard without
    stubbing the language model or the accelerator.
    """
    from ado2gh.agents.migration_agent.hitl.forms import sanitize_form
    from ado2gh.agents.migration_agent.hitl.intake import build_plan_review_form

    session = _helpers._sessions[session_id]
    session["migration_plan"] = {
        "phase": "poc",
        "repo_count": repo_count,
        "repos": ["Proj/repo1"],
        "blocked": False,
        "narrative": "Plan ready",
    }
    form = sanitize_form(build_plan_review_form(session), session)
    session["pending_form"] = form
    return form


def _create_session(agent_client: TestClient) -> str:
    created = agent_client.post("/v1/sessions", json={"profile_id": "lightweight"})
    assert created.status_code == 200, created.text
    return created.json()["session_id"]


def test_matching_form_instance_id_is_accepted(agent_client):
    sid = _create_session(agent_client)
    form = _seed_pending_plan_form(sid)

    submitted = agent_client.post(
        f"/v1/sessions/{sid}/form-submit",
        json={
            "form_instance_id": form["instance_id"],
            "values": {"plan_confirmed": True, "confirm_execute": False},
        },
    )

    assert submitted.status_code == 200, submitted.text


def test_mismatched_form_instance_id_is_refused_as_stale(agent_client, fake_audit):
    sid = _create_session(agent_client)
    form = _seed_pending_plan_form(sid)

    submitted = agent_client.post(
        f"/v1/sessions/{sid}/form-submit",
        json={
            "form_instance_id": "form_stale00000",
            "values": {"plan_confirmed": True, "confirm_execute": False},
        },
    )

    assert submitted.status_code == 409, submitted.text
    assert submitted.json()["detail"] == "stale_form"
    assert _helpers._sessions[sid]["pending_form"] == form, (
        "a refused submission must leave the pending form in place for the operator to retry"
    )
    stale_events = [e for e in fake_audit.events if e[0] == form_guard.STALE_FORM_SUBMISSION_EVENT]
    assert len(stale_events) == 1
    _, kwargs = stale_events[0]
    assert kwargs["metadata"]["expected_instance_id"] == form["instance_id"]
    assert kwargs["metadata"]["submitted_instance_id"] == "form_stale00000"


def test_omitted_form_instance_id_is_accepted(agent_client):
    """An older console that never sends the field is not rejected on that basis alone."""
    sid = _create_session(agent_client)
    _seed_pending_plan_form(sid)

    submitted = agent_client.post(
        f"/v1/sessions/{sid}/form-submit",
        json={"values": {"plan_confirmed": True, "confirm_execute": False}},
    )

    assert submitted.status_code == 200, submitted.text


def test_plan_revision_changed_under_the_form_is_refused_as_stale(agent_client, fake_audit):
    sid = _create_session(agent_client)
    form = _seed_pending_plan_form(sid)

    # The agent rebuilt the plan (e.g. after a replan) while the form was pending, without
    # a fresh form being issued — the stamped plan_revision on the pending form is now stale
    # even though its instance id still matches. `revision` is one of the fields
    # `plan_revision_key` hashes, so bumping it changes the digest.
    _helpers._sessions[sid]["migration_plan"]["revision"] = 1

    submitted = agent_client.post(
        f"/v1/sessions/{sid}/form-submit",
        json={
            "form_instance_id": form["instance_id"],
            "values": {"plan_confirmed": True, "confirm_execute": False},
        },
    )

    assert submitted.status_code == 409, submitted.text
    assert submitted.json()["detail"] == "stale_form"
    stale_events = [e for e in fake_audit.events if e[0] == form_guard.STALE_FORM_SUBMISSION_EVENT]
    assert len(stale_events) == 1
