"""Pin the migration agent's new protocol enums to the exact wire strings they replaced.

Code review items A-F (2026-09) named module constants and ``str, Enum`` classes
for values that used to be repeated literals. These tests pin every new member to
the literal string or number the console, the accelerator routes, and the graph's
own persisted state already expect, so a rename here cannot silently change what
goes out over the wire.
"""
from __future__ import annotations

from ado2gh.agents.migration_agent.constants import CONTEXT_TOKEN_BUDGET, MAX_ITERATIONS
from ado2gh.agents.migration_agent.graph.state import AgentEventKind, AgentMessageType, AgentRole
from ado2gh.agents.migration_agent.hitl.form_fields import (
    FORM_DESCRIPTION_MAX_CHARS,
    FORM_FIELD_NAME_MAX_CHARS,
    FORM_ID_MAX_CHARS,
    FORM_OPTION_MAX_CHARS,
    FORM_PLACEHOLDER_MAX_CHARS,
    FORM_SUGGESTION_MAX_CHARS,
)
from ado2gh.agents.migration_agent.hitl.schemas import (
    OPERATOR_BLOCKER_SUMMARY_MAX_ITEMS,
    OPERATOR_CONTEXT_MAX_ITEMS,
    OPERATOR_DIGEST_MAX_CHARS,
    OPERATOR_IDENTIFIER_MAX_CHARS,
    MigrationFailureCode,
    OperatorIntent,
)
from ado2gh.agents.migration_agent.session.state import SessionState


def test_form_limits_match_the_lengths_the_console_form_used():
    assert FORM_FIELD_NAME_MAX_CHARS == 40
    assert FORM_OPTION_MAX_CHARS == 60
    assert FORM_DESCRIPTION_MAX_CHARS == 200
    assert FORM_PLACEHOLDER_MAX_CHARS == 120
    assert FORM_SUGGESTION_MAX_CHARS == 120
    assert FORM_ID_MAX_CHARS == 60


def test_operator_input_limits_match_the_previous_magic_numbers():
    assert OPERATOR_IDENTIFIER_MAX_CHARS == 48
    assert OPERATOR_DIGEST_MAX_CHARS == 10
    assert OPERATOR_BLOCKER_SUMMARY_MAX_ITEMS == 6
    assert OPERATOR_CONTEXT_MAX_ITEMS == 8


def test_migration_failure_code_matches_the_error_codes_the_agent_dispatches_on():
    assert MigrationFailureCode.MIGRATION_IN_PROGRESS.value == "migration_in_progress"
    assert MigrationFailureCode.FR036.value == "fr036"
    assert MigrationFailureCode.ACTIVE_LIVE_MIGRATION.value == "active_live_migration"
    # These are used as plain strings in dicts persisted to the session and
    # compared against by the console, so the enum must be a str subclass.
    assert MigrationFailureCode.MIGRATION_IN_PROGRESS == "migration_in_progress"


def test_agent_event_kind_matches_the_kind_values_streamed_to_the_console():
    assert AgentEventKind.MESSAGE.value == "message"
    assert AgentEventKind.THINKING.value == "thinking"
    assert AgentEventKind.TOOL_CALL.value == "tool_call"
    assert AgentEventKind.TOOL_RESULT.value == "tool_result"


def test_agent_role_matches_the_subagent_names_streamed_to_the_console():
    assert AgentRole.ORCHESTRATOR.value == "orchestrator"
    assert AgentRole.PLANNER.value == "planner"
    assert AgentRole.EXECUTOR.value == "executor"
    assert AgentRole.VALIDATOR.value == "validator"


def test_agent_message_type_matches_the_inter_agent_message_wire_value():
    assert AgentMessageType.VALIDATION_RESULT.value == "validation_result"


def test_session_state_and_operator_intent_are_unchanged_by_this_refactor():
    # Not new — these already existed. Pinned here so the enum-substitution
    # work in orchestrator.py, intent.py and validator.py can be checked
    # against something, without re-declaring session/state.py's own tests.
    assert SessionState.IDLE.value == "idle"
    assert SessionState.THINKING.value == "thinking"
    assert SessionState.PLANNING.value == "planning"
    assert SessionState.EXECUTING.value == "executing"
    assert SessionState.VALIDATING.value == "validating"
    assert OperatorIntent.GENERAL_CHAT.value == "general_chat"


def test_context_token_budget_matches_the_previous_literal():
    assert CONTEXT_TOKEN_BUDGET == 32000


def test_max_iterations_matches_the_previous_literal():
    assert MAX_ITERATIONS == 20
