"""Shared plumbing behind the ``/v1/migrate/*`` resource endpoints.

Holds the pieces every migrate route needs before and after it hands work to a
scope handler: building the ADO and GitHub clients for the active profile,
assembling the :class:`~ado2gh.core.scopes.base.ScopeContext`, running the
handler off the event loop, shaping the JSON body the console reads, and
turning ADO REST failures into client-visible HTTP errors.

Split out of ``migrate_routes.py`` so that module holds only the nine route
handlers; every name here is re-exported there, so existing import paths and
test patch targets keep working.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import requests
from fastapi import HTTPException

from ado2gh.api.accelerator import _build_ado_client, _build_gh_client
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.core.scopes.base import ScopeContext, ScopeHandler, ScopeResult
from ado2gh.models import DEFAULT_MIGRATION_STRATEGY, ExecutionMode
from services.accelerator_api.routes._shared import _settings

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a runtime import cycle
    from ado2gh.api.settings_models import MigrationProfile
    from ado2gh.clients import ADOClient, GHClient
    from ado2gh.models import RepoConfig
    from ado2gh.state.base import StateDBBase


def _get_clients() -> tuple[ADOClient, GHClient, str, MigrationProfile]:
    """Build ADO and GitHub clients from the active profile plus `migration.yaml`.

    Returns:
        A four-tuple of the ADO client, the GitHub client, the target GitHub
        organisation (profile value, falling back to `global.gh_org`, else an
        empty string) and the active migration profile itself.

    Raises:
        HTTPException: 400 when no migration profile is active.
    """
    active = _settings.get_active_profile()
    if not active:
        raise HTTPException(status_code=400, detail="No active migration profile. Create and activate a profile first.")
    config_path = _settings.load().advanced.config_path or "migration.yaml"
    try:
        global_cfg, _ = ConfigLoader.load(config_path)
    except Exception:
        global_cfg = {}
    ado = _build_ado_client(
        global_cfg,
        ado_url=active.ado_org_url,
        ado_pat=active.ado_pat,
    )
    gh = _build_gh_client(
        global_cfg,
        gh_token=active.github_tokens[0].token if active.github_tokens else None,
    )
    gh_org = active.gh_org or global_cfg.get("gh_org", "")
    return ado, gh, gh_org, active


def _load_global_cfg() -> dict[str, Any]:
    """Read the `global` block of the configured `migration.yaml`.

    Returns:
        The parsed global configuration, or an empty mapping when the file is
        missing or unreadable — callers treat both the same way.
    """
    config_path = _settings.load().advanced.config_path or "migration.yaml"
    try:
        global_cfg, _ = ConfigLoader.load(config_path)
        return global_cfg
    except Exception:
        return {}


def _parse_repo_key(repo: str) -> tuple[str, str]:
    """Split a discovery repository key into its ADO project and repository.

    Args:
        repo: A `project/repo_name` key as produced by discovery. Leading
            slashes and surrounding whitespace are tolerated.

    Returns:
        The ADO project name and the repository name, both stripped.

    Raises:
        HTTPException: 400 when the key has no separator or either half is blank.
    """
    normalized = (repo or "").strip().lstrip("/")
    if "/" not in normalized:
        raise HTTPException(
            status_code=400,
            detail="repo must be formatted as 'project/repo_name'",
        )
    project, repo_name = normalized.split("/", 1)
    if not project.strip() or not repo_name.strip():
        raise HTTPException(
            status_code=400,
            detail="repo must include both ADO project and repository name",
        )
    return project.strip(), repo_name.strip()


def _build_scope_context(
    ado: ADOClient,
    gh: GHClient,
    *,
    mode: ExecutionMode,
    db: StateDBBase,
    global_cfg: dict[str, Any] | None = None,
) -> ScopeContext:
    """Assemble the context a scope handler runs against.

    The active profile fills in `ado_org_url` and `gh_org` when the supplied
    configuration leaves them blank, so a request never has to repeat what the
    profile already knows.

    Args:
        ado: ADO client the handler reads the source system through.
        gh: GitHub client the handler writes the target system through.
        mode: Whether the handler previews the work or performs it.
        db: State store the handler records its rows against. Passed in rather
            than opened here so the route module stays the single place that
            resolves the configured database.
        global_cfg: The `global` configuration block, if already loaded.

    Returns:
        A scope context carrying the merged configuration, both clients, the
        state store, the execution mode and the configured migration strategy.
    """
    cfg = dict(global_cfg or {})
    active = _settings.get_active_profile()
    if active:
        if not cfg.get("ado_org_url") and active.ado_org_url:
            cfg["ado_org_url"] = active.ado_org_url
        if not cfg.get("gh_org") and active.gh_org:
            cfg["gh_org"] = active.gh_org
    return ScopeContext(
        global_cfg=cfg,
        ado=ado,
        gh=gh,
        db=db,
        mode=mode,
        strategy=cfg.get("migration_strategy", DEFAULT_MIGRATION_STRATEGY),
    )


def _scope_handler_response(
    result: ScopeResult,
    *,
    mode: ExecutionMode,
    extra: dict[str, Any],
) -> dict[str, Any]:
    """Shape a scope handler's outcome into the JSON body the console reads.

    Args:
        result: What the scope handler reported.
        mode: The mode the handler ran in; a preview always reports `dry_run`.
        extra: Endpoint-specific identifiers merged into the body, such as the
            project, repository and target organisation.

    Returns:
        A mapping with `status` (`dry_run`, `success`, `partial` when some items
        completed before the failure, or `failed`), the `failed` list from the
        handler, the `extra` identifiers, and the handler's own statistics.
    """
    stats = dict(result.stats or {})
    if mode is ExecutionMode.DRY_RUN:
        status = "dry_run"
    elif result.failed:
        status = "partial" if int(stats.get("completed", 0)) > 0 else "failed"
    else:
        status = "success"
    return {"status": status, "failed": result.failed, **extra, **stats}


async def _run_scope_migrate(handler: ScopeHandler, repo: RepoConfig, ctx: ScopeContext) -> ScopeResult:
    """Run a scope handler off the event loop (a git mirror or a GitHub Enterprise Importer (GEI) run can take many minutes).

    Args:
        handler: The scope handler to invoke.
        repo: The source and target repository pair.
        ctx: The context built by :func:`_build_scope_context`.

    Returns:
        The handler's result, once the worker thread finishes.
    """
    return await asyncio.to_thread(handler.migrate, repo, ctx)


def _raise_ado_http_error(
    exc: requests.exceptions.HTTPError,
    *,
    project: str,
    repo_name: str,
) -> None:
    """Map ADO REST failures to a client-visible HTTP error instead of an ASGI traceback.

    Args:
        exc: The failure raised while calling Azure DevOps.
        project: ADO project named in the request, used in the message.
        repo_name: ADO repository named in the request, used in the message.

    Raises:
        HTTPException: Always — 404 when ADO reported the repository as missing,
            otherwise 400, with the ADO `message` or `typeKey` appended when the
            response carried one.
    """
    response = exc.response
    status = response.status_code if response is not None else 400
    detail = f"ADO repository lookup failed for {project}/{repo_name}"
    if response is not None:
        try:
            body = response.json()
            if isinstance(body, dict):
                message = str(body.get("message") or body.get("typeKey") or "").strip()
                if message:
                    detail = f"{detail}: {message}"
        except Exception:
            pass
    if status == 404:
        raise HTTPException(
            status_code=404,
            detail=f"{detail}. Verify project and repository names in discovery.",
        ) from exc
    raise HTTPException(
        status_code=400,
        detail=(
            f"{detail}. "
            "Use the full ADO key project/repo_name from discovery (bare repo names need a matching discovery snapshot)."
        ),
    ) from exc


def _encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """Encrypt a secret value using GitHub's public key (libsodium sealed box).

    Args:
        public_key_b64: The repository's base64 public key from the GitHub API.
        secret_value: The plaintext to seal. It is never logged and never
            appears in an error message.

    Returns:
        The sealed box, base64-encoded, ready to send as `encrypted_value`.

    Raises:
        HTTPException: 500 when PyNaCl is not installed in the runtime image.
    """
    from base64 import b64decode, b64encode
    try:
        from nacl import encoding, public
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="PyNaCl not installed. Install with: pip install pynacl",
        )
    pk = public.PublicKey(b64decode(public_key_b64), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return b64encode(encrypted).decode("utf-8")
