"""Pipeline readiness report — assess which pipelines can auto-convert vs need manual work."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ado2gh.logging_config import console, log
from ado2gh.models import (
    PipelineComplexity, PipelineMetadata, PipelineType, RepoConfig,
)
from ado2gh.state.db import StateDB


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

    def __init__(self, db: StateDB):
        self.db = db

    def generate(self, repos: list[RepoConfig] = None,
                 output_path: str = None) -> dict:
        """Generate readiness report for all pipelines in inventory.

        Returns summary dict and optionally writes CSV + JSON reports.
        """
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
                    all_pipelines.append(meta)
                except Exception:
                    pass

        assessments = [self._assess_pipeline(p) for p in all_pipelines]

        summary = self._build_summary(assessments)

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

    def _assess_pipeline(self, pipe: PipelineMetadata) -> dict:
        """Assess a single pipeline's migration readiness."""
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
            "effort_hours": round(effort_hours, 1),
            "blockers": blockers,
            "warnings": warnings,
            "last_run": pipe.last_run_result,
            "avg_duration_min": pipe.avg_duration_min,
        }

    def _build_summary(self, assessments: list[dict]) -> dict:
        total = len(assessments)
        by_conversion = defaultdict(int)
        by_type = defaultdict(int)
        by_complexity = defaultdict(int)
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

    def _write_csv(self, assessments: list[dict], output_path: str):
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

    def print_summary(self, summary: dict):
        """Print readiness summary to console."""
        from rich.panel import Panel
        from rich.table import Table
        from rich import box

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
