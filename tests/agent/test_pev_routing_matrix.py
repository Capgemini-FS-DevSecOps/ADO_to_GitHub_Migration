"""The full conditional-edge matrix of the PEV graph (COV-DRIFT-007).

`nodes/orchestrator.py` sits at 20 % with 292 statements unexercised, and the
drift report's point is that "the routing logic between orchestrator, planner,
executor and validator is largely unproven, including the retry and
iteration-cap branches (max 20 iterations, 3 PEV retries) that stop a runaway
loop".

The four ``_route_after_*`` functions are pure: they take an ``AgentState``
mapping and return a node name. So the whole matrix can be driven without an
LLM, a checkpointer or a compiled graph — this module stubs nothing because
there is nothing to stub.

`tests/unit/test_graph_structure.py` and `tests/unit/test_pev_loop.py` cover the
happy paths; what is added here is every remaining branch, both sides of each
threshold, and the precedence order between the branches of one router.
"""
from __future__ import annotations

import pytest

from ado2gh.agents.migration_agent.constants import MAX_ITERATIONS, MAX_PEV_RETRIES
from ado2gh.agents.migration_agent.graph import (
    NODE_EXECUTOR,
    NODE_FINALIZE,
    NODE_HUMAN_INPUT,
    NODE_ORCHESTRATOR,
    NODE_PLANNER,
    NODE_VALIDATOR,
    _needs_human_input,
    _route_after_execute_tools,
    _route_after_executor,
    _route_after_orchestrator,
    _route_after_planner,
    _route_after_validator,
)
from ado2gh.agents.migration_agent.hitl.intake import record_plan_approval

PLAN = {"id": "plan-1", "steps": [{"scope": "repo"}]}
FORM = {"id": "form-1", "fields": []}

# An approval is bound to the plan revision that was approved: the router
# recomputes a fingerprint of the plan carried in state and compares it against
# the fingerprint recorded at approval time, so a session must carry a matching
# fingerprint, not the bare "plan_approved" flag by itself, for the approval to
# still count (ado2gh/agents/migration_agent/hitl/intake.py, functions
# record_plan_approval and clear_stale_plan_approval). Building this session the
# same way production code does keeps the fixture honest about what "approved"
# means.
APPROVED_SESSION = {"migration_plan": PLAN}
record_plan_approval(APPROVED_SESSION)


# --------------------------------------------------------------------------
# _needs_human_input
# --------------------------------------------------------------------------


def test_no_pending_form_anywhere_needs_no_human_input():
    assert _needs_human_input({}) is False
    assert _needs_human_input({"session": {}}) is False


def test_a_form_pending_on_the_state_needs_human_input():
    assert _needs_human_input({"pending_form": FORM}) is True


def test_a_form_pending_only_on_the_session_still_needs_human_input():
    assert _needs_human_input({"session": {"pending_form": FORM}}) is True


def test_a_submitted_form_short_circuits_every_pending_form():
    """Once the operator has answered, the interrupt must not fire again."""
    state = {
        "pending_form": FORM,
        "session": {"pending_form": FORM},
        "form_submission": {"answer": "yes"},
    }
    assert _needs_human_input(state) is False


def test_an_empty_pending_form_is_not_a_pending_form():
    assert _needs_human_input({"pending_form": {}}) is False
    assert _needs_human_input({"session": {"pending_form": None}}) is False


# --------------------------------------------------------------------------
# _route_after_orchestrator
# --------------------------------------------------------------------------

