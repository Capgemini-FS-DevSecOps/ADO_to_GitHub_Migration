"""Guardrail integration for LangChain tool execution.

Wraps tools with validation checks: plan authorization, resource validation,
deletion confirmation, parameter validation, and GuardrailDecision logging.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, cast


class GuardrailAction(str, Enum):
    """What a guardrail evaluation tells the tool wrapper to do.

    Attributes:
        ALLOW: Run the tool.
        BLOCK: Refuse the call and return the reason to the agent.
        REQUIRE_CONFIRMATION: Refuse for now and ask the operator to confirm (CA-002).
    """

    ALLOW = "allow"
    BLOCK = "block"
    REQUIRE_CONFIRMATION = "require_confirmation"


@dataclass
class GuardrailDecision:
    """Result of a guardrail evaluation."""
    action: GuardrailAction
    agent_role: str
    tool_name: str
    operation_type: str
    target_resource: str
    reason: str
    plan_reference: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def allowed(self) -> bool:
        """Whether the tool call may proceed.

        Returns:
            ``True`` when the action is :attr:`GuardrailAction.ALLOW`.
        """
        return self.action == GuardrailAction.ALLOW

    @property
    def blocked(self) -> bool:
        """Whether the tool call was refused outright.

        Returns:
            ``True`` when the action is :attr:`GuardrailAction.BLOCK`.
        """
        return self.action == GuardrailAction.BLOCK

    @property
    def needs_confirmation(self) -> bool:
        """Whether the tool call needs explicit operator confirmation first (CA-002).

        Returns:
            ``True`` when the action is :attr:`GuardrailAction.REQUIRE_CONFIRMATION`.
        """
        return self.action == GuardrailAction.REQUIRE_CONFIRMATION

    @property
    def decision(self) -> str:
        """Backward-compatible alias for action.value."""
        return self.action.value

    def to_dict(self) -> dict[str, Any]:
        """Flatten the decision for the audit log and the agent-facing error payload.

        Returns:
            A JSON-serialisable dict of every field, with ``action`` as its string value.
        """
        return {
            "action": self.action.value,
            "agent_role": self.agent_role,
            "tool_name": self.tool_name,
            "operation_type": self.operation_type,
            "target_resource": self.target_resource,
            "reason": self.reason,
            "plan_reference": self.plan_reference,
            "timestamp": self.timestamp,
        }


# Operations that require explicit plan authorization
_WRITE_OPERATIONS = frozenset({
    "call_accelerator", "workflow_create", "github_api",
})

# Operations that require deletion confirmation
_DELETION_OPERATIONS = frozenset({
    "repo_delete", "pipeline_disable", "workflow_delete",
    "secret_delete", "environment_delete",
})

# Read-only operations that always pass guardrails
_READ_OPERATIONS = frozenset({
    "fetch_migration_status",
    "get_current_profile",
    "ado_api_query", "github_api_query",
    "generate_plan", "validate_workflow_conversion", "validate_workflow_syntax",
})


def _now() -> str:
    """Timestamp helper for guardrail records.

    Returns:
        The current UTC time in ISO 8601 form.
    """
    return datetime.now(timezone.utc).isoformat()


def evaluate_guardrail(  # noqa: PLR0913 - exception-register.md: authorization inputs, no existing model groups them
    agent_role: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    migration_plan: dict[str, Any] | None = None,
    plan_approved: bool = False,
    session: dict[str, Any] | None = None,
    approved_plan: dict[str, Any] | None = None,  # backward compat alias
) -> GuardrailDecision:
    """Decide whether one tool call may run, in order of severity.

    The checks are, in order: read-only operations always pass; accelerator and GitHub
    ``GET`` calls pass as reads; accelerator and GitHub *writes* are blocked while the
    session is in dry-run (CA-001); a GitHub ``DELETE`` needs operator confirmation;
    remaining write operations need an approved plan whose repo list contains the
    target resource; and the deletion operations need confirmation (CA-002).

    ``operation_type`` is derived from ``tool_name`` and never from the arguments, so a
    tool call cannot name itself read-only to skip the write checks.

    Args:
        agent_role: Which agent asked — orchestrator, planner, executor or validator.
        tool_name: The tool being invoked; also the operation identity.
        arguments: The tool arguments, read for ``target_resource``/``repository_id``
            and for the HTTP ``method`` of the accelerator and GitHub tools.
        migration_plan: The plan this call must be covered by, if there is one.
        plan_approved: Whether the operator has approved ``migration_plan``. This is
            approval state on record, not an execution mode, so it stays a boolean
            (operator decision, spec 013 increment 10).
        session: The live session, read only for its ``dry_run`` flag.
        approved_plan: Backward-compatible alias for passing an already-approved plan;
            supplying it implies ``plan_approved=True``.

    Returns:
        A :class:`GuardrailDecision` carrying the action, the operation identity, the
        target resource, a human-readable reason and, for plan-scoped decisions, the
        plan id. Unknown operations are allowed and say so in the reason.
    """
    # operation_type is derived from tool_name, never from LLM-supplied arguments —
    # otherwise a tool call could set operation_type to a read-only value to bypass
    # the write/deletion checks below.
    operation_type = tool_name
    target_resource = (
        arguments.get("target_resource")
        or arguments.get("repository_id")
        or ""
    )

    # Backward compat: approved_plan alias
    if approved_plan is not None and migration_plan is None:
        migration_plan = approved_plan
        plan_approved = True

    # Read-only operations always pass
    if tool_name in _READ_OPERATIONS or operation_type in _READ_OPERATIONS:
        return GuardrailDecision(
            action=GuardrailAction.ALLOW,
            agent_role=agent_role,
            tool_name=tool_name,
            operation_type=operation_type,
            target_resource=str(target_resource),
            reason="Read-only operation",
        )

    # call_accelerator: allow GET requests without approved plan (read-only)
    if tool_name == "call_accelerator":
        method = str(arguments.get("method", "POST")).upper()
        if method == "GET":
            return GuardrailDecision(
                action=GuardrailAction.ALLOW,
                agent_role=agent_role,
                tool_name=tool_name,
                operation_type=operation_type,
                target_resource=str(target_resource),
                reason="Read-only accelerator GET",
            )
        # Only an explicit `False` is live authority: a missing or malformed flag must
        # keep blocking writes rather than be coerced into "live" (GAP-076, CA-001).
        if session and session.get("dry_run") is not False:
            return GuardrailDecision(
                action=GuardrailAction.BLOCK,
                agent_role=agent_role,
                tool_name=tool_name,
                operation_type=operation_type,
                target_resource=str(target_resource),
                reason="Accelerator writes are blocked in dry-run mode — use GET only or run live after operator approval",
            )

    # github_api: GET is read-only; writes require approved plan (and live mode)
    if tool_name == "github_api":
        method = str(arguments.get("method", "GET")).upper()
        if method == "GET":
            return GuardrailDecision(
                action=GuardrailAction.ALLOW,
                agent_role=agent_role,
                tool_name=tool_name,
                operation_type=operation_type,
                target_resource=str(target_resource),
                reason="Read-only GitHub GET",
            )
        if session and session.get("dry_run") is not False:  # see the note above
            return GuardrailDecision(
                action=GuardrailAction.BLOCK,
                agent_role=agent_role,
                tool_name=tool_name,
                operation_type=operation_type,
                target_resource=str(target_resource),
                reason="GitHub writes are blocked in dry-run mode — use GET only or run live after operator approval",
            )
        if method == "DELETE":
            return GuardrailDecision(
                action=GuardrailAction.REQUIRE_CONFIRMATION,
                agent_role=agent_role,
                tool_name=tool_name,
                operation_type="workflow_delete",
                target_resource=str(target_resource),
                reason="GitHub DELETE requires explicit operator confirmation",
            )
        operation_type = "github_api_write"

    # Write operations require plan authorization
    if operation_type in _WRITE_OPERATIONS or tool_name in _WRITE_OPERATIONS:
        if not migration_plan:
            return GuardrailDecision(
                action=GuardrailAction.BLOCK,
                agent_role=agent_role,
                tool_name=tool_name,
                operation_type=operation_type,
                target_resource=str(target_resource),
                reason="No migration plan — write operations require an approved plan",
            )
        if not plan_approved:
            return GuardrailDecision(
                action=GuardrailAction.BLOCK,
                agent_role=agent_role,
                tool_name=tool_name,
                operation_type=operation_type,
                target_resource=str(target_resource),
                reason="Plan not approved by operator",
                plan_reference=str(migration_plan.get("plan_id", "")),
            )

        # Validate target resource exists in plan
        plan_repos = {r.get("id", r.get("name", "")) for r in migration_plan.get("repos", [])}
        if plan_repos:
            if not target_resource:
                return GuardrailDecision(
                    action=GuardrailAction.BLOCK,
                    agent_role=agent_role,
                    tool_name=tool_name,
                    operation_type=operation_type,
                    target_resource=str(target_resource),
                    reason="Write operation missing target_resource — cannot verify against approved plan scope",
                    plan_reference=str(migration_plan.get("plan_id", "")),
                )
            if str(target_resource) not in plan_repos:
                return GuardrailDecision(
                    action=GuardrailAction.BLOCK,
                    agent_role=agent_role,
                    tool_name=tool_name,
                    operation_type=operation_type,
                    target_resource=str(target_resource),
                    reason=f"Resource '{target_resource}' not in approved plan",
                    plan_reference=str(migration_plan.get("plan_id", "")),
                )

        return GuardrailDecision(
            action=GuardrailAction.ALLOW,
            agent_role=agent_role,
            tool_name=tool_name,
            operation_type=operation_type,
            target_resource=str(target_resource),
            reason="Plan authorized and resource validated",
            plan_reference=str(migration_plan.get("plan_id", "")),
        )

    # Deletion operations require confirmation
    if operation_type in _DELETION_OPERATIONS or tool_name in _DELETION_OPERATIONS:
        return GuardrailDecision(
            action=GuardrailAction.REQUIRE_CONFIRMATION,
            agent_role=agent_role,
            tool_name=tool_name,
            operation_type=operation_type,
            target_resource=str(target_resource),
            reason="Deletion operations require explicit operator confirmation",
        )

    # Default: allow unknown operations (they'll be caught by other checks)
    return GuardrailDecision(
        action=GuardrailAction.ALLOW,
        agent_role=agent_role,
        tool_name=tool_name,
        operation_type=operation_type,
        target_resource=str(target_resource),
        reason="Unknown operation — allowed by default",
    )


def wrap_tool_with_guardrail(
    tool_func: Callable,
    agent_role: str,
    *,
    session_getter: Callable[[], dict[str, Any]] | None = None,
    log_decision: Callable[[str, dict[str, Any]], None] | None = None,
) -> Callable:
    """Wrap a tool function with guardrail evaluation.

    The returned function checks guardrails before calling the original tool.
    If blocked, returns an error dict instead of calling the tool.
    If confirmation required, returns a confirmation request dict.
    Supports both sync and async (coroutine) tool functions.

    Args:
        tool_func: The tool implementation, sync or coroutine.
        agent_role: The role the wrapped tool is being handed to.
        session_getter: Returns the live session at call time; the guardrail reads the
            migration plan, its approval state and the dry-run flag from it.
        log_decision: Receives ``(session_id, decision_dict)`` for the audit trail.

    Returns:
        A callable with the same calling convention as ``tool_func`` (a coroutine
        function when ``tool_func`` is one) that either returns the tool's own result
        or, when the guardrail refuses, an error dict with ``error`` set to
        ``guardrail_blocked`` or ``confirmation_required``.
    """
    import asyncio

    def _evaluate(**kwargs: object) -> GuardrailDecision | None:
        session = session_getter() if session_getter else {}
        migration_plan = session.get("migration_plan")
        plan_approved = session.get("plan_approved", False)

        decision = evaluate_guardrail(
            agent_role=agent_role,
            # `_tool_name`, when a caller passes it, is always a str override
            # of the wrapped tool's name (see the pop() sites below).
            tool_name=cast("str", kwargs.get("_tool_name", tool_func.__name__)),
            arguments=kwargs,
            migration_plan=migration_plan,
            plan_approved=plan_approved,
            session=session,
        )

        if log_decision and session.get("session_id"):
            log_decision(session["session_id"], decision.to_dict())

        return decision

    def _check_decision(decision: GuardrailDecision | None) -> dict[str, Any] | None:
        """Record the tool call in metrics and turn a refusal into an error payload.

        Args:
            decision: The guardrail verdict, or ``None`` when none was produced.

        Returns:
            ``None`` when the tool may run, otherwise an error dict carrying
            ``guardrail_blocked`` or ``confirmation_required``, the reason and the
            serialised decision.
        """
        from ado2gh.agents.metrics import get_metrics_collector

        metrics = get_metrics_collector()

        # T102: Record tool call
        metrics.record_tool_call()

        if decision is None:
            return None
        if decision.blocked:
            # T102: Record guardrail block
            metrics.record_guardrail_block()
            return {
                "error": "guardrail_blocked",
                "message": decision.reason,
                "decision": decision.to_dict(),
            }
        if decision.needs_confirmation:
            return {
                "error": "confirmation_required",
                "message": decision.reason,
                "decision": decision.to_dict(),
            }
        return None

    if asyncio.iscoroutinefunction(tool_func):
        async def wrapped_async(**kwargs: object) -> object:
            decision = _evaluate(**kwargs)
            error = _check_decision(decision)
            if error:
                return error
            kwargs.pop("_tool_name", None)
            return await tool_func(**kwargs)
        return wrapped_async
    else:
        def wrapped(**kwargs: object) -> object:
            decision = _evaluate(**kwargs)
            error = _check_decision(decision)
            if error:
                return error
            kwargs.pop("_tool_name", None)
            return tool_func(**kwargs)
        return wrapped
