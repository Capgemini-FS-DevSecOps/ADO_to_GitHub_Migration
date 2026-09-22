"""Session activity state for the migration agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SessionState(str, Enum):
    """Operator-visible agent activity — only these five values are persisted."""

    IDLE = "idle"
    THINKING = "thinking"
    PLANNING = "planning"
    EXECUTING = "executing"
    VALIDATING = "validating"


BUSY_SESSION_STATUSES = frozenset({
    SessionState.THINKING.value,
    SessionState.PLANNING.value,
    SessionState.EXECUTING.value,
    SessionState.VALIDATING.value,
})

_LEGACY_STATUS_ALIASES = {
    "awaiting_input": SessionState.IDLE.value,
    "awaiting_approval": SessionState.IDLE.value,
    "completed": SessionState.IDLE.value,
    "failed": SessionState.IDLE.value,
    "cancelled": SessionState.IDLE.value,
    "running": SessionState.IDLE.value,
    "remediating": SessionState.IDLE.value,
    "escalated": SessionState.IDLE.value,
}


class InvalidTransitionError(Exception):
    """Kept for backward compatibility — transitions are no longer enforced."""


def normalize_session_status(status: str | None) -> str:
    """Map legacy session statuses to the five-state model."""
    if not status:
        return SessionState.IDLE.value
    if status in BUSY_SESSION_STATUSES or status == SessionState.IDLE.value:
        return status
    return _LEGACY_STATUS_ALIASES.get(status, SessionState.IDLE.value)


def is_session_busy(status: str | None) -> bool:
    """True while an agent node is actively running."""
    return normalize_session_status(status) in BUSY_SESSION_STATUSES


def set_session_phase(session: dict[str, Any], phase: SessionState) -> None:
    """Mark which agent is currently active."""
    session["status"] = phase.value


def set_session_idle(session: dict[str, Any]) -> None:
    """Return session to chat-ready idle."""
    session["status"] = SessionState.IDLE.value


class SessionStateMachine:
    """Tracks the current agent phase; transitions are direct assignments."""

    def __init__(self, initial: SessionState | str = SessionState.IDLE) -> None:
        """Start in ``initial``, accepting a SessionState or a legacy status string."""
        if isinstance(initial, str):
            initial = SessionState(normalize_session_status(initial))
        self._state = initial

    @property
    def state(self) -> SessionState:
        """Phase the agent is currently in.

        Returns:
            The current session state.
        """
        return self._state

    def transition(self, target: SessionState) -> SessionState:
        """Move the machine into ``target``.

        Returns:
            The new current state, which is always ``target``.
        """
        self._state = target
        return self._state


def release_session_for_chat(
    session: dict[str, Any],
    *,
    outcome: str = "idle",
) -> None:
    """Return session to idle after the plan-execute-validate loop (PEV) completes so the operator can chat normally."""
    set_session_idle(session)
    if outcome == "failed":
        session["last_pev_outcome"] = "failed"
    else:
        session["pev_execution_completed"] = True
        session.pop("last_pev_outcome", None)
    session.pop("start_execution", None)
    session.pop("start_pev", None)
    session.pop("pev_max_retries_exhausted", None)


def reset_session_for_new_migration(
    session: dict[str, Any],
    *,
    repository_id: str | None = None,
) -> None:
    """Clear prior plan/PEV state when the operator starts a new migration."""
    from ado2gh.agents.migration_agent.utils import normalize_repo_key

    for key in (
        "migration_plan",
        "plan_approved",
        "plan_review_presented",
        "migration_queue",
        "executor_result",
        "validation_feedback",
        "pev_retry_count",
        "start_pev",
        "start_execution",
        "run_id",
        "pev_execution_completed",
        "pev_execution_started",
        "pev_max_retries_exhausted",
        "last_pev_outcome",
        "pending_form",
        "operator_secret_mappings",
        "pending_clarification",
    ):
        session.pop(key, None)
    session["plan_approved"] = False
    session.pop("plan_repository_ids", None)
    session.pop("plan_repository_id", None)
    session.pop("plan_phase", None)
    session.pop("execution_mode_confirmed", None)
    session.pop("last_completed_repository_id", None)
    session.pop("run_id", None)
    session.pop("pipeline_run_id", None)
    session.pop("agent_run_seq", None)
    if repository_id:
        session["plan_repository_id"] = normalize_repo_key(repository_id)
    release_session_for_chat(session)
    try:
        from ado2gh.agents.migration_agent.graph import clear_langgraph_thread_sync

        clear_langgraph_thread_sync(str(session.get("session_id") or ""))
    except Exception:
        pass


def reset_for_migration_request(session: dict[str, Any], repository_id: str) -> bool:
    """Clear plan/PEV state and point the session at ``repository_id``.

    Returns:
        True once the session has been reset, or False when ``repository_id``
        is blank — in which case the session is left untouched.
    """
    from ado2gh.agents.migration_agent.utils import normalize_repo_key

    new_repo = normalize_repo_key(repository_id)
    if not new_repo:
        return False
    reset_session_for_new_migration(session, repository_id=new_repo)
    return True


def maybe_reset_for_migration_request(session: dict[str, Any], repository_id: str) -> bool:
    """Reset stale plan/PEV state only when this request supersedes the current one.

    Returns:
        True when the session was reset — the repository changed, a plan was
        already built, or the last PEV run failed or exhausted its retries.
        False when the request matches the session's existing state, or when
        ``repository_id`` is blank.
    """
    from ado2gh.agents.migration_agent.utils import normalize_repo_key

    new_repo = normalize_repo_key(repository_id)
    if not new_repo:
        return False
    old_repo = normalize_repo_key(str(session.get("plan_repository_id") or ""))
    supersedes_current = (
        new_repo != old_repo
        or bool(session.get("migration_plan"))
        or session.get("last_pev_outcome") == "failed"
        or bool(session.get("pev_max_retries_exhausted"))
    )
    if not supersedes_current:
        return False
    return reset_for_migration_request(session, new_repo)


@dataclass
class OrchestratorResult:
    """What one orchestrator turn produced for the caller."""

    reply: str = ""
    tasks: list[dict[str, Any]] = field(default_factory=list)
    pending_form: dict[str, Any] | None = None
    start_pev: bool = False
