"""GAP-015 (GAP-SEAM-01): the work-item producer and its consumers disagree on field names.

The sole work-item producer -- ``build_work_items_for_repos`` in
``ado2gh/api/migration_work_plan.py`` -- writes the *singular* keys ``scope``
(line 265) and ``blocker`` (line 271) onto every work item. All three consumers
read the *plural* keys ``scopes`` and ``blocked_reasons``, which are never set:

* ``services/agent/routes/session_routes.py:1301`` builds ``destructive_operations``
  with ``for scope in wi.get("scopes", [])``. The loop body never executes, so the
  list is unconditionally empty and CA-002's individual confirmation of
  ``repo_delete`` / ``workflow_delete`` / ``secret_delete`` / ``pipeline_disable``
  can never fire.
* The same handler serialises ``"scopes"`` and ``"blocked_reasons"`` onto the wire
  (lines 1315, 1317) from those same absent keys, so ``plan-summary`` reports zero
  scopes and zero blocker text for every work item.
* ``ado2gh/agents/migration_agent/nodes/executor/node.py:243`` and ``:370`` fill a
  blocked item's skip/audit ``details`` from ``wi.get("blocked_reasons", [])``, so
  the operator-facing reason the producer computed is discarded on every skip.
  (The executor is internally inconsistent: its third ``details`` reader, at
  ``node.py:304``, reads the singular ``blocker`` and works today.)

Reproduction: build work items with the real producer, then run the real consumers
over them and assert the two sides agree.

These tests are deliberately key-agnostic -- the gap can be closed at either choke
point (producer emits plural, or consumers read singular) and they must go green
either way, so nothing below asserts a work-item key spelling. Two consequences:

* ``MigrationScope`` has no destructive members, so the real producer cannot emit
  ``repo_delete`` unaided; ``_retarget_scope`` rewrites the scope *value* in place,
  leaving the producer's own key spelling untouched.
* The executor's reader sits inside a ~330-line node function that cannot be
  invoked without a live migration, so ``_executor_detail_keys`` lifts the key
  expression out of the source at the cited lines rather than hardcoding it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ado2gh.api.migration_work_plan import build_work_items_for_repos
from ado2gh.models import MigrationScope, RepoConfig
from services.agent.main import app as agent_app

# One of the DESTRUCTIVE_SCOPES declared at session_routes.py:1298.
DESTRUCTIVE_SCOPE = "repo_delete"

READY_REPO = "Payments/payments-api"
BLOCKED_REPO = "Payments/billing-svc"

_EXECUTOR_NODE = (
    Path(__file__).resolve().parents[2]
    / "ado2gh" / "agents" / "migration_agent" / "nodes" / "executor" / "node.py"
)
# Anchored on the blocked-item skip record so it matches exactly the two cited readers
# (node.py:243 and :370) and not the unrelated one at :304, which already reads singular.
_SKIP_DETAILS_READ = re.compile(
    r'"reason":\s*"blocked",\s*"details":\s*\[?\s*wi\.get\(\s*"([^"]+)"'
)


def _as_text(value: Any) -> str:
    """Flatten a str / list / anything into one string so shape never decides the assert."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return " ".join(_as_text(v) for v in value)
    return str(value)


def _build_work_items() -> list[dict[str, Any]]:
    """Real producer output: one repo with a ready scope, one blocked on pipelines."""
    repos = [
        RepoConfig(ado_project="Payments", ado_repo="payments-api", gh_org="contoso", gh_repo="payments-api"),
        RepoConfig(ado_project="Payments", ado_repo="billing-svc", gh_org="contoso", gh_repo="billing-svc"),
    ]
    return build_work_items_for_repos(
        repos,
        enabled_scopes_per_repo={
            READY_REPO: [MigrationScope.REPO.value],
            BLOCKED_REPO: [MigrationScope.PIPELINES.value],
        },
        repo_pipeline_counts={READY_REPO: 0, BLOCKED_REPO: 0},
    )


def _pick(items: list[dict[str, Any]], repo: str, status: str) -> dict[str, Any]:
    matches = [wi for wi in items if wi.get("repo") == repo and wi.get("status") == status]
    assert len(matches) == 1, f"expected exactly one {status!r} work item for {repo}, got {len(matches)}"
    return matches[0]


