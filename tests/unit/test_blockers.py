"""Tests for resolvable migration plan blockers."""
from ado2gh.agents.migration_agent.hitl.blockers import (
    blocker_key,
    needs_blocker_resolution,
    outstanding_blockers,
    sanitize_plan_for_operator_view,
)
from ado2gh.models import MigrationScope


def _pipeline_blocked_item(**overrides):
    item = {
        "scope": MigrationScope.PIPELINES.value,
        "label": "Proj/app — Convert pipelines → GitHub Actions",
        "status": "blocked",
        "blocker": "No pipelines in inventory — run pipeline inventory first",
        "repo": "Proj/app",
    }
    item.update(overrides)
    return item


def test_blocker_key_is_generic():
    wi = _pipeline_blocked_item()
    key = blocker_key(wi)
    assert key
    assert key.startswith("pipelines:Proj/app:")


def test_outstanding_blockers_excludes_declined():
    plan = {"work_items": [_pipeline_blocked_item()]}
    key = blocker_key(_pipeline_blocked_item())
    session = {"declined_blocker_resolutions": [key]}
    assert outstanding_blockers(plan, session) == []
    assert not needs_blocker_resolution(plan, session)


def test_needs_blocker_resolution_when_work_item_blocked():
    plan = {"work_items": [_pipeline_blocked_item()]}
    session: dict = {}
    assert needs_blocker_resolution(plan, session)
    blockers = outstanding_blockers(plan, session)
    assert blockers[0]["key"]


def test_sanitize_plan_hides_blocked_work_items():
    plan = {
        "work_items": [
            _pipeline_blocked_item(),
            {"scope": "repo", "label": "Proj/app — Migrate repository", "status": "ready", "repo": "Proj/app"},
        ],
        "blocked_items": [{"label": "hidden"}],
    }
    sanitized = sanitize_plan_for_operator_view(plan, {})
    assert len(sanitized["work_items"]) == 1
    assert sanitized["work_items"][0]["scope"] == "repo"
    assert "blocked_items" not in sanitized
