"""Public Accelerator SDK facade — no Click dependency."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, cast

from ado2gh.api.contracts import (
    DiscoverRequest,
    DiscoverResult,
    PhaseRunRequest,
    PhaseRunResult,
    RunWaveRequest,
    RunWaveResult,
    StatusSnapshot,
    ValidateRequest,
    ValidateResult,
)
from ado2gh.api.errors import ConfigurationError
from ado2gh.api.live_approval_scopes import MIGRATE_JOB_SCOPE_TYPE
from ado2gh.api.live_approval_store import LiveApprovalStore, migrate_scope_id
from ado2gh.clients.ado_client import ADOClient
from ado2gh.clients.ado_token_manager import ADOTokenManager
from ado2gh.clients.gh_client import GHClient
from ado2gh.clients.gh_token_manager import TokenManager
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.core.discovery import DiscoveryScanner
from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.models import ExecutionMode, PhaseType
from ado2gh.phase.batch_executor import BatchExecutor
from ado2gh.phase.gate_checker import PhaseGateChecker
from ado2gh.phase.progress_tracker import ProgressTracker
from ado2gh.state.factory import create_state_db

# How many numbered environment-variable slots are probed for a rotating,
# multi-token credential (ADO_PAT_1.._N, GH_TOKEN_1.._N). Only the count of
# slots checked is named here — the variable names themselves stay fixed.
MAX_TOKEN_ENV_SLOTS = 20


def _build_ado_client(global_cfg: dict, ado_url: str | None = None, ado_pat: str | None = None) -> ADOClient:
    """Build an ADO client, preferring an explicit token over environment variables over configuration.

    The org URL and personal access token (PAT) are each resolved independently: the caller-supplied
    value wins, then the matching environment variable, then ``global_cfg``.
    When one or more numbered ``ADO_PAT_1..19`` environment variables are set
    they take priority over a single PAT and build a rotating token manager;
    otherwise the single resolved PAT is used.

    Args:
        global_cfg: The parsed ``global`` block of the migration config. Read
            for ``ado_org_url`` and ``ado_pat`` as the last-resort source.
        ado_url: Org URL supplied by the caller, tried before the environment
            and config.
        ado_pat: Single PAT supplied by the caller, tried before the
            environment and config; ignored when numbered ``ADO_PAT_1..19``
            variables are present.

    Returns:
        An ``ADOClient`` bound to the resolved org URL, backed by a
        single-token or multi-token rotating ``ADOTokenManager`` depending on
        which PAT source was used.

    Raises:
        ConfigurationError: If no org URL resolves, or no PAT resolves from
            either the numbered variables, the single PAT source, or config.
    """
    ado_url = ado_url or os.environ.get("ADO_ORG_URL") or global_cfg.get("ado_org_url", "")
    ado_pat = ado_pat or os.environ.get("ADO_PAT") or global_cfg.get("ado_pat", "")
    ado_vars = [f"ADO_PAT_{i}" for i in range(1, MAX_TOKEN_ENV_SLOTS) if os.environ.get(f"ADO_PAT_{i}")]
    if not ado_url:
        raise ConfigurationError("ADO_ORG_URL required")
    if ado_vars:
        pats = [os.environ[v] for v in ado_vars if os.environ.get(v)]
        tm = ADOTokenManager(pats)
    elif ado_pat:
        tm = ADOTokenManager.from_single(ado_pat)
    else:
        raise ConfigurationError("ADO_PAT or ADO_PAT_1..N required")
    return ADOClient(ado_url, token_manager=tm)


def _build_gh_client(global_cfg: dict, gh_token: str | None = None) -> GHClient:
    """Build a GitHub client from whichever credential source is configured.

    Sources are tried in a fixed order so that the strongest configuration wins:
    a multi-token JSON config file, then numbered ``GH_TOKEN_1..19`` environment
    variables, then a single token from the caller, the environment or the
    config. GitHub App authentication is layered on top when the app id,
    installation id and private key path are all present.

    Args:
        global_cfg: The parsed ``global`` block of the migration config. Read for
            ``gh_token_config`` and, as a last resort, ``gh_token``.
        gh_token: A single token supplied by the caller, used only when neither
            the token-config file nor the numbered environment variables are
            available.

    Returns:
        A ``GHClient`` bound to a token manager that rotates across whichever
        credentials were found and, where configured, mints GitHub App
        installation tokens. No credential value is logged or returned.

    Raises:
        ConfigurationError: No token config file, no ``GH_TOKEN_n`` variables and
            no single token, so there is nothing to authenticate with.
    """
    token_config = global_cfg.get("gh_token_config", "")
    gh_token_vars = [f"GH_TOKEN_{i}" for i in range(1, MAX_TOKEN_ENV_SLOTS) if os.environ.get(f"GH_TOKEN_{i}")]
    if token_config and Path(token_config).exists():
        tm = TokenManager.from_json_config(token_config)
    elif gh_token_vars:
        tm = TokenManager.from_env(gh_token_vars)
    else:
        gh_token = gh_token or os.environ.get("GH_TOKEN") or global_cfg.get("gh_token", "")
        if not gh_token:
            raise ConfigurationError("GH_TOKEN required")
        tm = TokenManager.from_single_token(gh_token)
    app_id = os.environ.get("GH_APP_ID", "")
    install_id = os.environ.get("GH_APP_INSTALLATION_ID", "")
    key_path = os.environ.get("GH_APP_PRIVATE_KEY_PATH", "")
    if app_id and install_id and key_path:
        tm.configure_app_auth(app_id, install_id, key_path)
    return GHClient(tm)


class Accelerator:
    """Deterministic migration engine facade."""

    def __init__(self, db_path: str = "migration_state.db") -> None:
        """Bind the facade to the state database every call on it reads and writes.

        Args:
            db_path: Location of the migration state database. It is interpreted by
                the configured storage backend, so a filesystem path for SQLite.
        """
        self.db_path = db_path

    def discover(self, request: DiscoverRequest, ado_url: str | None = None, ado_pat: str | None = None) -> DiscoverResult:
        """Scan the Azure DevOps organisation and write a discovered-repository configuration.

        Args:
            request: Carries the migration configuration to load and the directory the
                discovery output is written to.
            ado_url: Azure DevOps organisation URL. Falls back to ``ADO_ORG_URL``
                and then to the configuration file when omitted.
            ado_pat: Azure DevOps credential. Falls back to the environment and then
                to the configuration file when omitted.

        Returns:
            A ``DiscoverResult`` naming the directory that now holds the generated
            repository and wave configuration.

        Raises:
            ConfigurationError: No organisation URL or no Azure DevOps credential
                could be resolved.
        """
        global_cfg, _ = ConfigLoader.load(request.config_path)
        ado = _build_ado_client(global_cfg, ado_url=ado_url, ado_pat=ado_pat)
        scanner = DiscoveryScanner(ado)
        scanner.scan(request.output_dir)
        return DiscoverResult(output_dir=request.output_dir)

    def run_wave(self, request: RunWaveRequest, ado_url: str | None = None, ado_pat: str | None = None, gh_token: str | None = None) -> RunWaveResult:
        """Execute one migration wave, or every wave in the configuration in order.

        ``request.dry_run`` is converted to an ``ExecutionMode`` here: this is the
        boundary between the boolean external shape and the internal mode (CA-001).

        Args:
            request: Config path, state database path, the optional ``wave_id`` to
                run — all waves when it is ``None`` — and the ``dry_run`` flag.
            ado_url: Azure DevOps organisation URL override.
            ado_pat: Azure DevOps credential override.
            gh_token: GitHub credential override, used only when no multi-token or
                token-config source is present.

        A quoted ``request.live_approval_id`` is verified here before anything
        else happens: it must name an ``approved`` live-execution approval for
        *this* wave and config, or nothing is migrated (GAP-018, GAP-063).
        Quoting no token leaves the run as it was — this is not the approval
        gate itself, which lives in the routes.

        The scope checked here names no profile: the SDK has no settings store
        to resolve the active one from. The profile-aware gate is
        ``require_migrate_live_approval`` on the route, which drops the token
        once it has verified it so that the weaker check cannot overrule it.

        Returns:
            A ``RunWaveResult`` for the last wave executed: its wave id, final
            status, and the completed, failed and total repository counts.

        Raises:
            ConfigurationError: The quoted live approval does not exist or was
                not granted, the requested wave is not in the config, or Azure
                DevOps or GitHub credentials could not be resolved.
        """
        db = create_state_db(request.db_path)
        if request.live_approval_id:
            scope_id = migrate_scope_id(None, request.wave_id, request.config_path)
            if not LiveApprovalStore(request.db_path).is_approved_for(
                request.live_approval_id,
                scope_type=MIGRATE_JOB_SCOPE_TYPE,
                scope_id=scope_id,
            ):
                raise ConfigurationError(
                    f"live_approval_id {request.live_approval_id!r} is not an "
                    f"approved live-execution approval for this migrate scope "
                    f"({scope_id}). Nothing was migrated."
                )
        global_cfg, waves = ConfigLoader.load(request.config_path)
        ado = _build_ado_client(global_cfg, ado_url=ado_url, ado_pat=ado_pat)
        gh = _build_gh_client(global_cfg, gh_token=gh_token)
        engine = MigrationEngine(
            global_cfg, ado, gh, db,
            mode=ExecutionMode.from_dry_run(dry_run=request.dry_run),
        )
        executor = BatchExecutor(
            engine, db, ProgressTracker(total_repos=1, total_pipelines=1),
        )
        targets = [w for w in waves if request.wave_id is None or w.wave_id == request.wave_id]
        if not targets:
            raise ConfigurationError(f"Wave {request.wave_id} not found")
        summary = {"completed": 0, "failed": 0, "total": 0, "wave_id": 0, "status": "completed"}
        for w in targets:
            s = executor.execute_wave(
                w, mode=ExecutionMode.from_dry_run(dry_run=request.dry_run),
            )
            summary = s
        # execute_wave's own return (see its docstring) always carries wave_id,
        # status, completed, failed, total and dry_run with these exact types;
        # the two extra keys it also returns (name, repos) are silently
        # ignored by RunWaveResult. mypy widens summary's static type to
        # dict[str, object] across the loop reassignment, which it can't
        # verify against RunWaveResult's per-field types.
        return RunWaveResult(**cast("dict[str, Any]", summary))

    def run_phase(self, request: PhaseRunRequest, ado_url: str | None = None, ado_pat: str | None = None, gh_token: str | None = None) -> PhaseRunResult:
        """Execute every repository assigned to a migration phase, in batches.

        The gate on the preceding phase is always evaluated. ``request.force`` only
        escalates a blocking gate, it never skips it: forcing past one requires a
        non-empty ``request.override_reason``, and the override is recorded against
        the prior phase so the escalation stays auditable (GAP-009).

        Args:
            request: Config path, state database path, the phase to run, the
                ``dry_run`` flag, and ``force`` plus ``override_reason`` for a
                deliberate escalation past a blocking gate.
            ado_url: Azure DevOps organisation URL override.
            ado_pat: Azure DevOps credential override.
            gh_token: GitHub credential override, used only when no multi-token or
                token-config source is present.

        Returns:
            A ``PhaseRunResult`` with the phase name, the completed and failed
            repository counts, and how many batches ran versus were skipped.

        Raises:
            ConfigurationError: The prior phase gate blocks and ``force`` is unset;
                ``force`` is set without an ``override_reason``; or Azure DevOps or
                GitHub credentials could not be resolved.
        """
        global_cfg, waves = ConfigLoader.load(request.config_path)
        ado = _build_ado_client(global_cfg, ado_url=ado_url, ado_pat=ado_pat)
        gh = _build_gh_client(global_cfg, gh_token=gh_token)
        db = create_state_db(request.db_path)
        phase_t = PhaseType(request.phase)
        from ado2gh.models import PHASE_ORDER
        prev_idx = PHASE_ORDER.index(phase_t) - 1
        if prev_idx >= 0:
            prior = PHASE_ORDER[prev_idx]
            checker = PhaseGateChecker(db)
            # The gate is always evaluated; `force` escalates it, never skips it.
            if not checker.can_advance(prior):
                if not request.force:
                    raise ConfigurationError(f"Gate blocked for prior phase {prior.value}")
                reason = (request.override_reason or "").strip()
                if not reason:
                    raise ConfigurationError(
                        f"Gate blocked for prior phase {prior.value}: forcing past it "
                        "requires override_reason"
                    )
                checker.override(prior, reason)
        phase_scores = db.get_risk_scores_for_phase(phase_t)
        total_pipes = sum(
            db.inventory_count_for_repo(s["project"], s["repo_name"]) for s in phase_scores
        )
        engine = MigrationEngine(
            global_cfg, ado, gh, db,
            mode=ExecutionMode.from_dry_run(dry_run=request.dry_run),
        )
        executor = BatchExecutor(
            engine, db,
            ProgressTracker(total_repos=max(1, len(phase_scores)),
                            total_pipelines=max(1, total_pipes)),
        )
        summary = executor.execute_phase(
            phase_t, waves, mode=ExecutionMode.from_dry_run(dry_run=request.dry_run),
        )
        return PhaseRunResult(**summary)

    def validate(self, request: ValidateRequest) -> ValidateResult:
        """Compare migrated repositories against their Azure DevOps sources.

        Verification is commit-level: the HEAD commit of each target repository is
        compared with its source, so a repository that exists but is empty fails.
        Unlike the other run methods this one takes no credential overrides: it
        resolves both clients from the stored settings.

        Args:
            request: Where to read the repositories to check from — config path,
                inline config, input file or inline text — and where to write the
                report.

        Returns:
            A ``ValidateResult`` with the number of repositories checked, how many
            passed and failed, the report path when one was written, and a per-repo
            detail row for each comparison.
        """
        from ado2gh.api.settings_store import SettingsStore
        from ado2gh.api.validation_run import run_validation

        return run_validation(request, SettingsStore())

    def status(self, db_path: Optional[str] = None) -> StatusSnapshot:
        """Read the current migration state out of the state database.

        Args:
            db_path: State database to read. Defaults to the one this facade was
                constructed with.

        Returns:
            A ``StatusSnapshot`` holding every recorded migration row — repository,
            phase, status and timestamps — and the number of pipelines currently in
            the inventory.
        """
        db = create_state_db(db_path or self.db_path)
        return StatusSnapshot(
            migrations=db.get_all_migrations(),
            pipeline_inventory_count=db.inventory_count(),
        )

    def inventory(
        self,
        config_path: str,
        projects: list[str] | None = None,
        parallel: int = 12,
        ado_url: str | None = None,
        ado_pat: str | None = None,
    ) -> dict:
        """Scan Azure DevOps pipelines into the state database and summarise them.

        Args:
            config_path: Migration config to resolve Azure DevOps settings from.
            projects: Projects to scan. Every project in the organisation is scanned
                when omitted.
            parallel: Number of worker threads used to scan projects concurrently.
            ado_url: Azure DevOps organisation URL override.
            ado_pat: Azure DevOps credential override.

        Returns:
            A dict with ``projects`` mapping each project name to its own counts,
            plus the organisation totals ``pipelines``, ``build``, ``release`` and
            ``projects``.

        Raises:
            ConfigurationError: No organisation URL or no Azure DevOps credential
                could be resolved.
        """
        from ado2gh.pipelines.inventory import PipelineInventoryBuilder, summarize_project_inventory
        global_cfg, _ = ConfigLoader.load(config_path)
        ado = _build_ado_client(global_cfg, ado_url=ado_url, ado_pat=ado_pat)
        db = create_state_db(self.db_path)
        if not projects:
            projects = [p["name"] for p in ado.list_projects()]
        # The point of this scan is the persisted inventory the caller then reads,
        # so it asks for LIVE rather than inheriting the dry-run default (GAP-078).
        project_summary = PipelineInventoryBuilder(
            ado, db, parallel=parallel, mode=ExecutionMode.LIVE,
        ).build_for_projects(
            projects,
        )
        totals = summarize_project_inventory(project_summary)
        return {"projects": project_summary, **totals}
