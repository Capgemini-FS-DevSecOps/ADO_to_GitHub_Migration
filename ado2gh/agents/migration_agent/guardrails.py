"""Guardrail integration for LangChain tool execution.

Wraps tools with validation checks: plan authorization, resource validation,
deletion confirmation, parameter validation, and GuardrailDecision logging.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, cast
from urllib.parse import unquote

from ado2gh.agents.migration_agent.constants import (
    DEFAULT_HTTP_METHOD,
    DETERMINISTIC_SCOPE_WRITE_TOOL,
)
from ado2gh.agents.migration_agent.utils import coerce_dry_run


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
    "call_accelerator",
    "workflow_create",
    "github_api",
    DETERMINISTIC_SCOPE_WRITE_TOOL,
})

# Operations that require deletion confirmation
_DELETION_OPERATIONS = frozenset({
    "repo_delete", "pipeline_disable", "workflow_delete",
    "secret_delete", "environment_delete",
})

# Read-only operations that always pass guardrails.
# Every tool name any agent binds must appear in one of the three sets above or here:
# `evaluate_guardrail` ends in BLOCK, and
# tests/unit/test_guardrails.py::test_every_bound_tool_has_a_guardrail_classification
# ties these sets to the tool builders so a new tool fails the test, not production.
_READ_OPERATIONS = frozenset({
    "fetch_migration_status",
    "get_current_profile",
    "ado_api_query", "github_api_query",
    "generate_plan", "validate_workflow_conversion", "validate_workflow_syntax",
    "fetch_github_workflow", "list_ado_pipelines", "list_github_workflows",
    # Session-local control tools: they hand the turn to another agent or to the
    # operator and reach no external system themselves. They do start work whose
    # later steps write, but those steps are the executor's tools, each evaluated
    # here in their own right against the rule that a real run needs the operator's
    # explicit opt-in, and against the plan-approval check (CA-001).
    "invoke_planner", "invoke_bulk_planner", "run_migration_pev", "request_user_input",
})


_GITHUB_REPO_ENDPOINT_SEGMENT = "repos"
"""The path segment that introduces a repository-scoped GitHub endpoint.

