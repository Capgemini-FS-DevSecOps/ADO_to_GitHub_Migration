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


DISCOVERY_DETAIL_KEY = "__discovery_detail__"

DISCOVERY_DETAIL_FIELDS = (
    "project_details",
    "org_inventory",
    "inventory_gaps",
    "warnings",
    "status",
    "pipeline_inventory",
    "artifacts",
    "boards",
    "test_plans",
)


def pack_scan_summary_json(raw: dict[str, Any]) -> dict[str, Any]:
    """Phase buckets + embedded discovery inventory for profile_scans.summary_json."""
    summary = {
        k: {kk: vv for kk, vv in v.items() if kk != "repos"}
        for k, v in raw.get("recommendations", {}).items()
    }
    detail = {k: raw[k] for k in DISCOVERY_DETAIL_FIELDS if raw.get(k) is not None}
    if detail:
        summary[DISCOVERY_DETAIL_KEY] = detail
    return summary


def extract_discovery_fields(data: dict[str, Any] | None) -> dict[str, Any]:
    """Read service-connection inventory and related fields from scan payloads."""
    if not data:
        return {}
    out: dict[str, Any] = {}
    for key in DISCOVERY_DETAIL_FIELDS:
        if data.get(key) is not None:
            out[key] = data[key]
    nested = data.get(DISCOVERY_DETAIL_KEY)
    if isinstance(nested, dict):
        for key, value in nested.items():
            if value is not None:
                out[key] = value
    return out


