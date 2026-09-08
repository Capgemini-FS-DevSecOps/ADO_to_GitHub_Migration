"""Consolidated policies for the migration agent.

Combines execution_mode, live_execution_policy, session_access, and
agent_scope into a single module for the migration_agent package.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

# ─── Agent scope guardrails ───────────────────────────────────────────

OUT_OF_SCOPE_REPLY = (
    "I'm the ADO→GitHub migration assistant. I only help with Azure DevOps to GitHub "
    "migrations — discovery, planning, execution, validation, pipelines, secrets, and "
    "related platform tasks. I can't help with that request."
)

PROHIBITED_REPLY = (
    "I can't assist with hacking, bypassing security, credential theft, malware, or other "
    "harmful or illegal activity. This assistant is limited to authorized ADO→GitHub "
    "migration work."
)

_MIGRATION_TERMS = (
    "ado", "azure devops", "azure", "devops", "github", "gh ", " gh",
    "migrate", "migration", "repo", "repository", "pipeline", "phase",
    "dry-run", "dry run", "discovery", "inventory", "secret",
    "service connection", "gei", "actions", "validate", "rollback",
    "profile", "wave", "poc", "pilot", "branch polic", "work item",
    "variable group", "pev", "remigrate", "replan",
)

_IN_SCOPE_META_PHRASES = (
    "hello", "hi ", " hi", "hey", "thanks", "thank you",
    "what can you do", "what do you do", "how can you help",
    "who are you", "capabilities", "help me use", "help with migration",
)

_OFF_TOPIC_PHRASES = (
    "recipe", "cake", "cook ", "cooking", "bake ", "ingredient",
    "restaurant", "weather", "sports", "movie", "joke", "poem",
    "song", "lyrics", "search the web", "find me a", "write me a",
    "tell me a story", "stock price", "dating", "medical advice",
    "legal advice", "homework", "essay",
)

_PROHIBITED_PHRASES = (
    "hack ", "hacking", "exploit", "malware", "ransomware", "keylogger",
    "phishing", "bypass auth", "bypass security", "crack password",
    "steal ", "ddos", "sql injection", "xss attack", "illegal",
    "launder", "counterfeit", "weapon", "bomb",
)


def is_migration_related(user_message: str) -> bool:
    msg = user_message.lower()
    return any(term in msg for term in _MIGRATION_TERMS)


def is_in_scope_meta(user_message: str) -> bool:
    msg = user_message.lower().strip()
    if not msg:
        return True
    if any(phrase in msg for phrase in _IN_SCOPE_META_PHRASES):
        return True
    if msg in ("hi", "hey", "hello", "help", "thanks"):
        return True
    return False


def is_prohibited_message(user_message: str) -> bool:
    msg = user_message.lower()
    return any(phrase in msg for phrase in _PROHIBITED_PHRASES)


def is_out_of_scope_message(user_message: str) -> bool:
    if is_prohibited_message(user_message):
        return True
    if is_migration_related(user_message):
        return False
    if is_in_scope_meta(user_message):
        return False
    if any(phrase in user_message.lower() for phrase in _OFF_TOPIC_PHRASES):
        return True
    if len(user_message.split()) >= 6 and not is_migration_related(user_message):
        return True
    return False


def scope_refusal_reply(user_message: str) -> str:
    if is_prohibited_message(user_message):
        return PROHIBITED_REPLY
    return OUT_OF_SCOPE_REPLY


# ─── Live execution policy ────────────────────────────────────────────

def _attach_actor_to_session(session: dict[str, Any], user: Any | None) -> None:
    """Persist platform user identity on an agent session."""
    if not user:
        return
    from ado2gh.auth.service import permissions_for
    session["user_id"] = getattr(user, "id", None)
    session["user_username"] = getattr(user, "username", None)
    session["user_role"] = getattr(user.role, "value", str(getattr(user, "role", "")))
    session["user_display_name"] = getattr(user, "display_name", None) or getattr(
        user, "username", None,
    )
    session["permissions"] = permissions_for(user.role)


attach_actor_to_session = _attach_actor_to_session


def _role_may_execute_live(role: str | None) -> bool:
    if not role:
        return False
    from ado2gh.auth.models import PlatformRole
    return role in (PlatformRole.ADMIN.value, PlatformRole.APPROVER.value)


def can_execute_live_without_approval(session: dict[str, Any]) -> bool:
    """Live execution is a safety gate, not an identity gate.

    Deliberately does not consult ``auth_enabled()``: it used to return ``True``
    whenever ``ADO2GH_AUTH_ENABLED`` was unset — the shipped default — which made the
    whole agent approval gate inert (GAP-002). A session with no attached actor now
    has no live authority, exactly like an OPERATOR session. Dry-run still short-
    circuits first, so auth-disabled local development is unaffected.
    """
    if session.get("dry_run", True):
        return True
    if session.get("live_approval_status") == "approved":
        return True
    perms = session.get("permissions") or {}
    if perms.get("can_approve_live_execution"):
        return True
    return _role_may_execute_live(session.get("user_role"))


def session_requires_live_approval(session: dict[str, Any]) -> bool:
    """Exact complement of :func:`can_execute_live_without_approval` for live sessions.

    Previously ended in a role-name comparison against OPERATOR, so a COORDINATOR —
    a role that can operate and cannot approve — fell through to "no approval needed"
    (the agent-side twin of GAP-005).
    """
    if session.get("dry_run", True):
        return False
    return not can_execute_live_without_approval(session)


def enforce_live_mode_request(
    request: Request | None,
    dry_run: bool,
    session: dict[str, Any] | None = None,
) -> None:
    """The single check every non-dry-run entry point routes through.

    Two tiers, both skipped entirely for dry-run:

    * **Identity** — an OPERATOR or COORDINATOR *may* switch a session to live; they
      are then routed through the approval queue by
      :func:`session_requires_live_approval`. A caller with no identity at all cannot
      be: there is no actor to attribute the run to and no requester to open an
      approval on behalf of, so the transition is refused outright (GAP-002).
    * **Authority** — pass ``session`` on an entry point that takes the session live
      *without* a downstream approval queue behind it (``confirm-live``). The caller
      must then already hold live authority: an approval on record, or a role that
      can approve. Attach the actor to ``session`` first so the check sees the
      caller's permissions.

    ``session`` is probed as if it were already live, because both predicates
    short-circuit to "no approval needed" while ``session["dry_run"]`` is still
    ``True`` — the exact window ``confirm-live`` used to escalate through (GAP-006).

    Call this *before* mutating ``dry_run`` so a refused request leaves the session
    in dry-run.
    """
    if dry_run:
        return
    if not getattr(getattr(request, "state", None), "platform_user", None):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if session is not None and session_requires_live_approval({**session, "dry_run": False}):
        raise HTTPException(status_code=403, detail="live_execution_requires_approval")


def resolve_execution_dry_run(
    session: dict[str, Any],
    migration_plan: dict[str, Any] | None,
) -> bool:
    """Reconcile the two ``dry_run`` flags at execution time; the safe one wins.

    The executor used to read ``migration_plan["dry_run"]`` on its own and write that
    value straight onto the shared session dict, so plan data alone took a dry-run
    session live and disarmed every downstream guardrail — all of which key off
    ``session["dry_run"]`` — for the rest of the session (GAP-006, Principle IV: two
    disagreeing sources of truth).

    A plan may only ever be *more* conservative than its session. It may take the
    session live only when the session already holds live authority, which is the same
    predicate every other live entry point uses.

    The caller is
    :func:`~ado2gh.agents.migration_agent.nodes.executor.node.executor_node`, which
    resolves the mode once here and then uses that single value for both the
    dry-run/live banner it streams and the migration run itself. That node still
    assigns the result back to ``session["dry_run"]``; the assignment is safe now
    precisely because the value has been through this reconciliation first.
    """
    session_dry = bool(session.get("dry_run", True))
    plan_dry = bool((migration_plan or {}).get("dry_run", session_dry))
    if plan_dry:
        return True
    if not session_dry:
        return False
    return not can_execute_live_without_approval({**session, "dry_run": False})


def live_execution_block_message(session: dict[str, Any]) -> str:
    name = session.get("user_display_name") or session.get("user_username") or "operator"
    role = session.get("user_role") or "unknown"
    profile = session.get("profile_id") or "active profile"
    return (
        "### Live execution requires approval\n\n"
        f"You are signed in as **{name}** (`{role}`) using deployment profile "
        f"**{profile}**.\n\n"
        "A **live** migration writes to GitHub and **cannot start** until a platform "
        "**admin** or **approver** reviews and approves this run.\n\n"
        "- **Dry-run** migrations are safe to execute without approval.\n"
        "- Use **Request live execution** or ask an approver to act from "
        "**Settings → Approvals**.\n\n"
        "**No migration was started.**"
    )


def execution_policy_summary(session: dict[str, Any]) -> dict[str, Any]:
    dry_run = bool(session.get("dry_run", True))
    requires = session_requires_live_approval(session)
    perms = session.get("permissions") or {}
    return {
        "dry_run": dry_run,
        "requires_live_approval": requires,
        "live_approved": session.get("live_approval_status") == "approved",
        "can_execute_live_without_approval": can_execute_live_without_approval(session),
        "can_approve_live_execution": bool(perms.get("can_approve_live_execution")),
        "user_role": session.get("user_role"),
        "user_display_name": session.get("user_display_name"),
    }


# ─── Session access control ───────────────────────────────────────────

def request_username(request: Request | None) -> str | None:
    if not request:
        return None
    user = getattr(request.state, "platform_user", None)
    return getattr(user, "username", None) if user else None


def is_admin_request(request: Request | None) -> bool:
    from ado2gh.auth.models import PlatformRole
    from ado2gh.auth.service import auth_enabled
    if not request:
        return not auth_enabled()
    user = getattr(request.state, "platform_user", None)
    if not user:
        return not auth_enabled()
    return getattr(user, "role", None) == PlatformRole.ADMIN


def session_owner_username(session: dict[str, Any]) -> str | None:
    return session.get("user_username")


def can_access_agent_session(
    request: Request | None,
    session: dict[str, Any],
    *,
    profile_id: str | None = None,
    write: bool = False,
) -> bool:
    from ado2gh.auth.service import auth_enabled
    if profile_id and session.get("profile_id") != profile_id:
        return False
    if not auth_enabled():
        return True
    if is_admin_request(request):
        return True
    owner = session_owner_username(session)
    viewer = request_username(request)
    if not owner or not viewer:
        return False
    if owner == viewer:
        return True
    if write:
        return False
    perms = getattr(request.state, "permissions", None) or {}
    return bool(perms.get("can_approve_live_execution"))


