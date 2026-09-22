"""Pipeline step implementations as a mixin for PipelineRunner."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal, Protocol

from ado2gh.api.pipeline_models import PipelineRun, StepStatus
from ado2gh.api.pipeline_store import PipelineRunStore
from ado2gh.api.repo_input import load_repos
from ado2gh.api.repo_lock import REPO_LOCK_MANAGER, RepoLockedException

# A run's phase is operator-supplied free text (``PipelineRunStartRequest.phase:
# str``) while ``PhaseRunRequest.phase`` is a Literal of the five known phases,
# so an unknown value raised a pydantic ValidationError halfway through the
# migration step. This maps the one to the other; mypy checks these values
# against that Literal, so a change to the phase set fails here rather than at
# runtime.
_PHASE_REQUEST_VALUES: dict[str, Literal["poc", "pilot", "wave1", "wave2", "wave3"]] = {
    "poc": "poc",
    "pilot": "pilot",
    "wave1": "wave1",
    "wave2": "wave2",
    "wave3": "wave3",
}

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ado2gh.api.settings_store import SettingsStore
    from ado2gh.clients.ado_client import ADOClient
    from ado2gh.models import RepoConfig
    from ado2gh.state.factory import StateStore

    class _PipelineStepsHost(Protocol):
        """Attributes ``PipelineStepsMixin`` expects from ``PipelineRunner``."""

        settings: SettingsStore

        def _log(self, run: PipelineRun, msg: str) -> None: ...

        def _set_step(
            self,
            run: PipelineRun,
            step_id: str,
            status: StepStatus,
            message: str = "",
            result: dict | None = None,
        ) -> None: ...
else:
    _PipelineStepsHost = object


class PipelineStepsMixin(_PipelineStepsHost):
    """Step implementations mixed into :class:`~ado2gh.api.pipeline_runner.PipelineRunner`.

    Every step handler deliberately shares one signature —
    ``def _step_<name>(self, run: PipelineRun) -> None`` (FR-011). The
    uniformity is intentional and must be preserved: ``PipelineRunner._execute``
    dispatches through a single ``dict[str, Callable[[PipelineRun], None]]``
    handler table, so a step that took extra parameters or returned a value
    could not be dispatched. Steps therefore read everything they need from the
    run and the runner's settings, and report their outcome by calling
    ``_set_step`` and ``_log`` rather than by returning it.

    Scoped migration steps (``migrate``, ``migrate_repos``, ``convert_pipelines``,
    ``convert_metadata``) are the one exception in spirit: they are bound in the
    handler table as lambdas over :meth:`_migrate_scoped`, which keeps the
    dispatched callable itself conformant.
    """

    def _step_connect(self, run: PipelineRun) -> None:
        """Verify credentials and load discovery data for the run's phase.

        Builds ADO and GitHub clients from the merged configuration, proves both
        credentials work by listing ADO projects and reading GitHub rate limits,
        and confirms a GitHub target organisation is resolvable. When a profile
        is active, reuses its cached discovery scan if one exists (running a
        read-only ADO scan otherwise), syncs the scan into risk scores, and logs
        the repos queued for the run's phase.

        Args:
            run: The run to advance. Its ``connect`` step is marked completed
                with the project count and active profile id.

        Raises:
            Exception: Propagated from the ADO or GitHub client when a
                credential is missing or rejected, or when no GitHub
                organisation can be resolved, which fails the step.
        """
        from ado2gh.api.accelerator import _build_ado_client, _build_gh_client
        from ado2gh.api.profile_discovery import (
            ensure_profile_scan,
            require_gh_org,
            resolve_gh_org,
            sync_profile_scan_to_risk_scores,
        )
        from ado2gh.core.config_loader import ConfigLoader

        adv = self.settings.load().advanced
        global_cfg, _ = ConfigLoader.load(adv.config_path)
        profile = self.settings.get_active_profile()
        gh_org = resolve_gh_org(profile, global_cfg=global_cfg)
        if profile and gh_org:
            global_cfg = {**global_cfg, "gh_org": gh_org}

        ado = _build_ado_client(global_cfg)
        gh = _build_gh_client(global_cfg)
        projects = ado.list_projects()
        gh.token_manager.check_rate_limits()
        require_gh_org(profile, global_cfg=global_cfg)

        discovery_msg = ""
        if profile:
            from ado2gh.api.migration_scan import load_scan_results
            from ado2gh.api.profile_discovery import repo_configs_for_phase

            cached = load_scan_results(profile.id)
            if cached and cached.get("repos_scanned", 0) > 0:
                self._log(
                    run,
                    f"Using cached discovery scan ({cached.get('repos_scanned', 0)} repos, "
                    f"scanned {cached.get('scanned_at', 'unknown')})",
                )
            else:
                self._log(run, "No cached scan — running read-only ADO discovery scan")
            scan = ensure_profile_scan(profile, self.settings)
            synced = sync_profile_scan_to_risk_scores(
                profile.id, db_path=adv.db_path, config_path=adv.config_path,
            )
            phase_repos = repo_configs_for_phase(
                run.phase, profile, db_path=adv.db_path, config_path=adv.config_path,
            )

            discovery_msg = (
                f"; GitHub target: {gh_org}; Profile discovery: {scan.get('repos_scanned', len(phase_repos))} repos "
                f"({synced} synced); phase {run.phase}: {len(phase_repos)} queued"
            )
            for repo in phase_repos:
                self._log(
                    run,
                    f"  queued {repo.ado_project}/{repo.ado_repo} -> "
                    f"{repo.gh_org}/{repo.gh_repo} [scopes={','.join(repo.scopes or ['repo'])}]",
                )
        else:
            discovery_msg = "; No active profile — using migration.yaml only"

        self._set_step(
            run, "connect", StepStatus.COMPLETED,
            f"Discovery loaded: {len(projects)} projects; GitHub token OK{discovery_msg}",
            {"projects": len(projects), "profile_id": profile.id if profile else None},
        )

    def _step_analyze_deps(self, run: PipelineRun) -> None:
        """Resolve every dependency the run's repos carry and order them.

        Scans the in-scope ADO projects for service connections, variable
        groups, repo-to-repo references, environments, self-hosted agent pools
        and task inputs, then derives the order in which repos must be migrated
        so that upstream repos go first. Also folds in pipeline conversion
        readiness so the console can show blockers before anything is migrated.

        Args:
            run: The run to advance. A bare repo name in ``repository_id`` is
                expanded to ``"project/repo"`` here if the phase's repos allow
                it. The ``analyze_deps`` step is marked completed, or warned
                when dependencies need operator attention, and carries the
                dependency map, migration order and readiness summary in its
                result.
        """
        import os

        from ado2gh.api.accelerator import Accelerator
        from ado2gh.api.migration_work_plan import _pipeline_blockers_for_repo, _secrets_blockers
        from ado2gh.api.profile_discovery import repo_configs_for_phase
        from ado2gh.clients.ado_client import ADOClient
        from ado2gh.clients.ado_token_manager import ADOTokenManager
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.pipelines.inventory import summarize_project_inventory
        from ado2gh.reporting.pipeline_readiness import PipelineReadinessReport
        from ado2gh.state.factory import create_state_db

        adv = self.settings.load().advanced
        profile = self.settings.get_active_profile()
        db = create_state_db(adv.db_path)

        if run.repository_id and "/" not in run.repository_id and profile:
            try:
                from ado2gh.api.profile_discovery import repo_configs_for_phase
                phase_repos = repo_configs_for_phase(
                    run.phase, profile, db_path=adv.db_path, config_path=adv.config_path,
                )
                for r in phase_repos:
                    if r.ado_repo == run.repository_id:
                        run.repository_id = f"{r.ado_project}/{r.ado_repo}"
                        break
            except Exception:
                pass

        self._log(run, "Scanning ADO pipelines for service connections and dependencies…")
        global_cfg, _ = ConfigLoader.load(adv.config_path)
        ado_url = os.environ.get("ADO_ORG_URL") or global_cfg.get("ado_org_url", "")
        ado_pat = os.environ.get("ADO_PAT") or global_cfg.get("ado_pat", "")
        self._log(run, f"  ADO org: {ado_url or '(not set)'}; PAT: {'set' if ado_pat else '(not set)'}")

        if run.repository_id:
            scan_projects = [run.repository_id.split("/")[0]]
        elif profile:
            phase_repos = repo_configs_for_phase(
                run.phase, profile, db_path=adv.db_path, config_path=adv.config_path,
            )
            scan_projects = list({r.ado_project for r in phase_repos})
        else:
            try:
                ado = ADOClient(ado_url, token_manager=ADOTokenManager.from_single(ado_pat))
                scan_projects = [p["name"] for p in ado.list_projects()]
            except Exception:
                scan_projects = []

        self._log(run, f"  Projects to scan: {scan_projects}")

        pipeline_total = 0
        if scan_projects:
            try:
                accel = Accelerator(db_path=adv.db_path)
                stats = accel.inventory(adv.config_path, projects=scan_projects)
                project_summary = stats.get("projects", stats)
                totals = summarize_project_inventory(project_summary) if isinstance(project_summary, dict) else {}
                pipeline_total = stats.get("pipelines", totals.get("pipelines", 0))
                self._log(run, f"  Inventory scan complete: {pipeline_total} pipeline(s) across {len(scan_projects)} project(s)")
                if isinstance(project_summary, dict):
                    for proj, proj_stats in project_summary.items():
                        self._log(run, f"    {proj}: {proj_stats}")
                for proj in scan_projects:
                    all_pipes = db.get_all_inventory(project=proj)
                    for row in all_pipes:
                        meta_json = json.loads(row.get("metadata_json", "{}")) if isinstance(row.get("metadata_json"), str) else {}
                        self._log(run, f"    [diag] pipeline: {meta_json.get('pipeline_name', '?')} repo_name={meta_json.get('repo_name', '?')} repo_id={meta_json.get('repo_id', '?')} scs={len(meta_json.get('service_connections', []))} vgs={len(meta_json.get('variable_groups', []))}")
            except Exception as exc:
                self._log(run, f"  Inventory scan FAILED: {type(exc).__name__}: {exc}")
        else:
            self._log(run, "  No projects to scan — skipping inventory")

        def _check_repo_warnings(repo_id: str) -> tuple[list[str], dict[str, Any]]:
            parts = repo_id.split("/", 1)
            if len(parts) != 2:
                self._log(run, f"      [diag] cannot parse repo_id '{repo_id}' — expected Project/RepoName")
                return [], {}
            project, repo_name = parts
            pipelines = db.get_pipelines_for_repo(project, repo_name)
            self._log(run, f"      [diag] {repo_id}: found {len(pipelines)} pipeline(s) in inventory")
            for p in pipelines:
                scs = p.service_connections or []
                vgs = p.variable_groups or []
                yaml_len = len(p.yaml_content or "")
                self._log(run, f"      [diag]   pipeline={p.pipeline_name} type={p.pipeline_type} yaml={yaml_len} chars scs={len(scs)} vgs={len(vgs)}")
                if yaml_len and yaml_len < 2000:
                    self._log(run, f"      [diag]   yaml_content:\n{p.yaml_content}")
                if scs:
                    self._log(run, f"      [diag]     service_connections: {scs}")
                if vgs:
                    self._log(run, f"      [diag]     variable_groups: {vgs}")
            warnings: list[str] = []
            sec_blockers = _secrets_blockers(db, project, repo_name)
            pipe_blockers = _pipeline_blockers_for_repo(db, project, repo_name)
            for b in sec_blockers:
                warnings.append(f"⚠ {b}")
            for b in pipe_blockers:
                warnings.append(f"⚠ {b}")

            # Build structured dependency report per data-model.md
            dep_report: dict[str, Any] = {
                "service_connections": [],
                "variable_groups": [],
                "repo_dependencies": [],
                "environments": [],
                "unsupported_tasks": [],
                "self_hosted_agents": [],
            }
            for p in pipelines:
                if p.service_connections:
                    for sc in p.service_connections:
                        if isinstance(sc, dict):
                            dep_report["service_connections"].append({
                                "name": sc.get("name", sc),
                                "type": sc.get("type", "unknown"),
                                "id": sc.get("id", ""),
                                "status": "auto_provisionable" if sc.get("type") in ("azurerm", "kubernetes", "azurecr", "acr") else "operator_required"
                            })
                        else:
                            dep_report["service_connections"].append({
                                "name": str(sc),
                                "type": "unknown",
                                "id": "",
                                "status": "operator_required"
                            })
                if p.variable_groups:
                    for vg in p.variable_groups:
                        if isinstance(vg, dict):
                            dep_report["variable_groups"].append({
                                "name": vg.get("name", vg),
                                "status": "operator_required"
                            })
                        else:
                            dep_report["variable_groups"].append({
                                "name": str(vg),
                                "status": "operator_required"
                            })
            return warnings, dep_report

        def _readiness_summary() -> dict[str, int]:
            try:
                report = PipelineReadinessReport(db).generate(
                    migration_lookup=db.get_latest_pipeline_migrations(),
                    repo_migration_lookup=db.get_latest_repo_migrations()
                    if hasattr(db, "get_latest_repo_migrations") else {},
                )
                return {"auto": report.get("auto", 0), "assisted": report.get("assisted", 0), "manual": report.get("manual", 0)}
            except Exception:
                return {}

        if run.repository_id:
            self._log(run, f"Dependency analysis for {run.repository_id}")
            repo_warnings, repo_deps = _check_repo_warnings(run.repository_id)
            all_warnings = [f"{run.repository_id}: {w}" for w in repo_warnings]
            dependencies: dict[str, dict[str, Any]] = {run.repository_id: repo_deps}

            readiness = _readiness_summary()
            summary = f"Dependencies resolved for {run.repository_id}"
            if all_warnings:
                summary += f" — {len(all_warnings)} warning(s):\n" + self._format_warnings(all_warnings)
            result_data: dict[str, Any] = {
                "migration_order": [run.repository_id],
                "dependency_count": 0,
                "pipelines_scanned": pipeline_total,
                "dependencies": dependencies,
            }
            if all_warnings:
                result_data["warnings"] = all_warnings
            if readiness:
                result_data["readiness"] = readiness
            self._set_step(run, "analyze_deps", StepStatus.WARN if all_warnings else StepStatus.COMPLETED, summary, result_data)
        else:
            if profile:
                repo_ids = [f"{r.ado_project}/{r.ado_repo}" for r in phase_repos]
            else:
                repo_ids = []

            all_warnings = []
            dependencies = {}
            self._log(run, f"Dependency analysis: {len(repo_ids)} repo(s) in phase {run.phase}")
            for rid in repo_ids:
                repo_warnings, repo_deps = _check_repo_warnings(rid)
                for w in repo_warnings:
                    all_warnings.append(f"{rid}: {w}")
                dependencies[rid] = repo_deps

            readiness = _readiness_summary()
            summary = f"Dependencies analyzed: {len(repo_ids)} repo(s)"
            if all_warnings:
                summary += f" — {len(all_warnings)} warning(s):\n" + self._format_warnings(all_warnings)
            result_data = {
                "repo_count": len(repo_ids),
                "total_dependencies": 0,
                "pipelines_scanned": pipeline_total,
                "dependencies": dependencies,
                "repo_dependencies": {rid: [rid] for rid in repo_ids},
            }
            if all_warnings:
                result_data["warnings"] = all_warnings
            if readiness:
                result_data["readiness"] = readiness
            self._set_step(run, "analyze_deps", StepStatus.WARN if all_warnings else StepStatus.COMPLETED, summary, result_data)

    def _step_discover(self, run: PipelineRun) -> None:
        """Scan the ADO organisation and write a discovery file to disk.

        Delegates to the accelerator's discovery pass, which enumerates projects
        and repositories and writes the result under the configured output
        directory for later phases to consume.

        Args:
            run: The run to advance. Its ``discover`` step is marked completed
                with the output directory that was written.
        """
        from ado2gh.api.accelerator import Accelerator
        from ado2gh.api.contracts import DiscoverRequest

        adv = self.settings.load().advanced
        accel = Accelerator(db_path=adv.db_path)
        result = accel.discover(DiscoverRequest(config_path=adv.config_path))
        self._set_step(run, "discover", StepStatus.COMPLETED,
                       f"Discovery written to {result.output_dir}",
                       {"output_dir": result.output_dir})

    def _step_inventory(self, run: PipelineRun) -> None:
        """Deep-scan ADO pipeline definitions into the state database.

        Inventories YAML, classic and release pipeline definitions across every
        project, then, when a profile is active, reports how many of the
        inventoried pipelines belong to repos in the run's phase so the operator
        can see the phase-relevant subset rather than the org-wide total.

        Args:
            run: The run to advance. Its ``inventory`` step is marked completed
                with the pipeline totals as its result.
        """
        import os

        from ado2gh.api.accelerator import Accelerator
        from ado2gh.api.profile_discovery import repo_configs_for_phase
        from ado2gh.clients.ado_client import ADOClient
        from ado2gh.clients.ado_token_manager import ADOTokenManager
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.pipelines.inventory import summarize_project_inventory
        from ado2gh.state.factory import create_state_db

        adv = self.settings.load().advanced
        global_cfg, _ = ConfigLoader.load(adv.config_path)
        ado_url = os.environ.get("ADO_ORG_URL") or global_cfg.get("ado_org_url", "")
        ado_pat = os.environ.get("ADO_PAT") or global_cfg.get("ado_pat", "")
        ado = ADOClient(ado_url, token_manager=ADOTokenManager.from_single(ado_pat))
        projects = [p["name"] for p in ado.list_projects()]
        accel = Accelerator(db_path=adv.db_path)
        stats = accel.inventory(adv.config_path, projects=projects)
        project_summary = stats.get("projects", stats)
        totals = summarize_project_inventory(project_summary) if isinstance(project_summary, dict) else {}
        pipeline_total = stats.get("pipelines", totals.get("pipelines", 0))

        phase_linked = None
        profile = self.settings.get_active_profile()
        if profile:
            try:
                db = create_state_db(adv.db_path)
                phase_repos = repo_configs_for_phase(
                    run.phase, profile, db_path=adv.db_path, config_path=adv.config_path,
                )
                if phase_repos:
                    phase_linked = sum(
                        db.inventory_count_for_repo(r.ado_project, r.ado_repo)
                        for r in phase_repos
                    )
            except Exception:
                phase_linked = None

        message = f"Inventory: {pipeline_total} pipelines"
        if phase_linked is not None:
            message += f" ({phase_linked} linked to phase {run.phase})"
        self._set_step(run, "inventory", StepStatus.COMPLETED, message, stats)

    def _step_readiness(self, run: PipelineRun) -> None:
        """Classify inventoried pipelines by how much manual work conversion needs.

        Runs the readiness report over the inventory already in the state
        database, taking prior pipeline and repo migration results into account
        so pipelines that have already moved are not re-flagged.

        Args:
            run: The run to advance. Its ``readiness`` step is marked completed
                with the auto, assisted and manual counts as its result.
        """
        from ado2gh.reporting.pipeline_readiness import PipelineReadinessReport
        from ado2gh.state.factory import create_state_db

        adv = self.settings.load().advanced
        db = create_state_db(adv.db_path)
        report = PipelineReadinessReport(db).generate(
            migration_lookup=db.get_latest_pipeline_migrations(),
            repo_migration_lookup=db.get_latest_repo_migrations()
            if hasattr(db, "get_latest_repo_migrations") else {},
        )
        self._set_step(run, "readiness", StepStatus.COMPLETED,
                       f"Auto: {report.get('auto', 0)} | Assisted: {report.get('assisted', 0)} | Manual: {report.get('manual', 0)}",
                       {"auto": report.get("auto"), "assisted": report.get("assisted"), "manual": report.get("manual")})

    def _step_assign(self, run: PipelineRun) -> None:
        """Confirm a phase or wave assignment exists for the run to execute.

        Prefers an existing ``*_phase.yaml`` next to the configured migration
        config; failing that, accepts waves declared in the config itself. This
        step never generates an assignment — that is the CLI's ``phase assign``
        command — so when neither source is present it is skipped with guidance
        rather than failed.

        Args:
            run: The run to advance. Its ``assign`` step is marked completed
                with the assignment source, or skipped when none exists.
        """
        from pathlib import Path

        from ado2gh.core.config_loader import ConfigLoader

        adv = self.settings.load().advanced
        phase_path = Path(adv.config_path.replace(".yaml", "_phase.yaml"))
        if phase_path.exists():
            self._set_step(run, "assign", StepStatus.COMPLETED,
                           f"Using existing {phase_path.name}",
                           {"output": str(phase_path)})
            return
        _, waves = ConfigLoader.load(adv.config_path)
        if waves:
            self._set_step(run, "assign", StepStatus.COMPLETED,
                           f"Using {len(waves)} wave(s) from {adv.config_path}",
                           {"waves": len(waves)})
        else:
            self._set_step(run, "assign", StepStatus.SKIPPED,
                           "No waves in config — run ado2gh phase assign or edit migration.yaml",
                           {})

    def _dep_note(self, run: PipelineRun, repos: "list[RepoConfig]") -> str:
        """Summarise how many extra repositories a single-repository run pulled in.

        Args:
            run: The run being reported on. Multi-repo runs produce no note,
                since the dependency count is only meaningful relative to one
                named target repo.
            repos: The repos the run will actually migrate, including the target
                itself.

        Returns:
            str: A short parenthesised fragment to append to a step message —
            the number of dependencies dragged in alongside the target, or a
            note that it has none. Empty for a run that is not scoped to a
            single repo.
        """
        if not run.repository_id:
            return ""
        dep_count = max(0, len(repos) - 1)
        return f" ({dep_count} dependencies)" if dep_count else " (no repo dependencies)"

    def _repo_migration_dry_run_warnings(
        self,
        run: PipelineRun,
        repos: "list[RepoConfig]",
        *,
        ado: "ADOClient | None" = None,
        db: "StateStore | None" = None,
    ) -> list[str]:
        """Collect the dry-run notes that apply to moving repository content.

        Covers only what affects the git transfer itself: upstream repos that
        must migrate first, repository size and the strategy it implies (mirror,
        the GitHub Enterprise Importer (GEI) or manual), branch counts that will stretch the run, branch policies
        that need their own scope, and per-project metadata such as wikis and
        work items. Pipeline secret mapping is deliberately not reported here —
        that belongs to :meth:`_pipeline_conversion_dry_run_warnings`.

        Args:
            run: The run being previewed. Its ``analyze_deps`` result supplies
                the migration order used for the upstream-dependency note.
            repos: The repos the run would migrate.
            ado: ADO client used to read repository size, statistics, branch
                policies and project metadata. When omitted, the size and
                policy notes are skipped rather than failing.
            db: State database consulted for pipelines already inventoried
                against each repo. When omitted, those notes are skipped.

        Returns:
            list[str]: Human-readable notes, one per finding, in the order they
            were discovered. Empty when nothing needs the operator's attention.
            Each entry is prefixed with the ``"project/repo"`` it concerns
            except the leading migration-order note.
        """
        warnings: list[str] = []
        target = str(run.repository_id or "").strip()
        analyze_step = next((s for s in run.steps if s.id == "analyze_deps"), None)
        migration_order: list[str] = []
        if analyze_step and analyze_step.result:
            migration_order = list(analyze_step.result.get("migration_order") or [])

        if target:
            upstream = [repo for repo in migration_order if repo != target]
            if upstream:
                warnings.append(
                    f"Upstream repos to migrate first (dependency order): {', '.join(upstream)}"
                )
            else:
                warnings.append("No upstream repo dependencies in migration order")

        projects_seen: set[str] = set()
        for repo in repos:
            rid = f"{repo.ado_project}/{repo.ado_repo}"
            project, repo_name = repo.ado_project, repo.ado_repo

            source: dict[str, Any] | None = None
            if ado:
                try:
                    source = ado.get_repo(project, repo_name)
                    size_kb = int(source.get("size") or 0)
                    size_mb = size_kb / 1024
                    size_gb = size_mb / 1024
                    if size_gb >= 10:
                        warnings.append(
                            f"{rid}: Repository size {size_gb:.2f} GB — manual migration required"
                        )
                    elif size_gb >= 2:
                        warnings.append(
                            f"{rid}: Repository size {size_gb:.2f} GB — GEI recommended"
                        )
                    elif size_gb >= 0.5:
                        warnings.append(f"{rid}: Repository size {size_gb:.2f} GB — large repo")
                    elif size_mb >= 1:
                        warnings.append(f"{rid}: Repository size {size_mb:.1f} MB")
                    else:
                        warnings.append(f"{rid}: Repository size {size_kb} KB")

                    repo_uuid = source.get("id")
                    if repo_uuid:
                        stats = ado.get_repo_stats(project, repo_uuid)
                        branches = int(stats.get("branch_count") or 0)
                        if branches > 500:
                            warnings.append(
                                f"{rid}: {branches} branches — expect longer mirror/GEI runtime"
                            )
                        try:
                            policies = ado.list_branch_policies(project, repo_uuid)
                            if policies:
                                warnings.append(
                                    f"{rid}: {len(policies)} branch polic(ies) — "
                                    "migrate via branch_policies scope"
                                )
                        except Exception:
                            pass
                except Exception as exc:
                    warnings.append(f"{rid}: Could not read repository stats ({exc})")

            if db:
                try:
                    pipelines = db.get_pipelines_for_repo(project, repo_name)
                    tasks: set[str] = set()
                    for pipe in pipelines:
                        for task in pipe.unsupported_tasks or []:
                            if task:
                                tasks.add(str(task))
                    if tasks:
                        shown = sorted(tasks)[:5]
                        extra = f" (+{len(tasks) - 5} more)" if len(tasks) > 5 else ""
                        warnings.append(
                            f"{rid}: ADO pipeline tasks/extensions needing GitHub Actions equivalents: "
                            f"{', '.join(shown)}{extra}"
                        )
                except Exception:
                    pass

            if ado and project not in projects_seen:
                projects_seen.add(project)
                try:
                    wiki_pages = ado.list_wiki_pages(project)
                    if wiki_pages:
                        warnings.append(
                            f"{project}: Wiki detected ({len(wiki_pages)} wiki root(s)) — "
                            "migrate via convert_metadata / wiki scope"
                        )
                except Exception:
                    pass
                try:
                    feeds = ado.list_artifacts(project)
                    if feeds:
                        warnings.append(
                            f"{project}: Artifact feeds detected ({len(feeds)}) — "
                            "plan GitHub Packages or external feed migration"
                        )
                except Exception:
                    pass
                try:
                    work_items = ado.list_work_items(project, top=1)
                    if work_items:
                        warnings.append(
                            f"{project}: Work items present — migrate via work_items / boards scope"
                        )
                except Exception:
                    pass

        return warnings

    def _pipeline_conversion_dry_run_warnings(
        self,
        run: PipelineRun,
        repos: "list[RepoConfig]",
    ) -> list[str]:
        """Collect the dry-run notes that apply to converting pipelines.

        Reports the credential-bearing dependencies that a converted workflow
        will need on the GitHub side: each service connection the repo's
        pipelines use, which must become a GitHub secret or an OpenID Connect (OIDC) login, and
        each variable group, which must become Actions secrets or variables.
        These belong to pipeline conversion rather than to the repo content
        migration step, so they are reported separately.

        Args:
            run: The run being previewed. Its ``analyze_deps`` result supplies
                the dependency map; without one there is nothing to report.
                A run scoped to a single repo reports only that repo.
            repos: The repos the run would convert, used when the run is not
                scoped to a single repository.

        Returns:
            list[str]: One note per service connection and variable group,
            grouped by ``"project/repo"`` in sorted order, naming the dependency
            and what must be created for it on GitHub. Never contains a
            credential value, only the dependency's name.
        """
        warnings: list[str] = []
        analyze_step = next((s for s in run.steps if s.id == "analyze_deps"), None)
        if not analyze_step or not analyze_step.result:
            return warnings
        dependencies = analyze_step.result.get("dependencies") or {}
        targets = {str(run.repository_id)} if run.repository_id else {
            f"{r.ado_project}/{r.ado_repo}" for r in repos
        }
        for rid in sorted(targets):
            dep = dependencies.get(rid) or {}
            for sc in dep.get("service_connections") or []:
                name = sc.get("name") if isinstance(sc, dict) else str(sc)
                if name:
                    warnings.append(
                        f"{rid}: Service connection '{name}' — create matching GitHub secret or OIDC login"
                    )
            for vg in dep.get("variable_groups") or []:
                name = vg.get("name") if isinstance(vg, dict) else str(vg)
                if name:
                    warnings.append(
                        f"{rid}: Variable group '{name}' — map to GitHub Actions secrets/variables"
                    )
        return warnings

    @staticmethod
    def _format_warnings(warnings: list[str]) -> str:
        if not warnings:
            return ""
        page = warnings[:10]
        lines = [f"  • {w}" for w in page]
        if len(warnings) > 10:
            lines.append(f"  … and {len(warnings) - 10} more (see warnings in result data)")
        return "\n".join(lines)

    def _migrate_scoped(
        self, run: PipelineRun, step_id: str, scope_filter: list[str] | None,
    ) -> None:
        """Execute one migration step over a restricted set of migration scopes.

        Backs every scoped step in the pipeline — the full ``migrate`` run as
        well as the narrower ``migrate_repos``, ``convert_pipelines`` and
        ``convert_metadata`` steps — by building a wave for the run's phase (or
        a single-repo wave when the run names one), then executing it through
        the batch executor with each repo's scopes temporarily narrowed to
        ``scope_filter``. Repos are locked for the duration so two runs cannot
        migrate the same repo concurrently, and the original scopes are restored
        afterwards.

        In dry-run mode no migration is performed: the step reports the notes
        gathered by the per-step dry-run warning helpers instead. In live mode
        the operator's escalation justification is redacted before the phase
        request is built, so it is never persisted or logged verbatim.

        Args:
            run: The run to advance.
            step_id: Id of the step being executed, used to select the dry-run
                warnings to report and to mark the right step on the run.
            scope_filter: Migration scopes this step is allowed to touch, or
                ``None`` to allow every scope.

        Raises:
            RuntimeError: If the underlying migration reports a failure the step
                cannot continue past.
        """
        from ado2gh.api.accelerator import Accelerator, _build_ado_client, _build_gh_client
        from ado2gh.api.contracts import (
            PhaseRunRequest,
            PhaseRunResult,
            RunWaveRequest,
            RunWaveResult,
        )
        from ado2gh.api.migration_work_plan import (
            apply_scope_results_to_work_items,
            build_work_items_for_repos,
            filter_work_items_for_scopes,
        )
        from ado2gh.api.profile_discovery import build_wave_from_profile_phase, require_gh_org
        from ado2gh.api.validation_run import _merge_profile_credentials
        from ado2gh.audit import redact_payload
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.core.migration_engine import MigrationEngine
        from ado2gh.models import ExecutionMode, MigrationScope
        from ado2gh.phase.batch_executor import BatchExecutor
        from ado2gh.phase.progress_tracker import ProgressTracker
        from ado2gh.state.factory import create_state_db

        adv = self.settings.load().advanced
        profile = self.settings.get_active_profile()
        cancel_event = PipelineRunStore.cancel_event(run.id)
        step_label = next((s.label for s in run.steps if s.id == step_id), step_id)

        # For single-repository runs, create a minimal wave for dry-run validation
        if run.repository_id and run.dry_run:
            from ado2gh.models import RepoConfig, WaveConfig
            project, repo_name = run.repository_id.split("/", 1)
            gh_org = profile.gh_org if profile else "ado-to-gh-migration"
            wave: WaveConfig | None = WaveConfig(
                wave_id=9000,
                name=f"Dry-run validation for {run.repository_id}",
                description=f"Single-repo dry-run validation for {run.repository_id}",
                repos=[RepoConfig(
                    ado_project=project,
                    ado_repo=repo_name,
                    gh_org=gh_org,
                    gh_repo=repo_name,
                    phase="",
                    risk_score=0,
                    scopes=["repo"],
                )],
                parallel=adv.repo_parallel,
                pipeline_parallel=adv.pipeline_parallel,
            )
            self._log(run, f"{step_label}: Single-repo dry-run validation for {run.repository_id}")
        elif profile:
            gh_org = require_gh_org(profile, config_path=adv.config_path)
            wave = build_wave_from_profile_phase(
                run.phase,
                profile,
                wave_id=run.wave_id or 9000,
                db_path=adv.db_path,
                config_path=adv.config_path,
            )
            if wave:
                wave.parallel = adv.repo_parallel
                wave.pipeline_parallel = adv.pipeline_parallel
            self._log(run, f"{step_label}: Profile active, wave has {len(wave.repos) if wave else 0} repo(s)")
        else:
            wave = None

        if wave and wave.repos:
                if run.repository_id:
                    # Read migration order from analyze_deps step result; default to the target repository alone
                    analyze_step = next((s for s in run.steps if s.id == "analyze_deps"), None)
                    migration_order = [run.repository_id]
                    if analyze_step and analyze_step.result:
                        stored = (
                            analyze_step.result.get("migration_order")
                            or analyze_step.result.get("repo_dependencies", {}).get(run.repository_id)
                        )
                        if isinstance(stored, list) and stored:
                            migration_order = stored
                    if run.migrate_deps_only:
                        target_repos = set(migration_order)
                    else:
                        target_repos = {run.repository_id}
                    wave.repos = [
                        r for r in wave.repos
                        if r.ado_repo in target_repos
                        or f"{r.ado_project}/{r.ado_repo}" in target_repos
                    ]
                    self._log(
                        run,
                        f"{step_label}: filtered to {len(wave.repos)} repo(s) for {run.repository_id}"
                        f" (deps: {run.migrate_deps_only})",
                    )
                    for repo in wave.repos:
                        is_target = f"{repo.ado_project}/{repo.ado_repo}" == run.repository_id
                        self._log(
                            run,
                            f"  {'→ target: ' if is_target else '  dependency: '}"
                            f"{repo.ado_project}/{repo.ado_repo} -> {repo.gh_org}/{repo.gh_repo}",
                        )

                if run.dry_run:
                    global_cfg, _ = ConfigLoader.load(adv.config_path)
                    # Unconditional: the single-repository branch above builds a wave even
                    # with no profile, so this is the one path that can reach the
                    # merge with none. Skipping it would run the connectivity probes
                    # below against credential-less configuration and let the step report
                    # COMPLETED on credentials nothing ever checked (CA-001), so
                    # _merge_profile_credentials raises on a missing profile and the
                    # runner turns that into a FAILED step.
                    global_cfg = _merge_profile_credentials(global_cfg, profile, adv)
                    global_cfg["gh_org"] = gh_org
                    validation_errors: list[str] = []
                    try:
                        ado = _build_ado_client(global_cfg)
                        ado.list_projects()
                    except Exception as exc:
                        validation_errors.append(f"ADO connectivity: {exc}")
                    try:
                        gh = _build_gh_client(global_cfg)
                        gh.token_manager.check_rate_limits()
                    except Exception as exc:
                        validation_errors.append(f"GitHub connectivity: {exc}")

                    scope_note = f" scopes={','.join(scope_filter)}" if scope_filter else ""
                    dep_note = self._dep_note(run, wave.repos)
                    if validation_errors:
                        msg = f"{step_label}: DRY RUN validation failed ({len(wave.repos)} repo(s)){dep_note}{scope_note}"
                        for err in validation_errors:
                            msg += f"\n  - {err}"
                            self._log(run, f"  FAIL: {err}")
                        self._set_step(
                            run, step_id, StepStatus.FAILED, msg,
                            {"dry_run": True, "repos": len(wave.repos), "errors": validation_errors},
                        )
                        return

                    self._log(
                        run,
                        f"{step_label}: DRY RUN — credentials validated, {len(wave.repos)} repo(s) ready{dep_note}{scope_note}",
                    )
                    ado_client = None
                    try:
                        ado_client = _build_ado_client(global_cfg)
                    except Exception:
                        pass
                    db = create_state_db(adv.db_path)
                    step_warnings: list[str] = []
                    if step_id == "migrate_repos":
                        step_warnings = self._repo_migration_dry_run_warnings(
                            run, wave.repos, ado=ado_client, db=db,
                        )
                    elif step_id == "convert_pipelines":
                        step_warnings = self._pipeline_conversion_dry_run_warnings(
                            run, wave.repos,
                        )

                    msg = (
                        f"Dry run: {len(wave.repos)} repo(s) validated{dep_note} — "
                        f"{step_label.lower()}"
                    )
                    if step_warnings:
                        msg += f" — {len(step_warnings)} note(s):\n" + self._format_warnings(step_warnings)

                    result_data: dict[str, Any] = {
                        "dry_run": True,
                        "repos": len(wave.repos),
                        "validated": True,
                    }
                    if step_warnings:
                        result_data["warnings"] = step_warnings

                    self._set_step(
                        run,
                        step_id,
                        StepStatus.WARN if step_warnings else StepStatus.COMPLETED,
                        msg,
                        result_data,
                    )
                    return

                mode = "LIVE"
                scope_note = f" scopes={','.join(scope_filter)}" if scope_filter else ""
                dep_note = self._dep_note(run, wave.repos)
                self._log(
                    run,
                    f"{step_label}: {len(wave.repos)} repo(s) phase {run.phase} [{mode}]{dep_note}{scope_note}",
                )
                global_cfg, _ = ConfigLoader.load(adv.config_path)
                # wave is only non-empty here via the profile-driven branch above, so
                # profile is expected to be set; _merge_profile_credentials enforces
                # that rather than an assert, which `python -O` would strip and leave
                # this live path merging nothing (CA-001).
                global_cfg = _merge_profile_credentials(global_cfg, profile, adv)
                global_cfg["gh_org"] = gh_org
                ado = _build_ado_client(global_cfg)
                gh = _build_gh_client(global_cfg)
                db = create_state_db(adv.db_path)
                if not run.dry_run:
                    from ado2gh.core.conflict_detection import clear_stale_in_progress_migrations

                    for repo in wave.repos:
                        clear_stale_in_progress_migrations(
                            db,
                            repo.ado_project,
                            repo.ado_repo,
                            current_run_id=run.id,
                        )
                engine = MigrationEngine(
                    global_cfg,
                    ado,
                    gh,
                    db,
                    mode=ExecutionMode.from_dry_run(dry_run=run.dry_run),
                    pipeline_run_id=run.id,
                )
                executor = BatchExecutor(
                    engine, db,
                    ProgressTracker(total_repos=len(wave.repos), total_pipelines=1),
                )

                from ado2gh.api.run_reporting import migrate_repo_detail

                enabled_scopes = (
                    scope_filter
                    if scope_filter is not None
                    else [s.value for s in MigrationScope]
                )
                pre_work_items = build_work_items_for_repos(
                    wave.repos,
                    enabled_scopes=enabled_scopes,
                    db=db,
                )

                for repo in wave.repos:
                    rid = f"{repo.ado_project}/{repo.ado_repo}"
                    try:
                        REPO_LOCK_MANAGER.acquire(rid, run.id)
                    except RepoLockedException as exc:
                        self._log(run, f"  BLOCKED: {exc}")
                        self._set_step(
                            run, step_id, StepStatus.FAILED, str(exc),
                            {
                                "repo": rid,
                                "error": "migration_in_progress",
                                "holder_run_id": exc.holder_run_id,
                            },
                        )
                        return

                saved_scopes = []
                for repo in wave.repos:
                    saved_scopes.append(list(repo.scopes or ["repo"]))
                    if scope_filter is not None:
                        repo.scopes = list(scope_filter)

                def on_repo_done(
                    key: str, res: dict[str, Any], _repo_cfg: "RepoConfig",
                ) -> None:
                    """Log a one-line summary as each repository finishes.

                    Signature is fixed by ``BatchExecutor.execute_wave``, which
                    calls it positionally as ``Callable[[str, dict, RepoConfig],
                    None]``, so the third argument cannot be dropped. It carries
                    the leading underscore of the standard dummy-argument
                    convention because this callback reads nothing from it.

                    Args:
                        key: ``"project/repo"`` of the repo that finished.
                        res: That repo's result dictionary from the executor.
                        _repo_cfg: The repo's configuration, accepted to match
                            the executor's callback signature and not read here.
                    """
                    detail = migrate_repo_detail(key, res)
                    self._log(run, detail["summary"])

                try:
                    result = executor.execute_wave(
                        wave,
                        mode=ExecutionMode.from_dry_run(dry_run=run.dry_run),
                        cancel_event=cancel_event,
                        on_repo_done=on_repo_done,
                    )
                finally:
                    for repo, scopes in zip(wave.repos, saved_scopes):
                        repo.scopes = scopes

                repo_details = [
                    migrate_repo_detail(k, v) for k, v in result.get("repos", {}).items()
                ]
                result["repo_details"] = repo_details
                work_items = apply_scope_results_to_work_items(
                    pre_work_items, repo_details,
                )
                result["work_items"] = filter_work_items_for_scopes(
                    work_items, scope_filter,
                )
                if cancel_event.is_set():
                    self._set_step(run, step_id, StepStatus.SKIPPED,
                                   "Cancelled by user", result)
                    return
                failed_names = [d["repo"] for d in repo_details if d["status"] != "completed"]
                msg = f"{step_label}: {result['completed']} completed, {result['failed']} failed"
                if failed_names:
                    shown = ", ".join(failed_names[:5])
                    extra = f" (+{len(failed_names) - 5} more)" if len(failed_names) > 5 else ""
                    msg += f" — failed: {shown}{extra}"

                # Check feasibility reports for warnings
                feasibility_warnings = []
                for detail in repo_details:
                    feas = detail.get("stats", {}).get("feasibility_report", {})
                    if feas:
                        status = feas.get("status", "")
                        if status in ("warn", "fail_soft"):
                            warnings = feas.get("warnings", [])
                            for w in warnings:
                                feasibility_warnings.append(f"{detail['repo']}: {w}")

                if feasibility_warnings:
                    shown_warnings = feasibility_warnings[:3]
                    extra = f" (+{len(feasibility_warnings) - 3} more)" if len(feasibility_warnings) > 3 else ""
                    msg += f"\nFeasibility warnings: {', '.join(shown_warnings)}{extra}"

                # Continue-on-error: WARN (not FAILED) when some repos fail or feasibility warnings exist in non-dry-run
                has_failures = not run.dry_run and result.get("failed", 0) > 0
                has_feasibility_warnings = bool(feasibility_warnings)
                migrate_status = (
                    StepStatus.WARN
                    if has_failures or has_feasibility_warnings
                    else StepStatus.COMPLETED
                )
                self._set_step(run, step_id, migrate_status, msg, result)
                return

        accel = Accelerator(db_path=adv.db_path)
        if run.dry_run:
            if profile:
                self._log(run, f"{step_label}: DRY RUN — active profile but no repos to migrate")
                self._set_step(
                    run, step_id, StepStatus.SKIPPED,
                    "Dry run: skipped (no repos in profile to validate)",
                    {"dry_run": True, "skipped_reason": "no_repos"},
                )
            else:
                self._log(run, f"{step_label}: DRY RUN — no active profile, validating config only")
                self._set_step(
                    run, step_id, StepStatus.SKIPPED,
                    "Dry run: skipped (no active profile to validate against)",
                    {"dry_run": True, "skipped_reason": "no_profile"},
                )
            return
        outcome: RunWaveResult | PhaseRunResult
        if run.wave_id is not None:
            outcome = accel.run_wave(RunWaveRequest(
                config_path=adv.config_path,
                wave_id=run.wave_id,
                dry_run=run.dry_run,
                db_path=adv.db_path,
            ))
            msg = f"Wave {outcome.wave_id}: {outcome.completed}/{outcome.total} completed"
            self._set_step(run, step_id, StepStatus.COMPLETED, msg, outcome.__dict__)
        else:
            phase_cfg = adv.config_path.replace(".yaml", "_phase.yaml")
            config_for_phase = phase_cfg if _phase_config_exists(adv.config_path) else adv.config_path
            # force=True escalates a blocking prior-phase gate, it never skips it
            # (GAP-009). The escalation is only allowed with the operator's written
            # justification, which the console collects on the migrate form and
            # sends as `override_reason`. CA-003: it is free operator text, so it
            # goes through the platform's redaction choke point before it is
            # persisted on the OVERRIDE record and shown in audit history.
            override_reason = str(
                redact_payload((getattr(run, "override_reason", "") or "").strip()),
            )
            phase_value = _PHASE_REQUEST_VALUES.get(run.phase)
            if phase_value is None:
                msg = (
                    f"{step_label}: unknown phase {run.phase!r} — expected one of "
                    f"{', '.join(_PHASE_REQUEST_VALUES)}"
                )
                self._log(run, f"  FAIL: {msg}")
                self._set_step(run, step_id, StepStatus.FAILED, msg, {"phase": run.phase})
                return
            outcome = accel.run_phase(PhaseRunRequest(
                config_path=config_for_phase,
                phase=phase_value,
                dry_run=run.dry_run,
                force=True,
                override_reason=override_reason,
                db_path=adv.db_path,
            ))
            if outcome.completed == 0 and outcome.failed == 0:
                self._set_step(
                    run, step_id, StepStatus.SKIPPED,
                    f"No repos assigned to phase {run.phase} — assign phases on Discovery tab",
                    outcome.__dict__,
                )
                return
            msg = f"Phase {outcome.phase}: {outcome.completed} completed, {outcome.failed} failed"
            migrate_status = (
                StepStatus.FAILED
                if not run.dry_run and outcome.failed > 0
                else StepStatus.COMPLETED
            )
            self._set_step(run, step_id, migrate_status, msg, outcome.__dict__)

    def _step_validate(self, run: PipelineRun) -> None:
        """Verify that migrated repos on GitHub match their ADO sources.

        Compares each repo at commit-SHA and branch level against its ADO
        source, and checks that any workflows committed by the conversion step
        are intact. A dry run is skipped rather than validated, because nothing
        was pushed to GitHub for it to check.

        Args:
            run: The run to advance. Its ``validate`` step is marked completed,
                warned or skipped and carries per-repo validation details.

        Raises:
            RuntimeError: If one or more repos fail validation; the message
                names the failing repos and the primary reason for each, capped
                at fifteen with a count of the remainder.
        """
        from ado2gh.api.accelerator import _build_ado_client, _build_gh_client
        from ado2gh.api.profile_discovery import repo_configs_for_phase, resolve_gh_org
        from ado2gh.api.run_reporting import build_validation_message, validation_repo_detail
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.reporting.post_migration_validator import PostMigrationValidator
        from ado2gh.state.factory import create_state_db

        if run.dry_run:
            self._log(
                run,
                "Skipping validation — dry run did not push code to GitHub "
                "(disable dry run on Migrate to validate after a real migration)",
            )
            self._set_step(
                run, "validate", StepStatus.SKIPPED,
                "Skipped — dry run (no GitHub target to verify)",
                {"total": 0, "passed": 0, "failed": 0, "skipped_reason": "dry_run"},
            )
            return

        adv = self.settings.load().advanced
        profile = self.settings.get_active_profile()
        global_cfg, waves = ConfigLoader.load(adv.config_path)
        gh_org = resolve_gh_org(profile, global_cfg=global_cfg, config_path=adv.config_path)
        if gh_org:
            global_cfg = {**global_cfg, "gh_org": gh_org}

        repos = []
        if profile:
            repos = repo_configs_for_phase(
                run.phase, profile, db_path=adv.db_path, config_path=adv.config_path,
            )
        if not repos:
            repos = load_repos("", global_cfg, waves)

        migrate_step = None
        for sid in ("migrate_repos", "migrate"):
            migrate_step = next((s for s in run.steps if s.id == sid and s.result), None)
            if migrate_step:
                break
        if migrate_step and migrate_step.result:
            from ado2gh.api.run_reporting import migrate_repo_detail

            repo_details = migrate_step.result.get("repo_details") or []
            if not repo_details:
                statuses = migrate_step.result.get("repos") or {}
                repo_details = [
                    migrate_repo_detail(k, v) for k, v in statuses.items()
                ]
            ok_keys = {d["repo"] for d in repo_details if d["status"] == "completed"}
            skipped = []
            filtered = []
            for repo in repos:
                key = f"{repo.ado_project}/{repo.ado_repo}"
                if key in ok_keys or not repo_details:
                    filtered.append(repo)
                else:
                    skipped.append(key)
            for key in skipped:
                self._log(run, f"  skip validate {key} — migration did not complete")
            repos = filtered

        # Read convert_pipelines step result for workflow integrity check
        convert_step = next((s for s in run.steps if s.id == "convert_pipelines" and s.result), None)
        workflow_files = convert_step.result.get("workflow_files") if convert_step and convert_step.result else None

        if not repos:
            self._set_step(run, "validate", StepStatus.SKIPPED,
                           "No successfully migrated repos to validate",
                           {"total": 0, "passed": 0, "failed": 0})
            return

        self._log(run, f"Validating {len(repos)} repo(s) (read-only ADO + GitHub API checks)")
        ado = _build_ado_client(global_cfg)
        gh = _build_gh_client(global_cfg)
        db = create_state_db(adv.db_path)
        validator = PostMigrationValidator(ado, gh, db)
        results = validator.validate(repos, output_path=adv.config_path.replace(".yaml", "_validation.csv"), workflow_files=workflow_files)
        repo_details = [validation_repo_detail(r) for r in results]
        for detail in repo_details:
            key = f"{detail['project']}/{detail['repo']}"
            self._log(
                run,
                f"  {key} -> {detail['gh_target']}: {detail['overall']} — {detail['primary_reason']}",
            )
            for check in detail.get("checks", []):
                if check.get("verdict") in ("FAIL", "WARN"):
                    self._log(
                        run,
                        f"    {check['check']}: {check['verdict']} — {check.get('detail', '')}",
                    )

        passed = sum(1 for r in results if r.get("overall") == "PASS")
        failed_rows = [r for r in results if r.get("overall") == "FAIL"]
        failed = len(failed_rows)
        status = StepStatus.COMPLETED if failed == 0 else StepStatus.FAILED
        msg = build_validation_message(results)
        self._set_step(
            run, "validate", status, msg,
            {
                "passed": passed,
                "failed": failed,
                "total": len(results),
                "repo_details": repo_details,
            },
        )
        if failed:
            reasons = [
                f"{d['project']}/{d['repo']}: {d['primary_reason']}"
                for d in repo_details if d["overall"] == "FAIL"
            ]
            raise RuntimeError(
                f"Validation failed for {failed} repo(s):\n" + "\n".join(reasons[:15])
                + (f"\n... and {len(reasons) - 15} more" if len(reasons) > 15 else "")
            )


def _phase_config_exists(config_path: str) -> bool:
    """Report whether a phase assignment file sits beside a migration configuration.

    The phase file is found by convention rather than configuration: it is the
    migration config path with ``.yaml`` replaced by ``_phase.yaml``.

    Args:
        config_path: Path to the migration config, normally ``migration.yaml``.

    Returns:
        bool: ``True`` when the matching ``*_phase.yaml`` exists on disk, so
        phase-scoped execution can read repo assignments from it.
    """
    from pathlib import Path
    return Path(config_path.replace(".yaml", "_phase.yaml")).exists()