def _retarget_scope(work_item: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    """Point the item at a destructive scope without assuming which key holds the scope."""
    hits = 0
    for key, value in list(work_item.items()):
        if value == old:
            work_item[key] = new
            hits += 1
        elif isinstance(value, list) and old in value:
            work_item[key] = [new if v == old else v for v in value]
            hits += 1
    assert hits, f"producer emitted no field carrying the scope value {old!r}: {work_item!r}"
    return work_item


def _producer_blocker(work_item: dict[str, Any]) -> str:
    """The blocker text the producer computed, under either spelling."""
    text = _as_text(work_item.get("blocker") or work_item.get("blocked_reasons"))
    assert text, f"producer emitted no blocker text for a blocked work item: {work_item!r}"
    return text


def _executor_detail_keys() -> set[str]:
    """Work-item key(s) the executor actually reads for a blocked item's skip details."""
    source = _EXECUTOR_NODE.read_text(encoding="utf-8")
    return set(_SKIP_DETAILS_READ.findall(re.sub(r"\s*\n\s*", " ", source)))


@pytest.fixture
def plan_summary(monkeypatch):
    """Call the real GET /v1/sessions/{id}/plan-summary over producer-built work items."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "false")
    from ado2gh.agents.migration_agent import route_helpers

    def _call(work_items: list[dict[str, Any]]) -> dict[str, Any]:
        session_id = "ses_gap015_contract"
        monkeypatch.setitem(
            route_helpers._sessions,
            session_id,
            {
                "session_id": session_id,
                "migration_plan": {
                    "dry_run": True,
                    "repo_order": sorted({str(wi.get("repo", "")) for wi in work_items}),
                    "work_items": work_items,
                },
            },
        )
        response = TestClient(agent_app).get(f"/v1/sessions/{session_id}/plan-summary")
        assert response.status_code == 200, response.text
        return response.json()

    return _call


def test_plan_summary_flags_destructive_scope_on_producer_work_item(plan_summary):
    """CA-002: a work item on a destructive scope must reach destructive_operations."""
    destructive = _retarget_scope(
        _pick(_build_work_items(), READY_REPO, "ready"),
        MigrationScope.REPO.value,
        DESTRUCTIVE_SCOPE,
    )

    summary = plan_summary([destructive])

    assert summary["destructive_operations"], (
        f"plan-summary reported no destructive operations for a work item on scope "
        f"{DESTRUCTIVE_SCOPE!r} — producer and consumer disagree on the scope field "
        f"(GAP-015). work_item={destructive!r}"
    )
    assert summary["destructive_operations"][0]["repo"] == READY_REPO


def test_plan_summary_carries_producer_blocker_text(plan_summary):
    """A blocked work item's reason must survive onto the plan-summary wire."""
    blocked = _pick(_build_work_items(), BLOCKED_REPO, "blocked")
    blocker_text = _producer_blocker(blocked)

    wire_item = plan_summary([blocked])["work_items"][0]

    assert blocker_text in _as_text(wire_item.get("blocked_reasons") or wire_item), (
        f"plan-summary dropped the producer's blocker text {blocker_text!r} — producer "
        f"and consumer disagree on the blocker field (GAP-015). wire_item={wire_item!r}"
    )


def test_executor_skip_details_carry_producer_blocker_text():
    """The executor's skip/audit record must keep the blocker text, not an empty list."""
    blocked = _pick(_build_work_items(), BLOCKED_REPO, "blocked")
    blocker_text = _producer_blocker(blocked)

    detail_keys = _executor_detail_keys()
    assert detail_keys, (
        "no `\"details\": wi.get(...)` reader found in "
        f"{_EXECUTOR_NODE} — re-verify the GAP-015 evidence at node.py:243 and :370"
    )

    for key in sorted(detail_keys):
        details = _as_text(blocked.get(key, []))
        assert blocker_text in details, (
            f"executor reads skip details from wi[{key!r}], which the producer never "
            f"populates with the blocker text {blocker_text!r} — every blocked item is "
            f"audited with empty details (GAP-015). work_item={blocked!r}"
        )
