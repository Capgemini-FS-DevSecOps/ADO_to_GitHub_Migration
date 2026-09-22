"""Required fields are enforced and resume payloads must match (THR-09-003 / THR-09-004).

`required` was advertised to the console but never checked server-side, and the
human-in-the-loop (HITL) interrupt node relabelled whatever resume payload arrived
as an answer to the form it happened to be waiting on.
"""
from __future__ import annotations

import pytest

from ado2gh.agents.migration_agent.hitl import interrupt_node
from ado2gh.agents.migration_agent.hitl.form_fields import (
    default_required_for_type,
    field_dict_from_spec,
)
from ado2gh.agents.migration_agent.hitl.forms import (
    missing_required_form_fields,
    sanitize_form,
)
from ado2gh.agents.migration_agent.hitl.intake import build_plan_review_form, prepare_form_submission
from ado2gh.agents.migration_agent.hitl.schemas import INTAKE_FIELD_REGISTRY


def _field(form, name):
    return next(f for f in form["fields"] if f["name"] == name)


# ─── Per-type default and server-side enforcement (THR-09-003) ──────────


def test_default_required_is_explicit_per_field_type():
    assert default_required_for_type("text") is True
    assert default_required_for_type("select") is True
    assert default_required_for_type("checkbox") is False
    assert default_required_for_type("textarea") is False
    assert default_required_for_type(None) is True


def test_sanitize_form_applies_the_per_type_default():
    form = sanitize_form({
        "form_id": "custom",
        "fields": [
            {"name": "repo", "type": "text"},
            {"name": "notes", "type": "textarea"},
            {"name": "confirm", "type": "checkbox"},
            {"name": "must_tick", "type": "checkbox", "required": True},
            {"name": "optional_repo", "type": "text", "required": False},
        ],
    })
    assert _field(form, "repo")["required"] is True
    assert _field(form, "notes")["required"] is False
    assert _field(form, "confirm")["required"] is False
    assert _field(form, "must_tick")["required"] is True
    assert _field(form, "optional_repo")["required"] is False


def test_registry_specs_inherit_the_per_type_default():
    assert field_dict_from_spec(INTAKE_FIELD_REGISTRY["repository_id"])["required"] is True
    assert field_dict_from_spec(INTAKE_FIELD_REGISTRY["plan_notes"])["required"] is False
    assert field_dict_from_spec(INTAKE_FIELD_REGISTRY["plan_confirmed"])["required"] is False


def test_plan_review_form_never_demands_the_confirm_box():
    """Describing changes in Notes without ticking confirm is a supported answer."""
    form = build_plan_review_form({"migration_plan": {"repo_count": 1}})
    assert missing_required_form_fields(form, {"plan_confirmed": False, "plan_notes": "", "confirm_execute": False}) == []


def test_missing_required_form_fields_reports_blank_answers():
    form = sanitize_form({
        "form_id": "intake_repository_id",
        "fields": [{"name": "repository_id", "label": "Repository", "type": "text"}],
    })
    assert missing_required_form_fields(form, {"repository_id": "   "}) == ["repository_id"]
    assert missing_required_form_fields(form, {}) == ["repository_id"]
    assert missing_required_form_fields(form, {"repository_id": "Proj/App"}) == []


def test_missing_required_form_fields_accepts_console_aliases():
    form = sanitize_form({
        "form_id": "intake_repository_id",
        "fields": [{"name": "repository_id", "type": "text"}],
    })
    assert missing_required_form_fields(form, {"repository": "Proj/App"}) == []


def test_missing_required_form_fields_treats_false_as_an_answer():
    form = sanitize_form({
        "form_id": "custom",
        "fields": [{"name": "confirm_rollback", "type": "checkbox", "required": True}],
    })
    assert missing_required_form_fields(form, {"confirm_rollback": False}) == []


@pytest.mark.asyncio
async def test_prepare_form_submission_rejects_a_blank_required_field():
    form = sanitize_form({
        "form_id": "intake_repository_id",
        "fields": [{"name": "repository_id", "label": "Repository", "type": "text"}],
    })
    session: dict = {}
    outcome = await prepare_form_submission(session, form, {"repository_id": ""}, ensure_repo_valid=None)

    assert outcome["status"] == "form_incomplete"
    assert outcome["missing_fields"] == ["repository_id"]
    assert "Repository" in outcome["reply"]
    # Nothing from the incomplete submission reached the session.
    assert "plan_repository_id" not in session


@pytest.mark.asyncio
async def test_prepare_form_submission_rejects_an_unanswered_operator_decision():
    """An operator-input form cannot be dismissed by submitting nothing."""
    form = sanitize_form({
        "form_id": "operator_input_blockers_abc",
        "fields": [{
            "name": "resolution",
            "label": "How should we proceed?",
            "type": "select",
            "required": True,
            "options": [{"value": "replan", "label": "Replan"}],
        }],
    })
    session: dict = {"migration_plan": {"revision": 0}}
    outcome = await prepare_form_submission(session, form, {"resolution": ""}, ensure_repo_valid=None)

    assert outcome["status"] == "form_incomplete"
    assert outcome["missing_fields"] == ["resolution"]


# ─── The resume payload must name the pending form (THR-09-004) ─────────


def _human_input_state(form_id: str) -> dict:
    return {"pending_form": {"form_id": form_id, "title": "Confirm"}, "session": {}}


@pytest.mark.asyncio
async def test_human_input_node_rejects_a_payload_for_another_form(monkeypatch):
    monkeypatch.setattr(
        interrupt_node,
        "interrupt",
        lambda _payload: {"form_id": "intake_repository_id", "values": {"repository_id": "Proj/App"}},
    )
    with pytest.raises(ValueError, match="form_submission_mismatch"):
        await interrupt_node.human_input_node(_human_input_state("intake_plan_review"))


@pytest.mark.asyncio
async def test_human_input_node_accepts_the_matching_form(monkeypatch):
    monkeypatch.setattr(
        interrupt_node,
        "interrupt",
        lambda _payload: {"form_id": "intake_plan_review", "values": {"plan_confirmed": True}},
    )
    out = await interrupt_node.human_input_node(_human_input_state("intake_plan_review"))
    assert out["form_submission"]["form_id"] == "intake_plan_review"
    assert out["form_submission"]["values"] == {"plan_confirmed": True}


@pytest.mark.asyncio
async def test_human_input_node_labels_an_unlabelled_payload(monkeypatch):
    """A payload that names no form has nothing to mismatch and is still labelled."""
    monkeypatch.setattr(interrupt_node, "interrupt", lambda _payload: {"plan_confirmed": True})
    out = await interrupt_node.human_input_node(_human_input_state("intake_plan_review"))
    assert out["form_submission"] == {
        "form_id": "intake_plan_review",
        "values": {"plan_confirmed": True},
    }
