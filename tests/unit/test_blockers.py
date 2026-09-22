"""Tests for resolvable migration plan blockers."""
from types import SimpleNamespace

from ado2gh.agents.migration_agent.hitl.blockers import (
    blocker_key,
    needs_blocker_resolution,
    outstanding_blockers,
    plan_revision_key,
    record_declined_blockers,
    sanitize_plan_for_operator_view,
)
from ado2gh.api.settings_store import SettingsStore
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


def test_plan_revision_key_changes_when_target_org_or_repo_changes():
    """A retarget to a different GitHub destination must invalidate the approval (THR-09-002)."""
    plan = {
        "revision": 1,
        "repos": [{"id": "Proj/app", "gh_org": "acme", "gh_repo": "app"}],
    }
    retargeted_org = {
        "revision": 1,
        "repos": [{"id": "Proj/app", "gh_org": "other", "gh_repo": "app"}],
    }
    retargeted_repo = {
        "revision": 1,
        "repos": [{"id": "Proj/app", "gh_org": "acme", "gh_repo": "renamed"}],
    }
    assert plan_revision_key(retargeted_org) != plan_revision_key(plan)
    assert plan_revision_key(retargeted_repo) != plan_revision_key(plan)


def test_plan_revision_key_changes_with_scope_set():
    """A scope enabled or disabled at plan or repository level changes what a live run may touch."""
    plan = {
        "revision": 1,
        "enabled_scopes": ["repo"],
        "repos": [{"id": "Proj/app", "enabled_scopes": ["repo"]}],
    }
    plan_scope_added = {
        "revision": 1,
        "enabled_scopes": ["repo", "pipelines"],
        "repos": [{"id": "Proj/app", "enabled_scopes": ["repo"]}],
    }
    repo_scope_added = {
        "revision": 1,
        "enabled_scopes": ["repo"],
        "repos": [{"id": "Proj/app", "enabled_scopes": ["repo", "secrets"]}],
    }
    assert plan_revision_key(plan_scope_added) != plan_revision_key(plan)
    assert plan_revision_key(repo_scope_added) != plan_revision_key(plan)


def test_plan_revision_key_changes_with_work_item_destination_or_scope():
    """A work item retargeted to a different repository or scope changes the key (THR-09-002)."""
    plan = {
        "revision": 1,
        "work_items": [
            {"repo": "Proj/app", "scope": "repo", "github_org": "acme", "github_repo": "app"},
        ],
    }
    retargeted = {
        "revision": 1,
        "work_items": [
            {"repo": "Proj/app", "scope": "repo", "github_org": "acme", "github_repo": "different"},
        ],
    }
    rescoped = {
        "revision": 1,
        "work_items": [
            {"repo": "Proj/app", "scope": "pipelines", "github_org": "acme", "github_repo": "app"},
        ],
    }
    assert plan_revision_key(retargeted) != plan_revision_key(plan)
    assert plan_revision_key(rescoped) != plan_revision_key(plan)


def test_plan_revision_key_changes_with_config_path(monkeypatch):
    """The config path resolves the organisation defaults a live write applies against.

    Changing it changes what a plan-scoped write actually does, so it must be
    part of the fingerprint even though it lives outside the plan document
    itself.
    """
    plan = {"revision": 1, "repos": [{"id": "Proj/app"}]}

    monkeypatch.setattr(
        SettingsStore,
        "load",
        lambda self: SimpleNamespace(advanced=SimpleNamespace(config_path="migration.yaml")),
    )
    key_default = plan_revision_key(plan)

    monkeypatch.setattr(
        SettingsStore,
        "load",
        lambda self: SimpleNamespace(advanced=SimpleNamespace(config_path="other.yaml")),
    )
    key_other = plan_revision_key(plan)

    assert key_default != key_other


def test_plan_revision_key_is_stable_under_list_and_dict_reordering():
    """Re-listing the same repositories, work items or scopes must not change identity."""
    plan = {
        "revision": 1,
        "enabled_scopes": ["repo", "pipelines"],
        "repos": [
            {"id": "Proj/app", "gh_org": "acme", "gh_repo": "app"},
            {"id": "Proj/other", "gh_org": "acme", "gh_repo": "other"},
        ],
        "work_items": [
            {"repo": "Proj/app", "scope": "repo"},
            {"repo": "Proj/other", "scope": "pipelines"},
        ],
    }
    reordered = {
        "revision": 1,
        "enabled_scopes": ["pipelines", "repo"],
        "repos": [
            {"gh_repo": "other", "gh_org": "acme", "id": "Proj/other"},
            {"id": "Proj/app", "gh_repo": "app", "gh_org": "acme"},
        ],
        "work_items": [
            {"scope": "pipelines", "repo": "Proj/other"},
            {"scope": "repo", "repo": "Proj/app"},
        ],
    }
    assert plan_revision_key(plan) == plan_revision_key(reordered)


def test_plan_revision_key_changes_when_source_coordinates_or_gh_target_change():
    """A retargeted ADO source or `gh_target` destination must invalidate the approval.

    `resolve_repo_context` (nodes/executor/scope.py) reads a repository's or work
    item's `project`/`repo_name` independently of its bare `id`/`repo` string, and
    reads a work item's `gh_target` as a GitHub destination in its own right when
    `github_org`/`github_repo` are unset. None of the three changed what the plan's
    `id`/`repo`, `gh_org` or `gh_repo` fields said, so the key had to cover them
    directly or a live write could retarget without invalidating approval.
    """
    plan = {
        "revision": 1,
        "repos": [{"id": "Proj/app", "project": "Proj", "repo_name": "app"}],
        "work_items": [{"repo": "Proj/app", "scope": "repo", "project": "Proj", "repo_name": "app"}],
    }
    repo_project_changed = {
        "revision": 1,
        "repos": [{"id": "Proj/app", "project": "OtherProj", "repo_name": "app"}],
        "work_items": [{"repo": "Proj/app", "scope": "repo", "project": "Proj", "repo_name": "app"}],
    }
    work_item_repo_name_changed = {
        "revision": 1,
        "repos": [{"id": "Proj/app", "project": "Proj", "repo_name": "app"}],
        "work_items": [{"repo": "Proj/app", "scope": "repo", "project": "Proj", "repo_name": "renamed"}],
    }
    work_item_gh_target_changed = {
        "revision": 1,
        "repos": [{"id": "Proj/app", "project": "Proj", "repo_name": "app"}],
        "work_items": [
            {"repo": "Proj/app", "scope": "repo", "project": "Proj", "repo_name": "app", "gh_target": "acme/app"},
        ],
    }
    assert plan_revision_key(repo_project_changed) != plan_revision_key(plan)
    assert plan_revision_key(work_item_repo_name_changed) != plan_revision_key(plan)
    assert plan_revision_key(work_item_gh_target_changed) != plan_revision_key(plan)
