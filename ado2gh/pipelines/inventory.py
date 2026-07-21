"""Pipeline inventory builder — scans all ADO pipelines and stores in StateDB."""
from __future__ import annotations

import difflib
import inspect
import threading
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


def _pick_best_yaml(configured_path: str, repo_name: str,
                    candidates: list[str], min_score: float = 0.85,
                    min_margin: float = 0.15) -> str:
    """Pick the most likely intended YAML when the configured one is missing.

    Strategy:
      - Single candidate: just use it.
      - Multiple candidates: prefer an exact path/basename. Otherwise score by
        max similarity between its
        basename and (a) the configured path's basename, (b) the repo
        name. Highest score wins only if it clears ``min_score`` and is
        separated from the runner-up by ``min_margin``.
    """
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]

    configured_normalized = configured_path.lstrip("/").lower()
    exact_paths = [c for c in candidates if c.lstrip("/").lower() == configured_normalized]
    if len(exact_paths) == 1:
        return exact_paths[0]

    configured_basename = configured_normalized.rsplit("/", 1)[-1]
    exact_basenames = [
        c for c in candidates
        if c.lstrip("/").lower().rsplit("/", 1)[-1] == configured_basename
    ]
    if len(exact_basenames) == 1:
        return exact_basenames[0]

    cfg_base = configured_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    repo_norm = repo_name.lower().replace("-migration", "").replace("migration-", "")

    def _score(path: str) -> float:
        base = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        return max(
            difflib.SequenceMatcher(None, base, cfg_base).ratio(),
            difflib.SequenceMatcher(None, base, repo_norm).ratio(),
        )

    ranked = sorted(((_score(path), path) for path in candidates), reverse=True)
    best_score, best = ranked[0]
    runner_up_score = ranked[1][0]
    # Fuzzy substitution is allowed only for one strong, clearly separated
    # candidate. Ambiguous candidates must remain unresolved for operator/PEV
    # review rather than silently converting the wrong pipeline file.
    return (
        best
        if best_score >= min_score and best_score - runner_up_score >= min_margin
        else ""
    )


class PipelineInventoryError(RuntimeError):
    """Raised when strict inventory cannot produce a complete project scan."""


