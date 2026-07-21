"""Public Accelerator SDK facade — no Click dependency."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

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
from ado2gh.clients.ado_client import ADOClient
from ado2gh.clients.ado_token_manager import ADOTokenManager
from ado2gh.clients.gh_client import GHClient
from ado2gh.clients.token_manager import TokenManager
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.core.discovery import DiscoveryScanner
from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.models import PhaseType
from ado2gh.phase.batch_executor import BatchExecutor
from ado2gh.phase.gate_checker import PhaseGateChecker
from ado2gh.phase.progress_tracker import ProgressTracker
from ado2gh.state.factory import create_state_db


def _build_ado_client(global_cfg: dict, ado_url: str | None = None, ado_pat: str | None = None) -> ADOClient:
    ado_url = ado_url or os.environ.get("ADO_ORG_URL") or global_cfg.get("ado_org_url", "")
    ado_pat = ado_pat or os.environ.get("ADO_PAT") or global_cfg.get("ado_pat", "")
    ado_vars = [f"ADO_PAT_{i}" for i in range(1, 20) if os.environ.get(f"ADO_PAT_{i}")]
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


def _build_gh_client(global_cfg: dict, gh_token: str | None = None, gh_org: str | None = None) -> GHClient:
    token_config = global_cfg.get("gh_token_config", "")
    gh_token_vars = [f"GH_TOKEN_{i}" for i in range(1, 20) if os.environ.get(f"GH_TOKEN_{i}")]
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

    def __init__(self, db_path: str = "migration_state.db"):
        self.db_path = db_path

    def discover(self, request: DiscoverRequest, ado_url: str | None = None, ado_pat: str | None = None) -> DiscoverResult:
        global_cfg, _ = ConfigLoader.load(request.config_path)
        ado = _build_ado_client(global_cfg, ado_url=ado_url, ado_pat=ado_pat)
        scanner = DiscoveryScanner(ado)
        scanner.scan(request.output_dir)
        return DiscoverResult(output_dir=request.output_dir)

    def run_wave(self, request: RunWaveRequest, ado_url: str | None = None, ado_pat: str | None = None, gh_token: str | None = None, gh_org: str | None = None) -> RunWaveResult:
        global_cfg, waves = ConfigLoader.load(request.config_path)
        ado = _build_ado_client(global_cfg, ado_url=ado_url, ado_pat=ado_pat)
        gh = _build_gh_client(global_cfg, gh_token=gh_token, gh_org=gh_org)
        db = create_state_db(request.db_path)
        engine = MigrationEngine(global_cfg, ado, gh, db, dry_run=request.dry_run)
        executor = BatchExecutor(
            engine, db, ProgressTracker(total_repos=1, total_pipelines=1),
        )
        targets = [w for w in waves if request.wave_id is None or w.wave_id == request.wave_id]
        if not targets:
            raise ConfigurationError(f"Wave {request.wave_id} not found")
        summary = {"completed": 0, "failed": 0, "total": 0, "wave_id": 0, "status": "completed"}
        for w in targets:
            s = executor.execute_wave(w, dry_run=request.dry_run)
            summary = s
        return RunWaveResult(**summary)

    def run_phase(self, request: PhaseRunRequest, ado_url: str | None = None, ado_pat: str | None = None, gh_token: str | None = None, gh_org: str | None = None) -> PhaseRunResult:
        global_cfg, waves = ConfigLoader.load(request.config_path)
        ado = _build_ado_client(global_cfg, ado_url=ado_url, ado_pat=ado_pat)
        gh = _build_gh_client(global_cfg, gh_token=gh_token, gh_org=gh_org)
        db = create_state_db(request.db_path)
        phase_t = PhaseType(request.phase)
        if not request.force:
            checker = PhaseGateChecker(db)
            from ado2gh.models import PHASE_ORDER
            prev_idx = PHASE_ORDER.index(phase_t) - 1
            if prev_idx >= 0 and not checker.can_advance(PHASE_ORDER[prev_idx]):
                raise ConfigurationError(
                    f"Gate blocked for prior phase {PHASE_ORDER[prev_idx].value}"
                )
        phase_scores = db.get_risk_scores_for_phase(phase_t)
        total_pipes = sum(
            db.inventory_count_for_repo(s["project"], s["repo_name"]) for s in phase_scores
        )
        engine = MigrationEngine(global_cfg, ado, gh, db, dry_run=request.dry_run)
        executor = BatchExecutor(
            engine, db,
            ProgressTracker(total_repos=max(1, len(phase_scores)),
                            total_pipelines=max(1, total_pipes)),
        )
        summary = executor.execute_phase(phase_t, waves, dry_run=request.dry_run)
        return PhaseRunResult(**summary)

    def validate(self, request: ValidateRequest, ado_url: str | None = None, ado_pat: str | None = None, gh_token: str | None = None, gh_org: str | None = None) -> ValidateResult:
        from ado2gh.api.settings_store import SettingsStore
        from ado2gh.api.validation_run import run_validation

        return run_validation(request, SettingsStore())

    def status(self, db_path: Optional[str] = None) -> StatusSnapshot:
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
        from ado2gh.pipelines.inventory import PipelineInventoryBuilder, summarize_project_inventory
        global_cfg, _ = ConfigLoader.load(config_path)
        ado = _build_ado_client(global_cfg, ado_url=ado_url, ado_pat=ado_pat)
        db = create_state_db(self.db_path)
        if not projects:
            projects = [p["name"] for p in ado.list_projects()]
        project_summary = PipelineInventoryBuilder(ado, db, parallel=parallel).build_for_projects(
            projects,
        )
        totals = summarize_project_inventory(project_summary)
        return {"projects": project_summary, **totals}