ORCHESTRATOR_MATRIX = [
    # An approved plan plus a start signal is the only route into the executor.
    ({"start_execution": True, "migration_plan": PLAN}, "executor"),
    # A start signal with no plan has to go and make one first.
    ({"start_execution": True}, "planner"),
    ({"start_pev": True}, "planner"),
    ({"start_pev": True, "migration_plan": PLAN}, "planner"),
    # Nothing was started: the turn is a conversational one.
    ({}, "finalize"),
    ({"migration_plan": PLAN}, "finalize"),
    # should_return wins over everything except the human-input case below.
    ({"should_return": True}, "finalize"),
    ({"should_return": True, "start_execution": True, "migration_plan": PLAN}, "finalize"),
    ({"should_return": True, "start_pev": True}, "finalize"),
    # A form pending on a returning turn goes to the interrupt node.
    ({"should_return": True, "pending_form": FORM}, "human_input"),
    ({"should_return": True, "session": {"pending_form": FORM}}, "human_input"),
    # …unless the operator already answered it.
    (
        {"should_return": True, "pending_form": FORM, "form_submission": {"a": 1}},
        "finalize",
    ),
    # A form pending on a *started* returning turn still finalizes: the start
    # signal is checked first.
    (
        {"should_return": True, "start_execution": True, "pending_form": FORM},
        "finalize",
    ),
]


@pytest.mark.parametrize(
    "state,expected", ORCHESTRATOR_MATRIX,
    ids=[f"{i}-{e}" for i, (_, e) in enumerate(ORCHESTRATOR_MATRIX)],
)
def test_the_orchestrator_routes_this_state_here(state, expected):
    assert _route_after_orchestrator(state) == expected


def test_an_approved_plan_does_not_re_enter_the_planner():
    """Re-planning an approved plan would loop the graph."""
    assert _route_after_orchestrator(
        {"start_execution": True, "migration_plan": PLAN},
    ) == "executor"


def test_the_legacy_route_alias_is_the_orchestrator_router():
    assert _route_after_execute_tools is _route_after_orchestrator


# --------------------------------------------------------------------------
# _route_after_planner
# --------------------------------------------------------------------------

PLANNER_MATRIX = [
    # An explicit planner decision wins over everything downstream of it.
    ({"planner_next": "executor"}, "executor"),
    ({"planner_next": "orchestrator"}, "orchestrator"),
    ({"planner_next": "executor", "migration_plan": PLAN}, "executor"),
    # An unrecognised decision falls through to the plan-state rules.
    ({"planner_next": "nowhere"}, "finalize"),
    ({"planner_next": "nowhere", "migration_plan": PLAN}, "orchestrator"),
    # A clarification always goes back to the orchestrator to ask the operator.
    ({"pending_clarification": {"question": "which org?"}}, "orchestrator"),
    (
        {"pending_clarification": {"q": "?"}, "planner_next": "executor"},
        "orchestrator",
    ),
    # A plan plus an approval, from either source, reaches the executor. The
    # session-recorded approval must carry the plan-revision fingerprint that
    # record_plan_approval stamps on it, not just the bare "plan_approved" flag,
    # because a changed plan needs a fresh approval and the router revokes an
    # approval that does not match the plan revision it is being asked to
    # authorise.
    ({"migration_plan": PLAN, "start_execution": True}, "executor"),
    ({"migration_plan": PLAN, "session": APPROVED_SESSION}, "executor"),
    # A plan with no approval goes back for review.
    ({"migration_plan": PLAN}, "orchestrator"),
    ({"migration_plan": PLAN, "session": {"plan_approved": False}}, "orchestrator"),
    # No plan at all and nothing to say.
    ({}, "finalize"),
    ({"start_execution": True}, "finalize"),
    # should_return ends the turn, or collects a pending form first.
    ({"should_return": True}, "finalize"),
    ({"should_return": True, "migration_plan": PLAN, "start_execution": True}, "finalize"),
    ({"should_return": True, "pending_form": FORM}, "human_input"),
    ({"should_return": True, "session": {"pending_form": FORM}}, "human_input"),
    (
        {"should_return": True, "pending_form": FORM, "form_submission": {"a": 1}},
        "finalize",
    ),
]


@pytest.mark.parametrize(
    "state,expected", PLANNER_MATRIX,
    ids=[f"{i}-{e}" for i, (_, e) in enumerate(PLANNER_MATRIX)],
)
def test_the_planner_routes_this_state_here(state, expected):
    assert _route_after_planner(state) == expected


