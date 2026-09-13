"""Live-execution guard shared by the accelerator's migration routes (GAP-007).

The nine ``/v1/migrate/*`` feature routes each mutate GitHub irreversibly, so they
hang this one dependency off their router instead of repeating a check per handler
— a route added later is covered by construction.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request
from pydantic import TypeAdapter, ValidationError

from ado2gh.api.contracts import LiveApprovalCreateRequest
from ado2gh.api.platform_rbac import operator_requires_live_approval, platform_user
from ado2gh.models import ExecutionMode
from services.accelerator_api.routes._shared import _settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ado2gh.auth.models import PlatformUser

# The identity fields the nine request models use to name their target. An
# allowlist rather than a blocklist so no secret-bearing field (notably
# ``secret_value``) can reach an approval scope id or an audit payload (CA-003).
_SCOPE_FIELDS = (
    "project", "repo", "repo_name", "github_org", "github_repo",
    "connection_name", "feed_name", "wiki_name", "secret_name",
)

# Every request model on this router declares ``dry_run: bool``, so this adapter is
# the parse the handler will apply to the same value. Reading the raw body with
# ``bool()`` instead made ``"false"``, ``"0"`` and ``"off"`` dry runs to the guard
# and live runs to the handler (GAP-065).
_DRY_RUN = TypeAdapter(bool)


def active_profile_id() -> str | None:
    """Identify the migration profile a live run should be recorded against.

    Returns:
        The active profile's id, or ``None`` when no profile is active or the
        settings store cannot be read — callers fall back to the platform scope.
    """
    try:
        active = _settings.get_active_profile()
    except Exception:
        return None
    return active.id if active else None


def _dry_run_flag(body: dict[str, Any]) -> bool:
    """Read ``dry_run`` from a raw body exactly as the route's model will read it.

    Args:
        body: Parsed request body. A body that omits ``dry_run`` is a dry run —
            every request model on this router defaults it to ``True``.

    Returns:
        The parsed flag: ``True`` for a dry run, ``False`` for a live migration.

    Raises:
        HTTPException: 422 when the value is one neither this guard nor the
            request model can read. Guessing would mean the two disagree again.
    """
    try:
        return _DRY_RUN.validate_python(body.get("dry_run", True))
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail="dry_run must be a boolean",
        ) from exc


def _live_scope_id(path: str, body: dict[str, Any]) -> str:
    """Approval scope for one live run: the route plus the target it names.

    Args:
        path: Request path of the migrate route being guarded.
        body: Parsed request body; only allowlisted identity fields are read.

    Returns:
        A colon-joined scope id, stable for the same route and target, so a
        repeat of the same live request finds the approval already granted.
    """
    return ":".join([path, *(str(body[f]).strip() for f in _SCOPE_FIELDS if body.get(f))])


def audit_live_migration(path: str, user: PlatformUser | None, body: dict[str, Any]) -> None:
    """Record a live run before any irreversible work starts (CA-004).

    Only allowlisted identity fields go in: an audit row is permanent and the
    request body can carry a secret value (``/v1/migrate/secret-provision``).

    Args:
        path: Request path of the migrate route about to run live.
        user: Signed-in platform user, or ``None`` for an unauthenticated
            deployment; recorded as the actor and the role.
        body: Parsed request body, filtered to the allowlisted identity fields.
    """
    from ado2gh.api.profile_governance import write_profile_audit

    write_profile_audit(
        "accelerator.migrate.live_execution",
        profile_id=active_profile_id() or "_platform",
        actor=getattr(user, "username", "") or "",
        payload={
            "route": path,
            "role": user.role.value if user else None,
            "target": {f: body[f] for f in _SCOPE_FIELDS if body.get(f)},
        },
    )


async def guard_live_migration(request: Request) -> None:
    """Single live-execution choke point for every route on the migrate router.

    Dry runs pass straight through, so local development stays permissive for
    everything reversible. A live run needs an identity and the
    ``can_approve_live_execution`` capability; a caller who can only operate is
    parked in the shared approval queue exactly as the sibling ``POST /v1/migrate``
    parks it — which records the request (CA-004) and gives the operator the
    approve/deny preview path (CA-001).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    dry_run = _dry_run_flag(body)
    user = platform_user(request)
    path = request.url.path

    # Raises 401 for a live request that carries no identity at all.
    if not operator_requires_live_approval(
        user, ExecutionMode.from_dry_run(dry_run=dry_run),
    ):
        if not dry_run:
            audit_live_migration(path, user, body)
        return

    from ado2gh.api.live_approval_store import LiveApprovalStore

    scope_id = _live_scope_id(path, body)
    store = LiveApprovalStore()
    if store.has_approved("migrate_job", scope_id):
        audit_live_migration(path, user, body)
        return
    approval = store.create_or_get_pending(
        user,
        LiveApprovalCreateRequest(
            scope_type="migrate_job",
            scope_id=scope_id,
            profile_id=active_profile_id(),
            reason_request=f"Live {path}",
            context={"route": path, "scope_id": scope_id},
        ),
    )
    raise HTTPException(
        status_code=403,
        detail={"code": "awaiting_approval", "approval_id": approval["id"]},
    )