def merge_scan_payload(
    base: dict[str, Any] | None,
    extra: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not base:
        return extra
    if not extra:
        return base
    merged = dict(base)
    for key in DISCOVERY_DETAIL_FIELDS:
        if not merged.get(key) and extra.get(key) is not None:
            merged[key] = extra[key]
    return merged


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


def _summarize_service_connections(svc_conns: list[dict]) -> list[dict[str, Any]]:
    return [
        {
            "name": sc.get("name", ""),
            "type": sc.get("type", ""),
            "id": sc.get("id", ""),
            "is_ready": sc.get("isReady", True),
        }
        for sc in svc_conns
        if sc.get("name")
    ]


def _summarize_variable_groups(var_groups: list[dict]) -> list[dict[str, Any]]:
    return [
        {
            "name": vg.get("name", ""),
            "id": vg.get("id", ""),
            "variable_count": len(vg.get("variables") or {}),
            "is_shared": bool(vg.get("isShared")),
        }
        for vg in var_groups
        if vg.get("name")
    ]


def run_pipeline_inventory_scan(
    ado_org_url: str,
    ado_pat: str,
    db_path: str,
    *,
    projects: list[str] | None = None,
    parallel: int = 12,
) -> dict[str, Any]:
    """Deep pipeline inventory stored in StateDB for conversion and secrets mapping."""
    from ado2gh.api.state_db import create_state_db
    from ado2gh.pipelines.inventory import PipelineInventoryBuilder

    ado = _build_ado_client(ado_org_url, ado_pat)
    db = create_state_db(db_path)
    if not projects:
        projects = [p["name"] for p in ado.list_projects() if p.get("name")]
    summary = PipelineInventoryBuilder(ado, db, parallel=parallel, dry_run=False).build_for_projects(
        projects,
    )
    return {
        "projects": summary,
        "total_pipelines": sum(int(s.get("total", 0)) for s in summary.values()),
        "inventory_count": db.inventory_count(),
    }


def build_inventory_gaps(project_details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Surface ADO assets that need operator input before secrets/pipeline conversion."""
    gaps: list[dict[str, Any]] = []
    for project in project_details:
        proj_name = project.get("project", "")
        for sc in project.get("service_connections") or []:
            gaps.append({
                "type": "service_connection",
                "project": proj_name,
                "name": sc.get("name", ""),
                "connection_type": sc.get("type", ""),
                "field": f"secret_mapping__{proj_name}__{sc.get('name', '')}",
                "hint": "GitHub secret name or OIDC federated credential to use",
            })
        for vg in project.get("variable_groups") or []:
            gaps.append({
                "type": "variable_group",
                "project": proj_name,
                "name": vg.get("name", ""),
                "field": f"variable_group__{proj_name}__{vg.get('name', '')}",
                "hint": "Confirm variable group secrets were created in GitHub (values are not readable from ADO)",
            })
    return gaps


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

            try:
                environments = self.ado.list_environments(proj_name)
            except Exception:
                environments = []

            try:
                artifact_feeds = self.ado.list_artifacts(proj_name)
            except Exception:
                artifact_feeds = []

            try:
                teams = self.ado.list_teams(proj_name)
            except Exception:
                teams = []

            try:
                iterations = self.ado.list_iterations(proj_name)
            except Exception:
                iterations = []

            try:
                work_item_types = self.ado.list_work_item_types(proj_name)
            except Exception:
                work_item_types = []

            try:
                work_items = self.ado.list_work_items(proj_name, top=1)
                work_item_count = len(work_items)
            except Exception:
                work_item_count = 0

            try:
                test_plans = self.ado.list_test_plans(proj_name)
            except Exception:
                test_plans = []

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
            detail["service_connection_count"] = len(svc_conns)
            detail["service_connections"] = _summarize_service_connections(svc_conns)
            detail["variable_group_count"] = len(var_groups)
            detail["variable_groups"] = _summarize_variable_groups(var_groups)
            detail["environment_count"] = len(environments)
            detail["environments"] = [e.get("name", "") for e in environments if e.get("name")]
            detail["artifact_feed_count"] = len(artifact_feeds)
            detail["artifact_feeds"] = [
                {"name": f.get("name", ""), "id": f.get("id", ""), "is_public": bool(f.get("isPublic"))}
                for f in artifact_feeds if f.get("name")
            ]
            detail["team_count"] = len(teams)
            detail["teams"] = [t.get("name", "") for t in teams if t.get("name")]
            detail["iteration_count"] = len(iterations)
            detail["work_item_types"] = [w.get("name", "") for w in work_item_types if w.get("name")]
            detail["work_item_type_count"] = len(work_item_types)
            detail["work_item_count"] = work_item_count
            detail["test_plan_count"] = len(test_plans)
            detail["test_plans"] = [
                {"name": p.get("name", ""), "id": p.get("id", ""), "state": p.get("state", "")}
                for p in test_plans if p.get("name")
            ]

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
        all_assigned = assigned.get("unassigned", [])
        buckets: dict[str, Any] = {
            "unassigned": {
                "phase": "unassigned",
                "phase_name": "Unassigned",
                "repo_count": len(all_assigned),
                "risk_min": round(min((s.total_score for s in all_assigned), default=0), 1),
                "risk_max": round(max((s.total_score for s in all_assigned), default=0), 1),
                "risk_band_max": 100,
                "rationale": f"{len(all_assigned)} repos with risk scores — no automatic phase assignment.",
                "repos": [s.to_dict() for s in all_assigned],
            },
        }

        warnings = build_empty_scan_warnings(projects_scanned, repos_scanned, project_details)
        scan_status = "ok" if repos_scanned else ("error" if any(p.get("error") for p in project_details) else "empty")

        org_inventory = {
            "total_service_connections": sum(p.get("service_connection_count", 0) for p in project_details),
            "total_variable_groups": sum(p.get("variable_group_count", 0) for p in project_details),
            "total_environments": sum(p.get("environment_count", 0) for p in project_details),
            "total_pipeline_stubs": sum(p.get("pipeline_count", 0) for p in project_details),
            "total_artifact_feeds": sum(p.get("artifact_feed_count", 0) for p in project_details),
            "total_teams": sum(p.get("team_count", 0) for p in project_details),
            "total_iterations": sum(p.get("iteration_count", 0) for p in project_details),
            "total_work_items": sum(p.get("work_item_count", 0) for p in project_details),
            "total_work_item_types": sum(p.get("work_item_type_count", 0) for p in project_details),
            "total_test_plans": sum(p.get("test_plan_count", 0) for p in project_details),
        }

        return {
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "projects_scanned": projects_scanned,
            "repos_scanned": repos_scanned,
            "total_repos": repos_scanned,
            "gh_org": self.gh_org,
            "recommendations": buckets,
            "project_details": project_details,
            "org_inventory": org_inventory,
            "inventory_gaps": build_inventory_gaps(project_details),
            "warnings": warnings,
            "status": scan_status,
        }


def scan_with_credentials(
    ado_org_url: str,
    ado_pat: str,
    gh_org: str = "",
    max_repos: int | None = None,
    phase_definitions: list | None = None,
    *,
    db_path: str | None = None,
    run_inventory: bool = True,
    pipeline_parallel: int = 12,
) -> dict[str, Any]:
    ado = _build_ado_client(ado_org_url, ado_pat)
    raw = MigrationScanner(ado, gh_org=gh_org, phase_definitions=phase_definitions).scan(
        max_repos=max_repos,
    )
    if run_inventory and db_path:
        try:
            inventory = run_pipeline_inventory_scan(
                ado_org_url,
                ado_pat,
                db_path,
                parallel=pipeline_parallel,
            )
            raw["pipeline_inventory"] = inventory
            org = raw.setdefault("org_inventory", {})
            org["pipeline_inventory_count"] = inventory.get("inventory_count", 0)
            org["total_pipelines_indexed"] = inventory.get("total_pipelines", 0)
        except Exception as exc:
            raw.setdefault("warnings", []).append(
                f"Pipeline inventory scan failed: {exc}"
            )
    return raw


def persist_scan_results(
    profile_id: str,
    results: dict[str, Any],
    *,
    preserve_manual_assignments: bool = True,
) -> Path:
    """Persist scan to SQLite/state DB and keep JSON backup for portability."""
    from ado2gh.api.state_db import get_state_db

    db = get_state_db()
    if hasattr(db, "save_profile_scan"):
        db.save_profile_scan(
            profile_id,
            results,
            preserve_manual_assignments=preserve_manual_assignments,
        )

    path = _scan_results_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(db, "build_profile_scan_payload"):
        payload = db.build_profile_scan_payload(profile_id) or results
    else:
        payload = results
    path.write_text(
        json.dumps({"profile_id": profile_id, **payload}, indent=2),
        encoding="utf-8",
    )
    return path


def load_scan_results(profile_id: str) -> dict[str, Any] | None:
    from ado2gh.api.state_db import get_state_db

    db = get_state_db()
    payload: dict[str, Any] | None = None
    if hasattr(db, "build_profile_scan_payload"):
        payload = db.build_profile_scan_payload(profile_id)

    path = _scan_results_path(profile_id)
    file_data: dict[str, Any] | None = None
    if path.exists():
        try:
            file_data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            file_data = None

    payload = merge_scan_payload(payload, file_data)
    if payload:
        return payload

    if file_data and (file_data.get("recommendations") or file_data.get("repos_scanned", 0) > 0):
        return file_data
    return None
