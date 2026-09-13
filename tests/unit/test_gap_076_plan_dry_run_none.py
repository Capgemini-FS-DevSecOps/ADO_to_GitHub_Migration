"""GAP-076/GAP-077 — an unspecified plan ``dry_run`` must never mean "run live".

``bool(None)`` is ``False`` and ``False`` means *live*, so every place that coerced a
plan's ``dry_run`` field with ``bool()`` read a missing decision as a request to write
to GitHub (CA-001). The planner copies that field straight out of LLM JSON, so the
value is attacker/model controlled — the reconciliation choke points have to treat
anything that is not a real boolean as "unspecified".
"""

import sqlite3

import pytest

from ado2gh.agents.migration_agent.guardrails import GuardrailAction, evaluate_guardrail
from ado2gh.agents.migration_agent.nodes.executor.plan import finalize_agent_migration_plan
from ado2gh.agents.migration_agent.nodes.planner_plan_builders import _build_migration_plan_from_llm
from ado2gh.agents.migration_agent.policies import resolve_execution_dry_run
from ado2gh.agents.migration_agent.session.store import SCHEMA, MigrationSessionStore
from ado2gh.agents.migration_agent.utils import resolve_dry_run

# A dry-run session whose operator may take runs live without a second approver.
APPROVER_SESSION = {"dry_run": True, "permissions": {"can_approve_live_execution": True}}
OPERATOR_SESSION = {"dry_run": True, "permissions": {}}


# ─── Finding A: policies.resolve_execution_dry_run ───────────────────


def test_plan_dry_run_none_keeps_the_session_dry_run():
    assert resolve_execution_dry_run(APPROVER_SESSION, {"dry_run": None}) is True


def test_plan_dry_run_string_false_keeps_the_session_dry_run():
    assert resolve_execution_dry_run(APPROVER_SESSION, {"dry_run": "false"}) is True


def test_plan_without_dry_run_key_keeps_the_session_dry_run():
    assert resolve_execution_dry_run(APPROVER_SESSION, {}) is True
    assert resolve_execution_dry_run(APPROVER_SESSION, None) is True


def test_non_boolean_session_dry_run_defaults_to_dry_run():
    session = {"dry_run": None, "permissions": {"can_approve_live_execution": True}}
    assert resolve_execution_dry_run(session, {"dry_run": None}) is True


def test_plan_dry_run_false_with_approver_still_goes_live():
    assert resolve_execution_dry_run(APPROVER_SESSION, {"dry_run": False}) is False


def test_plan_dry_run_false_without_permission_stays_dry_run():
    assert resolve_execution_dry_run(OPERATOR_SESSION, {"dry_run": False}) is True


# ─── Finding B: utils.resolve_dry_run (validator side) ───────────────


def test_resolve_dry_run_treats_null_plan_flag_as_unspecified():
    assert resolve_dry_run({"dry_run": True}, migration_plan={"dry_run": None}) is True


def test_resolve_dry_run_treats_null_executor_flag_as_unspecified():
    assert resolve_dry_run({"dry_run": True}, executor_result={"dry_run": None}) is True


def test_resolve_dry_run_treats_string_flag_as_unspecified():
    assert resolve_dry_run({"dry_run": True}, migration_plan={"dry_run": "false"}) is True


def test_resolve_dry_run_defaults_to_dry_run_without_any_flag():
    assert resolve_dry_run({}, migration_plan={"dry_run": None}) is True


def test_resolve_dry_run_still_prefers_a_real_executor_boolean():
    assert resolve_dry_run(
        {"dry_run": True},
        migration_plan={"dry_run": True},
        executor_result={"dry_run": False},
    ) is False


# ─── The source of the bad value: planner + plan finalisation ────────


def test_planner_normalises_a_null_model_dry_run():
    plan = _build_migration_plan_from_llm({"dry_run": None}, {"dry_run": True}, 1)
    assert plan["dry_run"] is True


def test_planner_rejects_a_non_boolean_model_dry_run():
    plan = _build_migration_plan_from_llm({"dry_run": "false"}, {"dry_run": False}, 1)
    assert plan["dry_run"] is True


def test_planner_keeps_a_real_boolean_from_the_model():
    plan = _build_migration_plan_from_llm({"dry_run": False}, {"dry_run": False}, 1)
    assert plan["dry_run"] is False


def test_finalize_writes_a_real_boolean_back_onto_the_plan():
    plan = finalize_agent_migration_plan({"dry_run": None}, {"dry_run": True})
    assert plan["dry_run"] is True


# ─── Same coercion, read as authority: write guard and persistence ───


@pytest.mark.parametrize("flag", [None, "false", 0, ""])
def test_guardrail_still_blocks_writes_when_the_session_flag_is_malformed(flag):
    for tool, method in (("github_api", "POST"), ("call_accelerator", "POST")):
        decision = evaluate_guardrail(
            "executor",
            tool,
            {"method": method, "repository_id": "Proj/Repo"},
            session={"dry_run": flag},
        )
        assert decision.action == GuardrailAction.BLOCK
        assert "dry-run" in decision.reason


def test_guardrail_lets_an_explicitly_live_session_through_the_dry_run_check():
    decision = evaluate_guardrail(
        "executor",
        "github_api",
        {"method": "POST", "repository_id": "Proj/Repo"},
        session={"dry_run": False},
        migration_plan={"repos": [{"id": "Proj/Repo"}]},
        plan_approved=True,
    )
    assert "dry-run" not in decision.reason


@pytest.fixture
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    class _MockDb:
        def _conn(self):
            return conn

    return MigrationSessionStore(db=_MockDb())


def _persisted_dry_run(store, session_id, flag):
    store.register_http_session({"session_id": session_id, "dry_run": flag})
    with store._db._conn() as conn:
        return conn.execute(
            "SELECT dry_run FROM agent_sessions WHERE session_id=?", (session_id,)
        ).fetchone()["dry_run"]


def test_malformed_session_flag_persists_as_a_dry_run(store):
    assert _persisted_dry_run(store, "ses_gap076_null", None) == 1
    assert _persisted_dry_run(store, "ses_gap076_str", "false") == 1


def test_explicitly_live_session_still_persists_as_live(store):
    assert _persisted_dry_run(store, "ses_gap076_live", False) == 0


def _patched_dry_run(store, session_id, flag):
    """``update_session`` is the second persistence path (persist_session_snapshot)."""
    _persisted_dry_run(store, session_id, True)
    store.update_session(session_id, dry_run=flag)
    with store._db._conn() as conn:
        return conn.execute(
            "SELECT dry_run FROM agent_sessions WHERE session_id=?", (session_id,)
        ).fetchone()["dry_run"]


def test_patching_a_malformed_flag_does_not_flip_the_row_to_live(store):
    # None is dropped by update_session; 0 and "" are the falsy values that reach the
    # column adapter — before GAP-076 they were written as live.
    assert _patched_dry_run(store, "ses_gap076_zero", 0) == 1
    assert _patched_dry_run(store, "ses_gap076_empty", "") == 1


def test_patching_an_explicit_live_flag_still_writes_live(store):
    assert _patched_dry_run(store, "ses_gap076_patch_live", False) == 0
