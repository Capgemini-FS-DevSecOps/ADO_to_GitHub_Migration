"""Pipeline inventory builder — scans all ADO pipelines and stores in StateDB."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TimeElapsedColumn,
)

from ado2gh.clients.ado_client import ADOClient
from ado2gh.logging_config import console, log
from ado2gh.models import PipelineMetadata
from ado2gh.pipelines.extractor import PipelineMetadataExtractor
from ado2gh.state.db import StateDB


class PipelineInventoryBuilder:
    """
    Scans ALL pipelines in an ADO org (or specific projects) and stores
    complete PipelineMetadata in StateDB.pipeline_inventory.
    Handles 1000+ pipelines via pagination + parallel enrichment.
    """

    def __init__(self, ado: ADOClient, db: StateDB,
                 parallel: int = 12, dry_run: bool = False):
        self.ado       = ado
        self.db        = db
        self.parallel  = parallel
        self.dry_run   = dry_run
        self.extractor = PipelineMetadataExtractor()

    def build_for_projects(self, projects: list[str],
                           include_releases: bool = True) -> dict:
        """
        Scan all pipelines across given projects.
        Returns summary: {project: {build: N, release: N, total: N}}
        """
        summary: dict[str, dict] = {}
        all_var_groups: dict[str, list] = {}

        console.print(Panel(
            f"[bold]Pipeline Inventory Scan[/bold]\n"
            f"Projects: {len(projects)} | Parallel enrichment: {self.parallel}",
            border_style="blue",
        ))

        for project in projects:
            console.print(f"\n[cyan]Scanning project: {project}[/cyan]")
            # Pre-fetch variable groups once per project
            vgs = self.ado.list_variable_groups(project)
            all_var_groups[project] = vgs

            build_count   = self._scan_build_pipelines(project, vgs)
            release_count = 0
            if include_releases:
                release_count = self._scan_release_pipelines(project)

            summary[project] = {
                "build":   build_count,
                "release": release_count,
                "total":   build_count + release_count,
            }
            console.print(
                f"  [green]{project}[/green]: "
                f"{build_count} build + {release_count} release = "
                f"{build_count + release_count} pipelines indexed"
            )

        total = sum(s["total"] for s in summary.values())
        console.print(Panel(
            f"[bold green]Inventory complete[/bold green]\n"
            f"Total pipelines scanned: {total}\n"
            f"Stored in migration_state.db > pipeline_inventory",
            border_style="green",
        ))
        return summary

    def _scan_build_pipelines(self, project: str, var_groups: list[dict]) -> int:
        """Enumerate + enrich all build pipelines for a project in parallel."""
        # Step 1: collect pipeline stubs (fast, paginated)
        stubs = list(self.ado.list_all_pipelines(project))
        if not stubs:
            return 0

        console.print(f"  Found {len(stubs)} build pipelines — enriching metadata ...")

        count = 0
        with Progress(
            SpinnerColumn(),
            "[progress.description]{task.description}",
            MofNCompleteColumn(),
            BarColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(
                f"  Enriching [{project}]", total=len(stubs)
            )
            with ThreadPoolExecutor(max_workers=self.parallel) as pool:
                futures = {
                    pool.submit(
                        self._enrich_build_pipeline, project, stub, var_groups
                    ): stub
                    for stub in stubs
                }
                for future in as_completed(futures):
                    try:
                        meta = future.result(timeout=60)
                        if meta and not self.dry_run:
                            self.db.upsert_pipeline_inventory(meta)
                        count += 1
                    except Exception as e:
                        stub = futures[future]
                        log.warning(f"  Enrich failed [{project}/{stub['name']}]: {e}")
                    finally:
                        progress.advance(task)
        return count

    def _enrich_build_pipeline(self, project: str, stub: dict,
                               var_groups: list[dict]) -> Optional[PipelineMetadata]:
        """Fetch full definition + YAML + runs for one build pipeline."""
        pipe_id = stub["id"]
        config  = stub.get("configuration", {})
        is_yaml = config.get("type") == "yaml"

        if is_yaml:
            definition = self.ado.get_pipeline_definition(project, pipe_id)
            build_def  = self.ado.get_build_definition_full(project, pipe_id)
            repo       = definition.get("configuration", {}).get("repository", {})
            yaml_path  = definition.get("configuration", {}).get("path", "azure-pipelines.yml")
            yaml_content = self.ado.get_pipeline_yaml_from_git(
                project, repo.get("id", ""), yaml_path,
                branch=repo.get("defaultBranch", "main").replace("refs/heads/", ""),
            )
            runs = self.ado.get_pipeline_runs(project, pipe_id, top=10)
            return self.extractor.extract_yaml_pipeline(
                project, stub, definition, build_def, yaml_content, runs, var_groups
            )
        else:
            build_def = self.ado.get_build_definition_full(project, pipe_id)
            runs      = self.ado.get_pipeline_runs(project, pipe_id, top=10)
            return self.extractor.extract_classic_build_pipeline(
                project, stub, build_def, runs, var_groups
            )

    def _scan_release_pipelines(self, project: str) -> int:
        """Enumerate + enrich all classic release pipelines."""
        count = 0
        for rel_stub in self.ado.list_all_release_pipelines(project):
            try:
                rel_def = self.ado.get_release_definition(project, rel_stub["id"])
                meta    = self.extractor.extract_release_pipeline(project, rel_def or rel_stub)
                if not self.dry_run:
                    self.db.upsert_pipeline_inventory(meta)
                count += 1
            except Exception as e:
                log.warning(f"  Release pipeline enrich failed "
                            f"[{project}/{rel_stub.get('name')}]: {e}")
        return count
