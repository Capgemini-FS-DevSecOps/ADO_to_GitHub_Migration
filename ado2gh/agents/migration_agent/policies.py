"""Consolidated policies for the migration agent.

Combines execution_mode, live_execution_policy, session_access, and
agent_scope into a single module for the migration_agent package.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

from ado2gh.models import ExecutionMode

if TYPE_CHECKING:  # imported lazily at runtime to keep `agents -> auth` off the import path
    from ado2gh.auth.models import PlatformUser

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
    """Check whether a message mentions any Azure DevOps or GitHub migration term.

    Args:
        user_message: Raw text the operator typed into the agent chat.

    Returns:
        ``True`` when at least one term from the migration vocabulary appears in the
        message, case-insensitively; ``False`` otherwise.
    """
    msg = user_message.lower()
    return any(term in msg for term in _MIGRATION_TERMS)


def is_in_scope_meta(user_message: str) -> bool:
    """Check whether a message is small talk the assistant should still answer.

    Greetings, thanks and "what can you do" carry no migration vocabulary but are part
    of using the assistant, so they must not be refused as off-topic.

    Args:
        user_message: Raw text the operator typed into the agent chat.

    Returns:
        ``True`` for an empty message, a recognised greeting or a capability question;
        ``False`` otherwise.
    """
    msg = user_message.lower().strip()
    if not msg:
        return True
    if any(phrase in msg for phrase in _IN_SCOPE_META_PHRASES):
        return True
    if msg in ("hi", "hey", "hello", "help", "thanks"):
        return True
    return False


def is_prohibited_message(user_message: str) -> bool:
    """Check whether a message asks for harmful or illegal help.

    Args:
        user_message: Raw text the operator typed into the agent chat.

    Returns:
        ``True`` when the message contains a phrase from the prohibited list (hacking,
        credential theft, malware and similar); ``False`` otherwise.
    """
    msg = user_message.lower()
    return any(phrase in msg for phrase in _PROHIBITED_PHRASES)


def is_out_of_scope_message(user_message: str) -> bool:
    """Decide whether the assistant should refuse a message instead of answering it.

    Prohibited requests are refused outright. Anything with migration vocabulary, and
    any greeting or capability question, is in scope. What is left is refused when it
    matches a known off-topic phrase or is long enough to be a real request about
    something else.

    Args:
        user_message: Raw text the operator typed into the agent chat.

    Returns:
        ``True`` when the assistant should reply with a refusal from
        :func:`scope_refusal_reply` instead of handling the message; ``False`` otherwise.
    """
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
    """Pick the refusal text that matches why a message is out of scope.

    Args:
        user_message: The message :func:`is_out_of_scope_message` rejected.

    Returns:
        :data:`PROHIBITED_REPLY` for a harmful or illegal request, otherwise
        :data:`OUT_OF_SCOPE_REPLY`.
    """
    if is_prohibited_message(user_message):
        return PROHIBITED_REPLY
    return OUT_OF_SCOPE_REPLY


# ─── Live execution policy ────────────────────────────────────────────

def _attach_actor_to_session(session: dict[str, Any], user: PlatformUser | None) -> None:
    """Persist platform user identity on an agent session.

    Writes ``user_id``, ``user_username``, ``user_role``, ``user_display_name`` and the
    role's ``permissions`` onto the session dict, which is what every live-execution
    predicate below reads. A ``None`` user leaves the session untouched, so a session
    with no attached actor keeps no authority.

    Args:
        session: The live agent session dict, mutated in place.
        user: The authenticated platform user, or ``None`` when the request carried none.
    """
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
    """Check whether a platform role carries live-execution authority by itself.

    Args:
        role: The ``PlatformRole`` value stored on the session, or ``None``.

    Returns:
        ``True`` only for ADMIN and APPROVER; ``False`` for every other role and for
        a session with no role at all.
    """
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

    Args:
        session: The live agent session dict.

    Returns:
        ``True`` when the session may run live immediately — it is still in dry-run, it
        already carries an approval, or its actor holds approve-live permission or an
        ADMIN/APPROVER role. ``False`` when the run must go through the approval queue.
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

    Args:
        session: The live agent session dict.

    Returns:
        ``False`` for a dry-run session, otherwise the negation of
        :func:`can_execute_live_without_approval` — ``True`` means the run must wait
        for a platform admin or approver.
    """
    if session.get("dry_run", True):
        return False
    return not can_execute_live_without_approval(session)


def enforce_live_mode_request(
    request: Request | None,
    mode: ExecutionMode,
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

    Call this *before* mutating the session's mode so a refused request leaves the
    session in dry-run.

    Args:
        request: The inbound HTTP request, read for its authenticated platform user.
        mode: The mode the caller wants the session to run in. Convert the wire-level
            boolean at the route with ``ExecutionMode.from_dry_run(dry_run=req.dry_run)``
            — the converter is keyword-only.
        session: The session being taken live, for entry points with no approval queue
            behind them. Omit it on entry points that enqueue an approval.

    Raises:
        HTTPException: 401 when a live request carries no authenticated user, 403 when
            the caller lacks the authority the entry point requires.
    """
    if mode is ExecutionMode.DRY_RUN:
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

    Args:
        session: The live agent session dict.
        migration_plan: The plan about to be executed, or ``None`` when there is none.

    Returns:
        ``True`` when the run must stay a dry run — either source says dry-run, or the
        plan asks for live and the session holds no live authority. ``False`` only when
        both agree the run may write.
    """
    from ado2gh.agents.migration_agent.utils import coerce_dry_run

    session_dry = coerce_dry_run(session.get("dry_run"), default=True)
    plan_dry = coerce_dry_run((migration_plan or {}).get("dry_run"), default=session_dry)
    if plan_dry:
        return True
    if not session_dry:
        return False
    return not can_execute_live_without_approval({**session, "dry_run": False})


def live_execution_block_message(session: dict[str, Any]) -> str:
    """Compose the chat reply shown when a live run is refused pending approval.

    Args:
        session: The live agent session dict, read for the signed-in identity and the
            active deployment profile.

    Returns:
        Markdown naming the operator, their role and the profile, explaining that an
        admin or approver must review the run, and stating that nothing was started.
    """
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
    """Flatten the live-execution policy for a session into one payload for the console.

    Args:
        session: The live agent session dict.

    Returns:
        A dict with ``dry_run``, ``requires_live_approval``, ``live_approved``,
        ``can_execute_live_without_approval``, ``can_approve_live_execution``,
        ``user_role`` and ``user_display_name`` — the values the UI needs to decide
        which execution controls to show.
    """
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
    """Read the username of the platform user attached to a request.

    Args:
        request: The inbound HTTP request, or ``None`` outside a request context.

    Returns:
        The username, or ``None`` when there is no request or no authenticated user.
    """
    if not request:
        return None
    user = getattr(request.state, "platform_user", None)
    return getattr(user, "username", None) if user else None


def is_admin_request(request: Request | None) -> bool:
    """Check whether a request may act with platform administrator authority.

    Args:
        request: The inbound HTTP request, or ``None`` outside a request context.

    Returns:
        ``True`` when the attached user is an ADMIN. With authentication disabled there
        is no identity to check, so a request without a user is treated as admin —
        that is the local single-operator mode, not a bypass of an enabled gate.
    """
    from ado2gh.auth.models import PlatformRole
    from ado2gh.auth.service import auth_enabled
    if not request:
        return not auth_enabled()
    user = getattr(request.state, "platform_user", None)
    if not user:
        return not auth_enabled()
    return getattr(user, "role", None) == PlatformRole.ADMIN




