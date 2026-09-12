"""Pipeline readiness report — assess which pipelines can auto-convert vs need manual work."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ado2gh.logging_config import console, log
from ado2gh.models import (
    PipelineComplexity,
    PipelineMetadata,
    PipelineType,
    RepoConfig,
)
from ado2gh.pipelines.repo_association import infer_pipeline_repo_name
from ado2gh.state.base import StateDBBase


def workflow_push_readiness(db: StateDBBase | None, repo: RepoConfig) -> dict[str, list[str]]:
    """Readiness verdict for a live workflow push to one repo (GAP-016).

    Consults the same auto/assisted/manual assessment the ``pipeline-readiness``
    command reports, so the push path and the report agree by construction
    instead of the push being gated on a caller-supplied literal.

    Fails closed: if readiness cannot be established at all, that is a blocker,
    not an approval.

    Args:
        db: State store holding the pipeline inventory, or ``None`` when the
            caller has no state store at all.
        repo: The repo the workflows would be pushed to.

    Returns:
        ``{"blockers": [...], "notes": [...]}``. A non-empty ``blockers`` means
        the live push must not proceed: a pipeline graded ``manual`` has hard
        conversion blockers and its generated YAML needs a human before it
        lands on the destination repo. ``notes`` are reviewer-facing caveats
        for pipelines graded ``assisted`` — they do not block.
    """
    if db is None:
        return {"blockers": ["pipeline readiness unavailable (no state store)"], "notes": []}
    try:
        assessments = PipelineReadinessReport(db).generate(repos=[repo]).get("pipelines") or []
    except Exception:
        # Never surface the underlying error text: a Postgres DSN can carry a password (CA-003).
        log.warning(
            "pipeline readiness assessment failed for %s/%s",
            repo.ado_project, repo.ado_repo, exc_info=True,
        )
        return {"blockers": ["pipeline readiness assessment failed"], "notes": []}

    blockers: list[str] = []
    notes: list[str] = []
    for a in assessments:
        name = a.get("pipeline_name") or f"pipeline {a.get('pipeline_id', '?')}"
        if a.get("classification") == "manual":
            reasons = "; ".join(a.get("blockers") or []) or "manual conversion required"
            blockers.append(f"{name}: {reasons}")
        elif a.get("classification") == "assisted":
            caveats = "; ".join(a.get("warnings") or []) or "needs review before enabling"
            notes.append(f"{name}: {caveats}")
    return {"blockers": blockers, "notes": notes}


class PipelineReadinessReport:
    """Analyze pipeline inventory and generate a readiness assessment.

    For each pipeline, estimates:
    - Conversion difficulty (auto / assisted / manual)
    - Estimated effort in hours
    - Blockers (unsupported tasks, self-hosted pools, etc.)
    - Recommendations
    """

    # Effort estimates in hours by complexity and type
    EFFORT_MATRIX = {
        (PipelineType.YAML, PipelineComplexity.SIMPLE): 0.5,
        (PipelineType.YAML, PipelineComplexity.MEDIUM): 2.0,
        (PipelineType.YAML, PipelineComplexity.COMPLEX): 8.0,
        (PipelineType.CLASSIC, PipelineComplexity.SIMPLE): 2.0,
        (PipelineType.CLASSIC, PipelineComplexity.MEDIUM): 6.0,
        (PipelineType.CLASSIC, PipelineComplexity.COMPLEX): 16.0,
        (PipelineType.RELEASE, PipelineComplexity.SIMPLE): 4.0,
        (PipelineType.RELEASE, PipelineComplexity.MEDIUM): 12.0,
        (PipelineType.RELEASE, PipelineComplexity.COMPLEX): 24.0,
    }

    # Tasks that block full auto-conversion
    BLOCKER_TASKS = {
        "AzureKeyVault@2", "AzureKeyVault@1",
        "AzureRmWebAppDeployment@4",
        "ServiceFabricDeploy@1", "ServiceFabricComposeDeploy@0",
        "SqlAzureDacpacDeployment@1",
        "IISWebAppDeploymentOnMachineGroup@0",
        "WindowsMachineFileCopy@2",
        "PackerBuild@1",
    }

    def __init__(self, db: StateDBBase) -> None:
        """Store the state store the pipeline inventory is read from.

        Args:
            db: State store holding the ``pipeline_inventory`` rows.
        """
        self.db = db

    def generate(self, repos: list[RepoConfig] | None = None,
                 output_path: str | None = None,
                 migration_lookup: dict[str, dict] | None = None,
                 repo_migration_lookup: dict[str, dict] | None = None) -> dict:
        """Generate a readiness report for the pipelines in the inventory.

        Args:
            repos: If given, restrict the scan to these repos; otherwise every
                inventoried pipeline is assessed.
            output_path: If given, also write the CSV and JSON reports there.
            migration_lookup: Pipeline migration rows keyed by pipeline, used
                to report what has already been converted.
            repo_migration_lookup: Repo migration rows keyed by repo, used to
                tell whether a pipeline's repo has landed on GitHub.

        Returns:
            A summary dict with the counts and effort estimate, plus the
            per-pipeline assessments under ``pipelines``.
        """
        migration_lookup = migration_lookup or {}
        repo_migration_lookup = repo_migration_lookup or {}
        project_repos: dict[str, list[dict[str, str]]] = {}
        if hasattr(self.db, "get_all_risk_scores"):
            try:
                for row in self.db.get_all_risk_scores():
                    project = row.get("project", "")
                    repo_name = row.get("repo_name", "")
                    if project and repo_name:
                        project_repos.setdefault(project, []).append({"name": repo_name})
            except Exception:
                project_repos = {}
        all_pipelines: list[PipelineMetadata] = []
        if repos:
            for repo in repos:
                all_pipelines.extend(
                    self.db.get_pipelines_for_repo(repo.ado_project, repo.ado_repo)
                )
        else:
            # All pipelines in inventory
            for row in self.db.get_all_inventory():
                try:
                    meta = PipelineMetadata.from_dict(json.loads(row["metadata_json"]))
                    # Deliberately not named `repos`: that outer variable is the
                    # `list[RepoConfig] | None` filter this branch runs without;
                    # this is unrelated per-project name-inference data.
                    repo_hints = project_repos.get(meta.project, [])
                    if repo_hints:
                        inferred = infer_pipeline_repo_name(
                            meta.pipeline_name,
                            meta.repo_name,
                            meta.repo_id,
                            repo_hints,
                        )
                        if inferred:
                            meta.repo_name = inferred
                    all_pipelines.append(meta)
                except Exception:
                    pass

        assessments = [
            self._assess_pipeline(p, migration_lookup, repo_migration_lookup)
            for p in all_pipelines
        ]

        summary = self._build_summary(assessments)
        summary["pipelines"] = assessments
        summary["by_conversion"] = {
            "auto": summary.get("auto", 0),
            "assisted": summary.get("assisted", 0),
            "manual": summary.get("manual", 0),
        }

        if output_path:
            self._write_csv(assessments, output_path)
            json_path = output_path.replace(".csv", ".json")
            Path(json_path).write_text(json.dumps({
                "summary": summary,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "pipelines": assessments,
            }, indent=2, default=str))
            log.info("Pipeline readiness report: %s", output_path)

        return summary

    def _assess_pipeline(
        self,
        pipe: PipelineMetadata,
        migration_lookup: dict[str, dict] | None = None,
        repo_migration_lookup: dict[str, dict] | None = None,
    ) -> dict:
        """Assess a single pipeline's migration readiness."""
        migration_lookup = migration_lookup or {}
        repo_migration_lookup = repo_migration_lookup or {}
        blockers: list[str] = []
        warnings: list[str] = []
        effort_hours = self.EFFORT_MATRIX.get(
            (pipe.pipeline_type, pipe.complexity), 4.0)

        # Check for blocker tasks
        for task in pipe.unsupported_tasks:
            if task in self.BLOCKER_TASKS:
                blockers.append(f"Unsupported task: {task}")
                effort_hours += 4.0

        # Check for self-hosted agent pools
        for pool in pipe.agent_pools:
            if pool not in ("ubuntu-latest", "windows-latest", "macos-latest",
                            "ubuntu-22.04", "ubuntu-20.04", "windows-2022",
                            "windows-2019", "macos-13"):
                blockers.append(f"Self-hosted pool: {pool}")
                effort_hours += 2.0

        # Check for variable groups (need manual secret setup)
        if pipe.variable_groups:
            vg_names = [vg.get("name", "?") for vg in pipe.variable_groups]
            warnings.append(f"Variable groups need manual setup: {', '.join(vg_names)}")
            effort_hours += 0.5 * len(pipe.variable_groups)

        # Check for service connections
        if pipe.service_connections:
            sc_names = [sc.get("name", "?") for sc in pipe.service_connections]
            warnings.append(f"Service connections need manual setup: {', '.join(sc_names)}")
            effort_hours += 1.0 * len(pipe.service_connections)

        # Check for environments with approvals
        approval_envs = [e for e in pipe.environments if e.required_approvers]
        if approval_envs:
            warnings.append(
                f"Environments with approvers need GH Environment setup: "
                f"{[e.name for e in approval_envs]}"
            )
            effort_hours += 0.5 * len(approval_envs)

        # Classic pipelines always need more work
        if pipe.pipeline_type == PipelineType.CLASSIC:
            warnings.append("Classic pipeline — no YAML source, best-effort conversion only")
        if pipe.pipeline_type == PipelineType.RELEASE:
            warnings.append("Release pipeline — map stages to GitHub Environments manually")

        # Determine conversion level
        if blockers:
            conversion = "manual"
        elif pipe.pipeline_type in (PipelineType.CLASSIC, PipelineType.RELEASE):
            conversion = "assisted"
        elif pipe.complexity == PipelineComplexity.COMPLEX or warnings:
            conversion = "assisted"
        else:
            conversion = "auto"

        mig_key = f"{pipe.project}:{pipe.pipeline_id}"
        mig_row = migration_lookup.get(mig_key)
        migration_status = "not_migrated"
        workflow_file = ""
        if mig_row:
            raw_status = str(mig_row.get("status") or "pending")
            if raw_status == "completed":
                migration_status = "migrated"
            elif raw_status in ("in_progress", "pending"):
                migration_status = "in_progress"
            elif raw_status == "failed":
                migration_status = "failed"
            else:
                migration_status = raw_status
            workflow_file = str(mig_row.get("workflow_file") or "")
        elif pipe.repo_name:
            repo_key = f"{pipe.project}:{pipe.repo_name}"
            repo_row = repo_migration_lookup.get(repo_key)
            if repo_row and str(repo_row.get("status")) == "completed":
                migration_status = "repo_migrated"

        return {
            "project": pipe.project,
            "pipeline_id": pipe.pipeline_id,
            "pipeline_name": pipe.pipeline_name,
            "pipeline_type": pipe.pipeline_type.value,
            "complexity": pipe.complexity.value,
            "repo_name": pipe.repo_name,
            "stages": len(pipe.stages),
            "environments": len(pipe.environments),
            "variable_groups": len(pipe.variable_groups),
            "service_connections": len(pipe.service_connections),
            "conversion": conversion,
            "classification": conversion,
            "migration_status": migration_status,
            "workflow_file": workflow_file,
            "effort_hours": round(effort_hours, 1),
            "blockers": blockers,
            "warnings": warnings,
            "last_run": pipe.last_run_result,
            "avg_duration_min": pipe.avg_duration_min,
        }

    def _build_summary(self, assessments: list[dict]) -> dict:
        """Aggregate per-pipeline assessments into the report's headline figures.

        Args:
            assessments: Assessment dicts as returned by :meth:`_assess_pipeline`.

        Returns:
            ``total_pipelines``; the ``auto``, ``assisted`` and ``manual`` conversion
            counts; ``auto_pct`` as the percentage converted automatically, ``0``
            when there are no pipelines; the ``by_type`` and ``by_complexity`` count
            maps; and the effort as ``total_effort_hours`` plus
            ``total_effort_days`` at eight hours to the day.
        """
        total = len(assessments)
        by_conversion: defaultdict[str, int] = defaultdict(int)
        by_type: defaultdict[str, int] = defaultdict(int)
        by_complexity: defaultdict[str, int] = defaultdict(int)
        total_effort = 0.0

        for a in assessments:
            by_conversion[a["conversion"]] += 1
            by_type[a["pipeline_type"]] += 1
            by_complexity[a["complexity"]] += 1
            total_effort += a["effort_hours"]

        return {
            "total_pipelines": total,
            "auto": by_conversion["auto"],
            "assisted": by_conversion["assisted"],
            "manual": by_conversion["manual"],
            "auto_pct": round(by_conversion["auto"] / total * 100, 1) if total else 0,
            "by_type": dict(by_type),
            "by_complexity": dict(by_complexity),
            "total_effort_hours": round(total_effort, 1),
            "total_effort_days": round(total_effort / 8, 1),
        }

    def _write_csv(self, assessments: list[dict], output_path: str) -> None:
        """Write one CSV row per assessed pipeline.

        Args:
            assessments: Per-pipeline assessment dicts.
            output_path: Destination CSV path; parent directories are created.
        """
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "project", "pipeline_name", "pipeline_type", "complexity",
                "repo_name", "conversion", "effort_hours", "stages",
                "environments", "variable_groups", "service_connections",
                "blockers", "warnings", "last_run",
            ])
            writer.writeheader()
            for a in assessments:
                row = {**a}
                row["blockers"] = "; ".join(row["blockers"])
                row["warnings"] = "; ".join(row["warnings"])
                writer.writerow({k: row[k] for k in writer.fieldnames})

    def print_summary(self, summary: dict) -> None:
        """Print the readiness summary as a console table.

        Args:
            summary: Summary dict as returned by :meth:`generate`.
        """
        from rich import box
        from rich.table import Table

        t = Table(title="Pipeline Readiness Assessment", box=box.ROUNDED)
        t.add_column("Metric", style="bold")
        t.add_column("Value", justify="right")

        t.add_row("Total pipelines", str(summary["total_pipelines"]))
        t.add_row("[green]Auto-convertible[/green]",
                  f"{summary['auto']} ({summary['auto_pct']}%)")
        t.add_row("[yellow]Assisted (needs review)[/yellow]",
                  str(summary["assisted"]))
        t.add_row("[red]Manual (blockers)[/red]",
                  str(summary["manual"]))
        t.add_row("", "")
        t.add_row("Estimated total effort", f"{summary['total_effort_hours']} hours")
        t.add_row("Estimated calendar days", f"{summary['total_effort_days']} days")
        t.add_row("", "")
        for ptype, count in summary.get("by_type", {}).items():
            t.add_row(f"Type: {ptype}", str(count))

        console.print(t)
