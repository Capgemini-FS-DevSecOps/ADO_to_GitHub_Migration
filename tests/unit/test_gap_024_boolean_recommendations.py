"""Regression check for register entry GAP-024: boolean form recommendations stay JSON booleans on the wire.

`str(False)` is `"False"`, and every browser reads that as truthy, so stringifying a
recommendation *not* to do something turned it into a recommendation to do it. The
console keeps `parseBooleanValue` as defence in depth; these tests pin the server half.
"""
from __future__ import annotations

from ado2gh.agents.migration_agent.hitl.form_fields import (
    build_field_recommendations,
    field_dict_from_spec,
    normalize_recommended_value,
)
from ado2gh.agents.migration_agent.hitl.forms import sanitize_form
from ado2gh.agents.migration_agent.hitl.intake import build_plan_review_form
from ado2gh.agents.migration_agent.hitl.schemas import INTAKE_FIELD_REGISTRY, IntakeFieldSpec


def _field(form, name):
    return next(f for f in form["fields"] if f["name"] == name)


def test_normalize_recommended_value_keeps_booleans():
    assert normalize_recommended_value(False) is False
    assert normalize_recommended_value(True) is True
    assert normalize_recommended_value("main") == "main"
    assert normalize_recommended_value("  main  ") == "main"
    assert normalize_recommended_value("") is None
    assert normalize_recommended_value(None) is None


def test_normalize_recommended_value_truncates_long_strings():
    assert len(normalize_recommended_value("x" * 500)) == 120


def test_field_dict_from_spec_keeps_a_false_recommendation_false():
    spec = IntakeFieldSpec(
        name="confirm_execute",
        label="Start after confirm",
        field_type="checkbox",
        recommended_value=False,
    )
    field = field_dict_from_spec(spec)
    assert field["recommended_value"] is False
    assert field["recommended_value"] != "False"


def test_field_dict_from_spec_keeps_a_false_planner_hint_false():
    field = field_dict_from_spec(
        INTAKE_FIELD_REGISTRY["confirm_execute"],
        planner_context={"field_recommendations": {"confirm_execute": {"recommended_value": False}}},
    )
    assert field["recommended_value"] is False


def test_sanitize_form_does_not_stringify_boolean_recommendations():
    form = sanitize_form({
        "form_id": "custom",
        "title": "t",
        "fields": [
            {"name": "confirm", "label": "Confirm", "type": "checkbox", "recommended_value": False},
            {"name": "go", "label": "Go", "type": "checkbox", "recommended_value": True},
            {"name": "repo", "label": "Repo", "type": "text", "recommended_value": " Proj/App "},
        ],
    })
    assert _field(form, "confirm")["recommended_value"] is False
    assert _field(form, "go")["recommended_value"] is True
    assert _field(form, "repo")["recommended_value"] == "Proj/App"


def test_sanitize_form_is_idempotent_for_boolean_recommendations():
    once = sanitize_form({
        "form_id": "custom",
        "fields": [{"name": "confirm", "type": "checkbox", "recommended_value": False}],
    })
    assert sanitize_form(once)["fields"][0]["recommended_value"] is False


def test_execution_mode_recommendation_is_a_boolean():
    live = build_field_recommendations({"dry_run": False}, missing_fields=["dry_run"])
    dry = build_field_recommendations({"dry_run": True}, missing_fields=["dry_run"])
    assert live["dry_run"]["recommended_value"] is False
    assert dry["dry_run"]["recommended_value"] is True


def test_execution_mode_recommendation_defaults_to_dry_run_when_malformed():
    """A malformed session flag is not a decision to go live (GAP-076, CA-001)."""
    recs = build_field_recommendations({"dry_run": "false"}, missing_fields=["dry_run"])
    assert recs["dry_run"]["recommended_value"] is True


def test_confirm_execute_recommendation_is_a_boolean():
    recs = build_field_recommendations({}, missing_fields=["plan_confirmed"])
    assert recs["confirm_execute"]["recommended_value"] is False


def test_plan_review_form_carries_a_real_boolean_end_to_end():
    form = build_plan_review_form({"migration_plan": {"repo_count": 1}, "dry_run": True})
    assert _field(form, "confirm_execute")["recommended_value"] is False