def test_an_unapproved_plan_never_reaches_the_executor():
    """CA-001: nothing runs until the plan has been approved."""
    assert _route_after_planner({"migration_plan": PLAN}) != "executor"


def test_a_changed_plan_revokes_a_stale_approval_and_returns_to_the_orchestrator():
    """An approval is bound to the plan revision that was approved.

    Once the plan carried in state is a different revision than the one the
    operator approved, the recorded approval no longer applies: a changed plan
    needs a fresh approval. The router must send the run back to the
    orchestrator step, where the operator reviews and re-approves, instead of
    forwarding the stale approval on to the executor (THR-09-002).
    """
    session = {"migration_plan": PLAN}
    record_plan_approval(session)

    revised_plan = {**PLAN, "revision": 1}
    state = {"migration_plan": revised_plan, "session": session}

    assert _route_after_planner(state) == "orchestrator"
    assert session["plan_approved"] is False


# --------------------------------------------------------------------------
# _route_after_executor
# --------------------------------------------------------------------------

EXECUTOR_MATRIX = [
    ({"executor_result": {"status": "ok"}}, "validator"),
    ({"executor_result": {"status": "failed"}}, "validator"),
    ({"pending_clarification": {"q": "?"}}, "orchestrator"),
    # A clarification outranks a result: the operator is asked before checking.
    (
        {"pending_clarification": {"q": "?"}, "executor_result": {"status": "ok"}},
        "orchestrator",
    ),
    ({}, "finalize"),
    ({"executor_result": {}}, "finalize"),
    # should_return outranks both.
    ({"should_return": True, "executor_result": {"status": "ok"}}, "finalize"),
    ({"should_return": True, "pending_clarification": {"q": "?"}}, "finalize"),
]


@pytest.mark.parametrize(
    "state,expected", EXECUTOR_MATRIX,
    ids=[f"{i}-{e}" for i, (_, e) in enumerate(EXECUTOR_MATRIX)],
)
def test_the_executor_routes_this_state_here(state, expected):
    assert _route_after_executor(state) == expected


def test_the_executor_has_no_edge_to_the_human_input_node():
    """A mid-execution interrupt would strand the run; the graph forbids it."""
    reachable = {
        _route_after_executor(state) for state, _ in EXECUTOR_MATRIX
    }
    assert "human_input" not in reachable
    assert _route_after_executor({"pending_form": FORM}) == "finalize"


# --------------------------------------------------------------------------
# _route_after_validator — the retry and iteration ceilings
# --------------------------------------------------------------------------


def test_a_passing_validation_goes_back_to_the_orchestrator():
    assert _route_after_validator({"validation_result": {"passed": True}}) == "orchestrator"


def test_a_passing_validation_ignores_an_exhausted_retry_count():
    """A pass is a pass; the ceilings only gate a replan."""
    assert _route_after_validator({
        "validation_result": {"passed": True},
        "pev_retry_count": 99,
        "iteration": 99,
    }) == "orchestrator"


def test_a_failing_validation_replans():
    assert _route_after_validator({"validation_result": {"passed": False}}) == "planner"


def test_a_missing_validation_result_replans():
    assert _route_after_validator({}) == "planner"


def test_an_escalation_request_stops_the_loop():
    assert _route_after_validator({
        "validation_result": {"passed": False},
        "validation_feedback": {"escalate": True},
    }) == "orchestrator"


@pytest.mark.parametrize(
    "retry_count,expected",
    [
        (0, "planner"),
        (MAX_PEV_RETRIES - 1, "planner"),
        (MAX_PEV_RETRIES, "orchestrator"),
        (MAX_PEV_RETRIES + 5, "orchestrator"),
    ],
)
def test_the_pev_retry_ceiling_is_both_sides_of_three(retry_count, expected):
    assert _route_after_validator({
        "validation_result": {"passed": False},
        "pev_retry_count": retry_count,
    }) == expected


