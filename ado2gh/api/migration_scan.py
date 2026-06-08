"""ADO org scan with risk scoring and phase recommendations for the migration UI."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ado2gh.clients.ado_client import ADOClient
from ado2gh.clients.ado_token_manager import ADOTokenManager
from ado2gh.api.phase_definitions import (
    ConfigurableWaveAssigner,
    PhaseDefinition,
    parse_phase_definitions,
    phase_rationale,
)
from ado2gh.api.scan_diagnostics import build_empty_scan_warnings
from ado2gh.models import PipelineComplexity, PipelineMetadata, PipelineType, RiskScore
from ado2gh.phase.risk_scorer import RiskScorer


def _scan_results_path(profile_id: str | None = None) -> Path:
    base = Path(os.environ.get("ADO2GH_DATA_DIR", "."))
    if profile_id:
        return base / f"scan_{profile_id}.json"
    return base / "scan_preview.json"


def _build_ado_client(ado_org_url: str, ado_pat: str) -> ADOClient:
    return ADOClient(ado_org_url.rstrip("/"), token_manager=ADOTokenManager.from_single(ado_pat))


def _stub_pipeline(defn: dict, project: str) -> PipelineMetadata:
    process_type = defn.get("process", {}).get("type", 1)
    ptype = PipelineType.YAML if process_type == 2 else PipelineType.CLASSIC
    repo = defn.get("repository", {})
    return PipelineMetadata(
        pipeline_id=defn.get("id", 0),
        pipeline_name=defn.get("name", ""),
        pipeline_type=ptype,
        project=project,
        repo_id=repo.get("id", ""),
        repo_name=repo.get("name", ""),
        complexity=PipelineComplexity.SIMPLE,
    )


def _phase_rationale(phase_def: PhaseDefinition, scores: list[RiskScore]) -> str:
    return phase_rationale(phase_def, scores)


class MigrationScanner:
    """Scan ADO org repos, score risk, and bucket into migration phases."""

    def __init__(
        self,
        ado: ADOClient,
        gh_org: str = "",
        phase_definitions: list[PhaseDefinition] | list[dict[str, Any]] | None = None,
    ):
        self.ado = ado
        self.gh_org = gh_org
        self.scorer = RiskScorer()
        if phase_definitions and isinstance(phase_definitions[0], PhaseDefinition):
            self.phase_defs = sorted(phase_definitions, key=lambda p: p.order)
        else:
            self.phase_defs = parse_phase_definitions(phase_definitions)  # type: ignore[arg-type]
        self.assigner = ConfigurableWaveAssigner(self.phase_defs)

    def scan(self, max_repos: int | None = None) -> dict[str, Any]:
        projects = self.ado.list_projects()
        all_scores = []
        repos_scanned = 0
        projects_scanned = 0
        project_details: list[dict[str, Any]] = []

        for proj in projects:
            proj_name = proj.get("name", "")
            if not proj_name:
                continue
            projects_scanned += 1
            detail: dict[str, Any] = {
                "project": proj_name,
                "project_id": proj.get("id", ""),
                "repo_count": 0,
                "disabled_count": 0,
                "pipeline_count": 0,
                "error": None,
            }

            try:
                var_groups = self.ado.list_variable_groups(proj_name)
            except Exception:
                var_groups = []

            try:
                svc_conns = self.ado.list_service_connections(proj_name)
            except Exception:
                svc_conns = []

            project_pipelines: list[PipelineMetadata] = []
            try:
                for stub in self.ado.list_all_pipelines(proj_name):
                    try:
                        defn = self.ado.get_build_definition_full(proj_name, stub["id"])
                        project_pipelines.append(_stub_pipeline(defn, proj_name))
                    except Exception:
                        continue
            except Exception:
                pass
            detail["pipeline_count"] = len(project_pipelines)

            try:
                repos = self.ado.list_repos(proj_name)
            except Exception as exc:
                detail["error"] = str(exc)
                project_details.append(detail)
                continue

            detail["repo_count"] = len(repos)
            disabled = 0
            for repo in repos:
                if max_repos is not None and repos_scanned >= max_repos:
                    break
                if repo.get("isDisabled"):
                    disabled += 1
                    continue

                repo_id = repo.get("id", "")
                repo_name = repo.get("name", "")
                repo_pipes = [
                    p for p in project_pipelines
                    if p.repo_name == repo_name or p.repo_id == repo_id
                ]

                try:
                    stats = self.ado.get_repo_stats(proj_name, repo_id)
                except Exception:
                    stats = {"branch_count": 0}

                try:
                    commits = self.ado.get_repo_commits(proj_name, repo_id, top=1)
                except Exception:
                    commits = []

                score = self.scorer.score(
                    proj_name, repo, repo_pipes, stats, commits,
                    var_groups, svc_conns, gh_org=self.gh_org,
                )
                all_scores.append(score)
                repos_scanned += 1

            detail["disabled_count"] = disabled
            project_details.append(detail)

            if max_repos is not None and repos_scanned >= max_repos:
                break

        assigned = self.assigner.assign(all_scores, gh_org=self.gh_org)
        buckets: dict[str, Any] = {}
        for phase_def in self.phase_defs:
            phase_scores = assigned.get(phase_def.id, [])
            buckets[phase_def.id] = {
                "phase": phase_def.id,
                "phase_name": phase_def.name,
                "repo_count": len(phase_scores),
                "risk_min": round(min((s.total_score for s in phase_scores), default=0), 1),
                "risk_max": round(max((s.total_score for s in phase_scores), default=0), 1),
                "risk_band_max": phase_def.risk_max,
                "rationale": _phase_rationale(phase_def, phase_scores),
                "repos": [s.to_dict() for s in phase_scores],
            }

        warnings = build_empty_scan_warnings(projects_scanned, repos_scanned, project_details)
        scan_status = "ok" if repos_scanned else ("error" if any(p.get("error") for p in project_details) else "empty")

        return {
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "projects_scanned": projects_scanned,
            "repos_scanned": repos_scanned,
            "total_repos": repos_scanned,
            "gh_org": self.gh_org,
            "recommendations": buckets,
            "project_details": project_details,
            "warnings": warnings,
            "status": scan_status,
        }


def scan_with_credentials(
    ado_org_url: str,
    ado_pat: str,
    gh_org: str = "",
    max_repos: int | None = None,
    phase_definitions: list | None = None,
) -> dict[str, Any]:
    ado = _build_ado_client(ado_org_url, ado_pat)
    return MigrationScanner(ado, gh_org=gh_org, phase_definitions=phase_definitions).scan(
        max_repos=max_repos,
    )


def persist_scan_results(profile_id: str, results: dict[str, Any]) -> Path:
    """Persist scan to SQLite/state DB and keep JSON backup for portability."""
    from ado2gh.api.state_db import get_state_db

    db = get_state_db()
    if hasattr(db, "save_profile_scan"):
        db.save_profile_scan(profile_id, results)

    path = _scan_results_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"profile_id": profile_id, **results}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_scan_results(profile_id: str) -> dict[str, Any] | None:
    from ado2gh.api.state_db import get_state_db

    db = get_state_db()
    if hasattr(db, "build_profile_scan_payload"):
        payload = db.build_profile_scan_payload(profile_id)
        if payload:
            return payload

    path = _scan_results_path(profile_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("recommendations"):
        return data
    return data if data.get("repos_scanned", 0) > 0 else None
