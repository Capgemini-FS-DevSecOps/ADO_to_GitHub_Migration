"""Tests for resolvable migration plan blockers."""
from ado2gh.agents.migration_agent.hitl.blockers import (
    blocker_key,
    needs_blocker_resolution,
    outstanding_blockers,
    plan_revision_key,
    record_declined_blockers,
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
    plan = {"work_items": [_pipeline_blocked_item()], "revision": 0}
    key = blocker_key(_pipeline_blocked_item())
    session: dict = {}
    record_declined_blockers(session, [key], plan=plan)
    assert outstanding_blockers(plan, session) == []
    assert not needs_blocker_resolution(plan, session)


def test_declined_blocker_does_not_carry_into_the_next_plan_revision():
    """THR-09-005: a decision answers one plan revision, not every future one."""
    plan = {"work_items": [_pipeline_blocked_item()], "revision": 0}
    session: dict = {}
    record_declined_blockers(session, [blocker_key(_pipeline_blocked_item())], plan=plan)
    assert not needs_blocker_resolution(plan, session)

    replanned = {"work_items": [_pipeline_blocked_item()], "revision": 1}
    assert needs_blocker_resolution(replanned, session)
    assert outstanding_blockers(replanned, session)[0]["key"] == blocker_key(_pipeline_blocked_item())


def test_bare_blocker_key_no_longer_suppresses_a_blocker():
    """An unscoped key from an older session fails safe: the operator is asked again."""
    plan = {"work_items": [_pipeline_blocked_item()], "revision": 0}
    session = {"declined_blocker_resolutions": [blocker_key(_pipeline_blocked_item())]}
    assert needs_blocker_resolution(plan, session)


def test_plan_revision_key_ignores_execution_progress():
    """An approval survives the run it authorised: statuses are not part of plan identity."""
    plan = {
        "revision": 2,
        "dry_run": True,
        "repos": [{"id": "Proj/app"}],
        "work_items": [_pipeline_blocked_item()],
    }
    running = {
        "revision": 2,
        "dry_run": True,
        "repos": [{"id": "Proj/app"}],
        "work_items": [_pipeline_blocked_item(status="completed", blocker="")],
    }
    assert plan_revision_key(plan) == plan_revision_key(running)
    assert plan_revision_key({**plan, "revision": 3}) != plan_revision_key(plan)
    assert plan_revision_key({**plan, "dry_run": False}) != plan_revision_key(plan)
    assert plan_revision_key({**plan, "repos": [{"id": "Proj/other"}]}) != plan_revision_key(plan)
    assert plan_revision_key(None) == ""


def test_plan_revision_key_reads_a_malformed_dry_run_as_dry_run():
    """A malformed flag must not fingerprint as a live plan (GAP-076, CA-001)."""
    assert plan_revision_key({"revision": 1, "dry_run": "false"}) == plan_revision_key(
        {"revision": 1, "dry_run": True},
    )


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