@pytest.mark.parametrize(
    "iteration,expected",
    [
        (0, "planner"),
        (MAX_ITERATIONS - 1, "planner"),
        (MAX_ITERATIONS, "orchestrator"),
        (MAX_ITERATIONS + 5, "orchestrator"),
    ],
)
def test_the_iteration_ceiling_is_both_sides_of_twenty(iteration, expected):
    assert _route_after_validator({
        "validation_result": {"passed": False},
        "iteration": iteration,
    }) == expected


def test_a_session_supplied_iteration_cap_overrides_the_default():
    """A session may cap the loop lower than the shipped default, never higher."""
    state = {
        "validation_result": {"passed": False},
        "iteration": 5,
        "max_iterations": 5,
    }
    assert _route_after_validator(state) == "orchestrator"
    assert _route_after_validator({**state, "max_iterations": 6}) == "planner"


@pytest.mark.parametrize("value", [None, "", 0])
def test_a_missing_or_falsy_counter_is_read_as_zero(value):
    assert _route_after_validator({
        "validation_result": {"passed": False},
        "pev_retry_count": value,
        "iteration": value,
    }) == "planner"


def test_a_falsy_max_iterations_falls_back_to_the_shipped_ceiling():
    state = {
        "validation_result": {"passed": False},
        "iteration": MAX_ITERATIONS - 1,
        "max_iterations": 0,
    }
    assert _route_after_validator(state) == "planner"
    assert _route_after_validator({**state, "iteration": MAX_ITERATIONS}) == "orchestrator"


def test_should_return_ends_the_turn_whatever_the_validation_said():
    for extra in (
        {"validation_result": {"passed": True}},
        {"validation_result": {"passed": False}},
        {"validation_feedback": {"escalate": True}},
    ):
        assert _route_after_validator({"should_return": True, **extra}) == "finalize"


# --------------------------------------------------------------------------
# Every router only ever names a node the graph wired for it
# --------------------------------------------------------------------------

ALLOWED = {
    _route_after_orchestrator: {"planner", "executor", "human_input", "finalize"},
    _route_after_planner: {"orchestrator", "executor", "human_input", "finalize"},
    _route_after_executor: {"validator", "orchestrator", "finalize"},
    _route_after_validator: {"planner", "orchestrator", "finalize"},
}

STATES = [
    {},
    {"should_return": True},
    {"should_return": True, "pending_form": FORM},
    {"start_execution": True},
    {"start_pev": True},
    {"start_execution": True, "migration_plan": PLAN},
    {"migration_plan": PLAN},
    {"migration_plan": PLAN, "session": {"plan_approved": True}},
    {"pending_clarification": {"q": "?"}},
    {"planner_next": "executor"},
    {"executor_result": {"status": "ok"}},
    {"validation_result": {"passed": True}},
    {"validation_result": {"passed": False}, "pev_retry_count": 99},
]


@pytest.mark.parametrize("router", list(ALLOWED), ids=lambda r: r.__name__)
def test_a_router_never_names_a_node_its_edge_map_does_not_carry(router):
    for state in STATES:
        assert router(state) in ALLOWED[router], (
            f"{router.__name__} returned {router(state)!r} for {state}"
        )


def test_the_node_names_the_graph_wires_are_the_ones_the_routers_return():
    """The edge maps translate router strings to these constants."""
    assert {
        NODE_ORCHESTRATOR, NODE_PLANNER, NODE_EXECUTOR,
        NODE_VALIDATOR, NODE_HUMAN_INPUT, NODE_FINALIZE,
    } >= {"orchestrator", "planner", "executor", "validator", "human_input", "finalize"}


def test_the_documented_ceilings_are_the_ones_the_router_enforces():
    """CLAUDE.md § Agent PEV: max 20 total iterations, 3 PEV retries per cycle."""
    assert MAX_ITERATIONS == 20
    assert MAX_PEV_RETRIES == 3