A GitHub write is repository-scoped when its endpoint contains this segment
followed by an owner and a repository name, for example ``repos/{owner}/{repo}``
or ``repos/{owner}/{repo}/issues``. A leading slash and any prefix ahead of the
segment (such as a proxy path like ``/v1/proxy/github/``) are both tolerated —
the extractor reads the first ``repos/{owner}/{repo}`` triple it finds and
ignores everything else in the path.
"""

_GITHUB_REPO_ENDPOINT_SHAPE = f"{_GITHUB_REPO_ENDPOINT_SEGMENT}/{{owner}}/{{repo}}"
"""Human-readable form of the accepted endpoint shape, used in refusal reasons."""


def _extract_github_repo_endpoint_target(endpoint: str) -> tuple[str, str] | None:
    """Read the repository owner and name a GitHub API endpoint path writes to.

    The raw endpoint is normalised the same way the transport that finally
    sends it behaves (see ``shared_tools.join_api_path``): percent-decoded,
    backslashes folded to forward slashes, any query string dropped, and
    ``..`` segments collapsed. Without that, a string that names the approved
    owner and repository here could still resolve to a different one on the
    wire — for example ``repos/{approved}/{approved}/../../other/other``
    reads as the approved repository to a plain split but reaches ``other``
    once the same dot-segments are collapsed. A ``#`` is refused outright
    rather than modelled, because httpx drops everything from the first
    ``#`` before it builds the request, so a fragment can hide a real
    segment from a scan that keeps it.

    Args:
        endpoint: The raw ``endpoint`` argument passed to the ``github_api`` tool.

    Returns:
        The ``(owner, repo)`` pair, or ``None`` when the normalised path
        carries no ``repos/{owner}/{repo}`` triple — an org-level, malformed,
        or other non-repository endpoint is not repository-scoped and has
        nothing to compare to the plan.
    """
    text = str(endpoint or "")
    if "#" in text:
        return None
    without_query = text.split("?", 1)[0]
    rooted = "/" + unquote(without_query).replace("\\", "/").lstrip("/")
    segments = [segment for segment in posixpath.normpath(rooted).split("/") if segment]
    for index, segment in enumerate(segments):
        if segment == _GITHUB_REPO_ENDPOINT_SEGMENT and index + 2 < len(segments):
            owner, repo = segments[index + 1], segments[index + 2]
            if owner and repo:
                return owner, repo
    return None


def _approved_plan_repo_entry(
    migration_plan: dict[str, Any] | None,
    target_resource: str,
) -> dict[str, Any] | None:
    """Find the plan repo entry that a write's approved ``target_resource`` names.

    Matches the same way :func:`plan_scope` does — by ``id`` first, then
    ``name`` — so this reads the identical entry the plan-authorization check
    already treats as the one the operator approved for this write.

    Args:
        migration_plan: The plan to search, or ``None`` when there is none yet.
        target_resource: The ``target_resource``/``repository_id`` the call named.

    Returns:
        The matching repo entry, or ``None`` when there is no plan, no match, or
        the target is blank.
    """
    if not isinstance(migration_plan, dict) or not target_resource:
        return None
    repos = migration_plan.get("repos")
    if not isinstance(repos, list):
        return None
    for entry in repos:
        if not isinstance(entry, dict):
            continue
        ident = entry.get("id") or entry.get("name") or ""
        if isinstance(ident, str) and ident.strip() == target_resource:
            return entry
    return None


def plan_scope(migration_plan: dict[str, Any] | None) -> set[str] | None:
    """Read the set of repository ids an approved plan authorises writes for.

    The check this feeds is an authorization boundary, so the shape is validated
    rather than best-effort parsed: only a non-empty ``repos`` list of entries that
    each yield a non-empty id counts. A plan that omits the key, spells it
    differently or carries an unusable entry returns ``None``, and the caller must
    refuse the write — an unreadable scope is not an empty scope (THR-06-001).

    Args:
        migration_plan: The plan to read, as the planner produced it.

    Returns:
        The authorised repository ids, or ``None`` when the plan declares no scope
        that can be validated against.
    """
    if not isinstance(migration_plan, dict):
        return None
    repos = migration_plan.get("repos")
    if not isinstance(repos, list) or not repos:
        return None
    scope: set[str] = set()
    for entry in repos:
        if isinstance(entry, dict):
            ident = entry.get("id") or entry.get("name") or ""
        elif isinstance(entry, str):
            ident = entry
        else:
            # Not stringified: `repos: [None]` would otherwise authorise the literal
            # target "None" instead of refusing an entry nothing can be matched to.
            return None
        if not isinstance(ident, str) or not ident.strip():
            return None
        scope.add(ident.strip())
    return scope or None


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
) -> GuardrailDecision:
    """Decide whether one tool call may run, in order of severity.

    The checks are, in order: read-only operations always pass; accelerator and GitHub
    ``GET`` calls pass as reads; accelerator and GitHub *writes* are blocked while the
    session is in dry-run (CA-001); a GitHub ``DELETE`` needs operator confirmation; a
    remaining GitHub write must target a repository-shaped endpoint whose owner and
    repository match the GitHub coordinates recorded on the approved plan entry for
    the call's repository, when the plan records any (GAP-128); remaining write
    operations need an approved plan whose repo list contains the target resource; and
    the deletion operations need confirmation (CA-002). A tool in none of the three
    classification sets is blocked, not allowed.

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

    Returns:
        A :class:`GuardrailDecision` carrying the action, the operation identity, the
        target resource, a human-readable reason and, for plan-scoped decisions, the
        plan id. Unclassified tools are blocked and say so in the reason.
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
    # Only a repository-scoped GitHub write ever overrides this; every other
    # path records target_resource unchanged (GAP-128).
    audit_target_resource: str | None = None

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

    # call_accelerator: allow GET requests without approved plan (read-only).
    # The default matches the tool the model actually calls — `CallAcceleratorArgs`
    # and `executor_tools.call_accelerator` both now default to GET, matching what
    # they actually perform — so an omitted method is evaluated as the read it will
    # perform, not as a write the guardrail would then refuse for want of live
    # authority (THR-06-007).
    if tool_name == "call_accelerator":
        method = str(arguments.get("method", DEFAULT_HTTP_METHOD)).upper()
        if method == "GET":
            return GuardrailDecision(
                action=GuardrailAction.ALLOW,
                agent_role=agent_role,
                tool_name=tool_name,
                operation_type=operation_type,
                target_resource=str(target_resource),
                reason="Read-only accelerator GET",
            )
        # Only an explicit `False` is live authority: a missing session, a missing flag
        # or a malformed one must keep blocking writes rather than be coerced into
        # "live" (GAP-076, CA-001, THR-06-005).
        if coerce_dry_run((session or {}).get("dry_run"), default=True):
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
        method = str(arguments.get("method", DEFAULT_HTTP_METHOD)).upper()
        if method == "GET":
            return GuardrailDecision(
                action=GuardrailAction.ALLOW,
                agent_role=agent_role,
                tool_name=tool_name,
                operation_type=operation_type,
                target_resource=str(target_resource),
                reason="Read-only GitHub GET",
            )
        if coerce_dry_run((session or {}).get("dry_run"), default=True):  # see the note above
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

        # The plan-authorization check below only confirms that the approved
        # repository_id/target_resource argument is in scope. It never looks at
        # the endpoint the request actually reaches, so an approved repository
        # could be paired with an endpoint naming a different one (GAP-128).
        # Pin the write to a repository path first, then to the GitHub
        # coordinates the plan recorded for the approved entry.
        endpoint = str(arguments.get("endpoint") or "")
        endpoint_target = _extract_github_repo_endpoint_target(endpoint)
        if endpoint_target is None:
            return GuardrailDecision(
                action=GuardrailAction.BLOCK,
                agent_role=agent_role,
                tool_name=tool_name,
                operation_type=operation_type,
                target_resource=str(target_resource),
                reason=(
                    "GitHub writes must target a repository path of the form "
                    f"{_GITHUB_REPO_ENDPOINT_SHAPE}"
                ),
            )
        endpoint_owner, endpoint_repo = endpoint_target
        audit_target_resource = f"{endpoint_owner}/{endpoint_repo}"

        plan_entry = _approved_plan_repo_entry(migration_plan, str(target_resource))
        if plan_entry is not None:
            approved_owner = str(
                plan_entry.get("gh_org") or plan_entry.get("github_org") or "",
            ).strip()
            approved_repo = str(
                plan_entry.get("gh_repo")
                or plan_entry.get("github_repo")
                or plan_entry.get("name")
                or "",
            ).strip()
            if (
                approved_owner
                and approved_repo
                and (
                    approved_owner.lower() != endpoint_owner.lower()
                    or approved_repo.lower() != endpoint_repo.lower()
                )
            ):
                return GuardrailDecision(
                    action=GuardrailAction.BLOCK,
                    agent_role=agent_role,
                    tool_name=tool_name,
                    operation_type=operation_type,
                    target_resource=audit_target_resource,
                    reason=(
                        f"Endpoint targets '{endpoint_owner}/{endpoint_repo}', but "
                        f"the approved plan authorizes writes to "
                        f"'{approved_owner}/{approved_repo}'"
                    ),
                    plan_reference=str(migration_plan.get("plan_id", "")) if migration_plan else None,
                )

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

        # Validate target resource exists in plan. An unreadable scope blocks: the
        # plan is model output, so a drifted key name must not disarm the check.
        plan_repos = plan_scope(migration_plan)
        if plan_repos is None:
            return GuardrailDecision(
                action=GuardrailAction.BLOCK,
                agent_role=agent_role,
                tool_name=tool_name,
                operation_type=operation_type,
                target_resource=str(target_resource),
                reason="Approved plan declares no usable repo scope — writes need a 'repos' list of identified repositories",
                plan_reference=str(migration_plan.get("plan_id", "")),
            )
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
            target_resource=audit_target_resource or str(target_resource),
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

    # Default: block. An allowlist whose fall-through is "permit" leaves every tool
    # registered later unguarded until someone remembers to classify it (THR-06-002).
    return GuardrailDecision(
        action=GuardrailAction.BLOCK,
        agent_role=agent_role,
        tool_name=tool_name,
        operation_type=operation_type,
        target_resource=str(target_resource),
        reason=f"Unknown tool '{tool_name}' — no guardrail classification, blocked by default",
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
        from ado2gh.agents.migration_agent.hitl.intake import clear_stale_plan_approval

        session = (session_getter() if session_getter else {}) or {}
        migration_plan = session.get("migration_plan")
        # Approval is revocable and is bound to the plan revision it was given for
        # (THR-09-002), so the flag is re-checked here rather than trusted from
        # whichever node last passed a revocation point. A plan revised after the
        # operator approved it is unapproved again by the time the tool runs.
        clear_stale_plan_approval(session, plan=migration_plan)
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

        # Record tool call
        metrics.record_tool_call()

        if decision is None:
            return None
        if decision.blocked:
            # Record guardrail block
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