class PipelineInventoryBuilder:
    """
    Scans ALL pipelines in an ADO org (or specific projects) and stores
    complete PipelineMetadata in StateDB.pipeline_inventory.
    Handles 1000+ pipelines via pagination + parallel enrichment.
    """

    def __init__(self, ado: ADOClient, db: StateDB,
                 parallel: int = 12, dry_run: bool = False,
                 strict: bool = False):
        self.ado       = ado
        self.db        = db
        self.parallel  = parallel
        self.dry_run   = dry_run
        self.strict    = strict
        self.extractor = PipelineMetadataExtractor()
        self._failure_lock = threading.Lock()
        self._project_failures: dict[str, int] = {}
        self._project_unmapped: dict[str, int] = {}
        self._build_repo_cache: dict[tuple[str, str], Optional[dict]] = {}

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
            self._project_failures[project] = 0
            self._project_unmapped[project] = 0
            inventory_run_id = ""
            if not self.dry_run and hasattr(
                self.db, "start_pipeline_inventory_run"
            ):
                inventory_run_id = self.db.start_pipeline_inventory_run(
                    project,
                    include_releases=include_releases,
                    ruleset_version=PipelineMetadataExtractor.RULESET_VERSION,
                )
            if not self.dry_run and hasattr(self.db, "clear_inventory"):
                # A scan is a replacement snapshot. Keeping rows for deleted
                # source pipelines would make plan drift checks falsely pass.
                self.db.clear_inventory(project)
            console.print(f"\n[cyan]Scanning project: {project}[/cyan]")
            # Pre-fetch variable groups once per project
            vgs = self.ado.list_variable_groups(project)
            all_var_groups[project] = vgs
            list_connections = getattr(self.ado, "list_service_connections", None)
            try:
                service_connections = list_connections(project) if list_connections else []
            except Exception as exc:
                # Endpoint inventory is enrichment only; Build-read-only PATs
                # must still be able to inventory pipelines. Referenced endpoint
                # names/ids will be retained as unresolved mappings.
                log.warning(
                    "  Service connection inventory unavailable [%s]: %s",
                    project, exc,
                )
                service_connections = []

            build_count   = self._scan_build_pipelines(project, vgs, service_connections)
            release_count = 0
            if include_releases:
                release_count = self._scan_release_pipelines(project, service_connections)

            summary[project] = {
                "build":   build_count,
                "release": release_count,
                "total":   build_count + release_count,
                "failed":  self._project_failures.get(project, 0),
                "unmapped": self._project_unmapped.get(project, 0),
            }
            console.print(
                f"  [green]{project}[/green]: "
                f"{build_count} build + {release_count} release = "
                f"{build_count + release_count} pipelines indexed; "
                f"{self._project_failures.get(project, 0)} failed; "
                f"{self._project_unmapped.get(project, 0)} pipeline(s) unmapped"
            )
            has_gaps = bool(
                self._project_failures.get(project, 0)
                or self._project_unmapped.get(project, 0)
            )
            if inventory_run_id:
                self.db.finish_pipeline_inventory_run(
                    inventory_run_id,
                    status="partial" if has_gaps else "completed",
                    build_count=build_count,
                    release_count=release_count,
                    failed_count=self._project_failures.get(project, 0),
                    unmapped_count=self._project_unmapped.get(project, 0),
                )
            if self.strict and (
                has_gaps
            ):
                raise PipelineInventoryError(
                    f"Strict pipeline inventory failed for project '{project}': "
                    f"{self._project_failures[project]} pipeline(s) could not be enriched, "
                    f"{self._project_unmapped[project]} pipeline(s) could not be mapped"
                )

        total = sum(s["total"] for s in summary.values())
        console.print(Panel(
            f"[bold green]Inventory complete[/bold green]\n"
            f"Total pipelines scanned: {total}\n"
            f"Stored in migration_state.db > pipeline_inventory",
            border_style="green",
        ))
        return summary

    def _scan_build_pipelines(self, project: str, var_groups: list[dict],
                              service_connections: Optional[list[dict]] = None) -> int:
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
                        self._enrich_build_pipeline, project, stub, var_groups,
                        service_connections or [],
                    ): stub
                    for stub in stubs
                }
                for future in as_completed(futures):
                    try:
                        meta = future.result(timeout=60)
                        if meta:
                            if (
                                not meta.repo_name or not meta.repo_id
                                or meta.repo_type not in {"TfsGit", ""}
                            ):
                                self._record_unmapped(project)
                            if not self.dry_run:
                                self._persist_inventory(meta)
                            count += 1
                        else:
                            self._record_failure(project)
                    except Exception as e:
                        stub = futures[future]
                        log.warning(f"  Enrich failed [{project}/{stub['name']}]: {e}")
                        self._record_failure(project)
                    finally:
                        progress.advance(task)
        return count

    def _enrich_build_pipeline(self, project: str, stub: dict,
                               var_groups: list[dict],
                               service_connections: Optional[list[dict]] = None
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

            runs = self.ado.get_pipeline_runs(project, pipe_id, top=10)
            meta = self.extractor.extract_yaml_pipeline(
                project, stub, definition, build_def, yaml_content, runs, var_groups,
                service_connections or [],
            )

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
            return meta
        else:
            runs = self.ado.get_pipeline_runs(project, pipe_id, top=10)
            return self.extractor.extract_classic_build_pipeline(
                project, stub, build_def, runs, var_groups,
                service_connections or [],
            )

    def _scan_release_pipelines(self, project: str,
                                service_connections: Optional[list[dict]] = None) -> int:
        """Enumerate + enrich all classic release pipelines."""
        count = 0
        for rel_stub in self.ado.list_all_release_pipelines(project):
            try:
                rel_def = self.ado.get_release_definition(project, rel_stub["id"])
                release_definition = rel_def or rel_stub
                artifact_repositories = self._resolve_release_artifact_repositories(
                    project, release_definition,
                )
                meta    = self.extractor.extract_release_pipeline(
                    project, release_definition, service_connections or [],
                    artifact_repositories,
                )
                if not meta.repo_id and not meta.repo_name:
                    self._record_unmapped(project)
                if not self.dry_run:
                    self._persist_inventory(meta)
                count += 1
            except Exception as e:
                log.warning(f"  Release pipeline enrich failed "
                            f"[{project}/{rel_stub.get('name')}]: {e}")
                self._record_failure(project)
        return count

    def _record_failure(self, project: str) -> None:
        with self._failure_lock:
            self._project_failures[project] = self._project_failures.get(project, 0) + 1

    def _record_unmapped(self, project: str) -> None:
        with self._failure_lock:
            self._project_unmapped[project] = self._project_unmapped.get(project, 0) + 1

    def _resolve_release_artifact_repositories(
        self,
        project: str,
        release_definition: dict,
    ) -> dict[str, dict]:
        """Resolve Build artifact definition ids to their actual repositories."""
        result: dict[str, dict] = {}
        for artifact in release_definition.get("artifacts", []) or []:
            if not isinstance(artifact, dict) or artifact.get("type") != "Build":
                continue
            reference = artifact.get("definitionReference", {}) or {}
            definition = reference.get("definition", {}) or {}
            definition_id = str(
                definition.get("id", definition.get("value", "")) or ""
            )
            if not definition_id:
                continue
            cache_key = (project, definition_id)
            if cache_key not in self._build_repo_cache:
                try:
                    build_definition = self.ado.get_build_definition_full(
                        project, definition_id,
                    )
                    repository = (build_definition or {}).get("repository", {})
                    self._build_repo_cache[cache_key] = (
                        dict(repository) if repository.get("id") or repository.get("name")
                        else None
                    )
                except Exception as exc:
                    log.warning(
                        "  Release artifact definition resolution failed [%s/%s]: %s",
                        project, definition_id, exc,
                    )
                    self._build_repo_cache[cache_key] = None
            repository = self._build_repo_cache[cache_key]
            if repository:
                result[definition_id] = repository
        return result

    def _persist_inventory(self, meta: PipelineMetadata) -> None:
        """Use versioned StateDB API when present, retaining fake/legacy DB compatibility."""
        method = self.db.upsert_pipeline_inventory
        try:
            parameters = inspect.signature(method).parameters.values()
            supports_version = any(
                p.name == "ruleset_version" or p.kind == inspect.Parameter.VAR_KEYWORD
                for p in parameters
            )
        except (TypeError, ValueError):
            supports_version = False
        if supports_version:
            method(meta, ruleset_version=PipelineMetadataExtractor.RULESET_VERSION)
        else:
            method(meta)
