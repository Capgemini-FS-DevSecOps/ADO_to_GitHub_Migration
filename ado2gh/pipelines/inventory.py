"""Pipeline inventory builder — scans all ADO pipelines and stores in StateDB."""
from __future__ import annotations

import difflib
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
from ado2gh.pipelines.repo_association import infer_pipeline_repo_name
from ado2gh.pipelines.resolve.template_resolver import (
    extract_template_refs,
    make_ado_git_fetcher,
    resolve_templates,
)
from ado2gh.pipelines.task_scanner import enrich_pipeline_readiness
from ado2gh.state.db import StateDB


def summarize_project_inventory(summary: dict[str, dict]) -> dict[str, int]:
    """Aggregate per-project inventory counts into org-level totals."""
    total = sum(int(v.get("total", 0)) for v in summary.values())
    build = sum(int(v.get("build", 0)) for v in summary.values())
    release = sum(int(v.get("release", 0)) for v in summary.values())
    return {
        "pipelines": total,
        "build": build,
        "release": release,
        "projects": len(summary),
    }


def _pick_best_yaml(configured_path: str, repo_name: str,
                    candidates: list[str], min_score: float = 0.4) -> str:
    """Pick the most likely intended YAML when the configured one is missing.

    Strategy:
      - Single candidate: just use it.
      - Multiple candidates: score each by max similarity between its
        basename and (a) the configured path's basename, (b) the repo
        name. Highest score wins, but only if it clears ``min_score`` so
        wildly-different files aren't auto-picked.
    """
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]

    cfg_base = configured_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    repo_norm = repo_name.lower().replace("-migration", "").replace("migration-", "")

    def _score(path: str) -> float:
        base = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        return max(
            difflib.SequenceMatcher(None, base, cfg_base).ratio(),
            difflib.SequenceMatcher(None, base, repo_norm).ratio(),
        )

    best = max(candidates, key=_score)
    return best if _score(best) >= min_score else ""


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
            project_scs = self.ado.list_service_connections(project)
            try:
                project_repos = self.ado.list_repos(project)
            except Exception:
                project_repos = []

            build_count   = self._scan_build_pipelines(
                project, vgs, project_scs, project_repos=project_repos,
            )
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

    def _scan_build_pipelines(
        self,
        project: str,
        var_groups: list[dict],
        project_scs: list[dict] | None = None,
        *,
        project_repos: list[dict] | None = None,
    ) -> int:
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
                        self._enrich_build_pipeline,
                        project,
                        stub,
                        var_groups,
                        project_scs or [],
                        project_repos or [],
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

    def _enrich_build_pipeline(
        self,
        project: str,
        stub: dict,
        var_groups: list[dict],
        project_scs: list[dict] | None = None,
        project_repos: list[dict] | None = None,
    ) -> Optional[PipelineMetadata]:
        """Fetch full definition + YAML + runs for one build pipeline."""
        pipe_id = stub["id"]
        config  = stub.get("configuration", {})
        is_yaml = config.get("type") == "yaml"

        # The Pipelines API can return an empty `configuration` block even
        # for YAML-driven pipelines (observed on classic-style pipelines that
        # reference an in-repo YAML file). Fall back to the Build Definitions
        # API's `process.type == 2` flag, which is the source of truth.
        build_def = self.ado.get_build_definition_full(project, pipe_id)
        process = build_def.get("process", {}) if build_def else {}
        yaml_path_from_build = ""
        if not is_yaml and process.get("type") == 2:
            is_yaml = True
            yaml_path_from_build = process.get("yamlFilename", "")

        if is_yaml:
            if yaml_path_from_build:
                # Source-of-truth was the Build Definitions API; synthesise the
                # definition shape that extract_yaml_pipeline expects.
                build_repo = build_def.get("repository", {})
                yaml_path = yaml_path_from_build
                branch = build_repo.get("defaultBranch", "main").replace(
                    "refs/heads/", "")
                definition = {
                    "configuration": {
                        "type": "yaml",
                        "repository": build_repo,
                        "path": yaml_path,
                    },
                }
                repo = build_repo
            else:
                definition = self.ado.get_pipeline_definition(project, pipe_id)
                repo = definition.get("configuration", {}).get("repository", {})
                yaml_path = definition.get("configuration", {}).get(
                    "path", "azure-pipelines.yml")
                branch = repo.get("defaultBranch", "main").replace(
                    "refs/heads/", "")
            yaml_content = self.ado.get_pipeline_yaml_from_git(
                project, repo.get("id", ""), yaml_path, branch=branch,
            )

            # Fallback: if the configured YAML is empty or missing in the
            # source repo, try to auto-pick the closest-named candidate so
            # the conversion still produces a usable workflow. Operators
            # always review the destination PR, and the migration_note below
            # makes the substitution explicit.
            fallback_used = ""
            other_candidates: list[str] = []
            if not (yaml_content or "").strip():
                candidates = self.ado.list_repo_yaml_files(
                    project, repo.get("id", ""), branch,
                )
                picked = _pick_best_yaml(
                    yaml_path, repo.get("name", ""), candidates,
                )
                if picked:
                    new_content = self.ado.get_pipeline_yaml_from_git(
                        project, repo.get("id", ""), picked, branch=branch,
                    )
                    if (new_content or "").strip():
                        yaml_content = new_content
                        fallback_used = picked
                        other_candidates = [c for c in candidates if c != picked]
                        # Update the synthesised definition so the metadata
                        # records which file actually drove the conversion.
                        if isinstance(definition, dict):
                            cfg = definition.setdefault("configuration", {})
                            cfg["path"] = picked

            if (yaml_content or "").strip() and extract_template_refs(yaml_content):
                fetcher = make_ado_git_fetcher(
                    self.ado,
                    project,
                    repo.get("id", ""),
                    branch,
                    yaml_path,
                )
                yaml_content = resolve_templates(
                    yaml_content, fetcher, source_path=yaml_path,
                )

            runs = self.ado.get_pipeline_runs(project, pipe_id, top=10)
            meta = self.extractor.extract_yaml_pipeline(
                project, stub, definition, build_def, yaml_content, runs, var_groups
            )
            if meta:
                enrich_pipeline_readiness(meta, project_scs or [], build_def=build_def)
                meta.complexity = self.extractor._score_complexity(meta)

            if meta and fallback_used:
                others = (", ".join(other_candidates)
                          if other_candidates else "(none)")
                meta.yaml_path = fallback_used
                meta.migration_notes.insert(0, (
                    f"Configured YAML '{yaml_path}' was missing in source "
                    f"repo '{repo.get('name', '?')}' on branch '{branch}'. "
                    f"Auto-selected '{fallback_used}' based on name "
                    f"similarity to the configured path and the repo name. "
                    f"Other candidates in repo: {others}. Review the "
                    f"generated workflow against the chosen source to "
                    f"confirm this matches the pipeline's intent."
                ))
                log.warning(
                    "  Pipeline %d (%s) configured YAML '%s' missing — "
                    "auto-selected '%s' (other candidates: %s)",
                    pipe_id, stub.get("name", "?"), yaml_path,
                    fallback_used, others,
                )
            elif meta and not (yaml_content or "").strip():
                # No candidates available (or all were also empty) — keep
                # the operator-facing diagnostic so they can fix the ADO
                # pipeline definition manually.
                candidates = self.ado.list_repo_yaml_files(
                    project, repo.get("id", ""), branch,
                )
                cand_str = ", ".join(candidates) if candidates else "(none found)"
                meta.migration_notes.insert(0, (
                    f"Configured YAML path '{yaml_path}' was empty or "
                    f"missing in source repo '{repo.get('name', '?')}' on "
                    f"branch '{branch}'. Available YAML files in repo: "
                    f"{cand_str}. Update the ADO pipeline definition to "
                    f"point at the correct file, or rename one of the "
                    f"available files to match, then re-run the migration."
                ))
                log.warning(
                    "  Pipeline %d (%s) configured YAML '%s' empty/missing "
                    "and no usable candidates found",
                    pipe_id, stub.get("name", "?"), yaml_path,
                )
            return self._apply_repo_association(meta, project_repos or [])
        else:
            runs = self.ado.get_pipeline_runs(project, pipe_id, top=10)
            meta = self.extractor.extract_classic_build_pipeline(
                project, stub, build_def, runs, var_groups
            )
            if meta:
                enrich_pipeline_readiness(meta, project_scs or [], build_def=build_def)
                meta.complexity = self.extractor._score_complexity(meta)
            return self._apply_repo_association(meta, project_repos or [])

    def _apply_repo_association(
        self,
        meta: PipelineMetadata | None,
        project_repos: list[dict],
    ) -> PipelineMetadata | None:
        if not meta or not project_repos:
            return meta
        inferred = infer_pipeline_repo_name(
            meta.pipeline_name,
            meta.repo_name,
            meta.repo_id,
            project_repos,
        )
        if inferred and inferred != meta.repo_name:
            if meta.repo_name:
                meta.migration_notes.append(
                    f"Repo association refined from '{meta.repo_name}' to '{inferred}'."
                )
            meta.repo_name = inferred
        return meta

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
