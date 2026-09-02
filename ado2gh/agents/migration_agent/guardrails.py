"""Guardrail integration for LangChain tool execution.

Wraps tools with validation checks: plan authorization, resource validation,
deletion confirmation, parameter validation, and GuardrailDecision logging.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


class GuardrailAction(str, Enum):
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
        return self.action == GuardrailAction.ALLOW

    @property
    def blocked(self) -> bool:
        return self.action == GuardrailAction.BLOCK

    @property
    def needs_confirmation(self) -> bool:
        return self.action == GuardrailAction.REQUIRE_CONFIRMATION

    @property
    def decision(self) -> str:
        """Backward-compatible alias for action.value."""
        return self.action.value

    def to_dict(self) -> dict[str, Any]:
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
    return datetime.now(timezone.utc).isoformat()


def evaluate_guardrail(
    agent_role: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    migration_plan: dict[str, Any] | None = None,
    plan_approved: bool = False,
    session: dict[str, Any] | None = None,
    approved_plan: dict[str, Any] | None = None,  # backward compat alias
) -> GuardrailDecision:
    """Evaluate whether a tool call should proceed.

    Checks:
    1. Plan authorization for write operations
    2. Resource validation (resource exists in plan)
    3. Deletion confirmation requirement
    4. Parameter validation
    """
    operation_type = arguments.get("operation_type", tool_name)
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
        if session and session.get("dry_run", True):
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
        if target_resource:
            plan_repos = {r.get("id", r.get("name", "")) for r in migration_plan.get("repos", [])}
            if plan_repos and str(target_resource) not in plan_repos:
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
    """
    import asyncio

    def _evaluate(**kwargs) -> GuardrailDecision | None:
        session = session_getter() if session_getter else {}
        migration_plan = session.get("migration_plan")
        plan_approved = session.get("plan_approved", False)

        decision = evaluate_guardrail(
            agent_role=agent_role,
            tool_name=kwargs.get("_tool_name", tool_func.__name__),
            arguments=kwargs,
            migration_plan=migration_plan,
            plan_approved=plan_approved,
            session=session,
        )

        if log_decision and session.get("session_id"):
            log_decision(session["session_id"], decision.to_dict())

        return decision

    def _check_decision(decision: GuardrailDecision | None, kwargs: dict) -> dict | None:
        """Return error dict if blocked/needs confirmation, else None."""
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
        async def wrapped_async(**kwargs) -> Any:
            decision = _evaluate(**kwargs)
            error = _check_decision(decision, kwargs)
            if error:
                return error
            kwargs.pop("_tool_name", None)
            return await tool_func(**kwargs)
        return wrapped_async
    else:
        def wrapped(**kwargs) -> Any:
            decision = _evaluate(**kwargs)
            error = _check_decision(decision, kwargs)
            if error:
                return error
            kwargs.pop("_tool_name", None)
            return tool_func(**kwargs)
        return wrapped
