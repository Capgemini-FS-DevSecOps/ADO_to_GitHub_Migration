"""Per-repo migration engine — git mirror/GEI + scope handlers."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ado2gh.clients import ADOClient, GHClient
from ado2gh.logging_config import log
from ado2gh.models import (
    MigrationScope,
    MigrationStatus,
    PipelineMetadata,
    RepoConfig,
)
from ado2gh.pipelines.transformer import PipelineTransformer
from ado2gh.state.db import StateDB


class MigrationEngine:
    """Orchestrates per-repo migration across all requested scopes."""

    SCOPES = [s.value for s in MigrationScope]

    def __init__(self, global_cfg: dict, ado: ADOClient, gh: GHClient,
                 db: StateDB, dry_run: bool = False):
        self.cfg = global_cfg
        self.ado = ado
        self.gh = gh
        self.db = db
        self.dry_run = dry_run
        self.transformer = PipelineTransformer()
        # Migration strategy: "mirror" (default) or "gei"
        self.strategy = global_cfg.get("migration_strategy", "mirror")

    def migrate_repo(self, wave_id: int, repo: RepoConfig,
                     progress: Any = None, task_id: Any = None,
                     pipeline_parallel: int = 8) -> dict:
        results: dict[str, dict] = {}
        requested = repo.scopes or self.SCOPES

        scope_handlers = {
            MigrationScope.REPO.value: self._migrate_git,
            MigrationScope.WORK_ITEMS.value: self._migrate_work_items,
            MigrationScope.PIPELINES.value: self._migrate_pipelines,
            MigrationScope.WIKI.value: self._migrate_wiki,
            MigrationScope.SECRETS.value: self._migrate_secrets,
            MigrationScope.BRANCH_POLICIES.value: self._migrate_branch_policies,
        }

        for scope in self.SCOPES:
            if scope not in requested:
                continue
            handler = scope_handlers.get(scope)
            if handler is None:
                continue

            self.db.upsert_migration(wave_id, repo, scope, MigrationStatus.IN_PROGRESS)

            if progress and task_id is not None:
                progress.update(task_id, description=f"[cyan]{repo.ado_repo}[/] → {scope}")

            try:
                kwargs: dict[str, Any] = {}
                if scope == MigrationScope.PIPELINES.value:
                    kwargs["pipeline_parallel"] = pipeline_parallel
                    kwargs["wave_id"] = wave_id

                scope_result = handler(repo, **kwargs)
                results[scope] = {"status": "completed", "detail": scope_result}
                self.db.upsert_migration(
                    wave_id, repo, scope, MigrationStatus.COMPLETED,
                    stats=scope_result,
                )
            except Exception as exc:
                log.error("scope %s failed for %s/%s: %s",
                          scope, repo.ado_project, repo.ado_repo, exc)
                results[scope] = {"status": "failed", "error": str(exc)}
                self.db.upsert_migration(
                    wave_id, repo, scope, MigrationStatus.FAILED,
                    error=str(exc),
                )

        completed = sum(1 for v in results.values() if v["status"] == "completed")
        total = len(results)
        overall = "completed" if completed == total else ("partial" if completed > 0 else "failed")

        if progress and task_id is not None:
            progress.advance(task_id)

        return {"status": overall, "scopes": results, "errors": [
            v["error"] for v in results.values() if v.get("error")
        ]}

    # ── GIT MIGRATION (the actual mirror) ───────────────────────────────────

    def _migrate_git(self, repo: RepoConfig, **_kw: Any) -> dict:
        """Actually migrate git content via mirror clone + push, or gh gei."""
        log.info("git: %s/%s → %s/%s [strategy=%s]%s",
                 repo.ado_project, repo.ado_repo,
                 repo.gh_org, repo.gh_repo, self.strategy,
                 " [DRY RUN]" if self.dry_run else "")

        source = self.ado.get_repo(repo.ado_project, repo.ado_repo)
        clone_url = source.get("remoteUrl", "")
        default_branch = (source.get("defaultBranch", "refs/heads/main")
                          .replace("refs/heads/", ""))
        repo_stats = self.ado.get_repo_stats(repo.ado_project, source.get("id", ""))

        stats: dict[str, Any] = {
            "strategy": self.strategy,
            "source_url": clone_url,
            "default_branch": default_branch,
            "branches": repo_stats.get("branch_count", 0),
            "size_kb": source.get("size", 0),
        }

        if self.dry_run:
            stats["dry_run"] = True
            return stats

        # Create target repo on GitHub if it doesn't exist
        if not self.gh.repo_exists(repo.gh_org, repo.gh_repo):
            self.gh.create_repo(
                repo.gh_org, repo.gh_repo, private=True,
                description=f"Migrated from ADO: {repo.ado_project}/{repo.ado_repo}",
            )

        if self.strategy == "gei":
            stats.update(self._run_gei_migration(repo, source))
        else:
            stats.update(self._run_mirror_migration(repo, clone_url))

        # Apply team mappings
        for ado_team, gh_team in repo.team_mapping.items():
            try:
                self.gh.add_team_to_repo(repo.gh_org, gh_team, repo.gh_repo)
            except Exception as exc:
                log.warning("team mapping %s → %s failed: %s", ado_team, gh_team, exc)

        # Verify: check that the default branch exists on target
        try:
            gh_branches = self.gh.list_branches(repo.gh_org, repo.gh_repo)
            gh_branch_names = [b.get("name", "") for b in gh_branches]
            stats["gh_branches"] = len(gh_branch_names)
            stats["default_branch_present"] = default_branch in gh_branch_names
        except Exception:
            stats["gh_branches"] = -1
            stats["default_branch_present"] = None

        return stats

    def _run_mirror_migration(self, repo: RepoConfig, clone_url: str) -> dict:
        """Execute git clone --mirror && git push --mirror."""
        auth_url = clone_url.replace("https://", f"https://:{self.ado.pat}@")
        gh_token = self.gh.token_manager.get_token()
        target_url = f"https://x-access-token:{gh_token}@github.com/{repo.gh_org}/{repo.gh_repo}.git"

        tmpdir = tempfile.mkdtemp(prefix="ado2gh_mirror_")
        mirror_path = os.path.join(tmpdir, f"{repo.ado_repo}.git")

        try:
            # Clone mirror from ADO
            result = subprocess.run(
                ["git", "clone", "--mirror", auth_url, mirror_path],
                capture_output=True, text=True, timeout=1800,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if result.returncode != 0:
                raise RuntimeError(f"git clone --mirror failed: {result.stderr[:500]}")

            # Push mirror to GitHub
            result = subprocess.run(
                ["git", "remote", "set-url", "origin", target_url],
                capture_output=True, text=True, cwd=mirror_path, timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git remote set-url failed: {result.stderr[:500]}")

            result = subprocess.run(
                ["git", "push", "--mirror"],
                capture_output=True, text=True, cwd=mirror_path, timeout=3600,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if result.returncode != 0:
                raise RuntimeError(f"git push --mirror failed: {result.stderr[:500]}")

            # LFS: if repo has LFS objects and skip_lfs is not set
            lfs_stats = {}
            if not repo.skip_lfs:
                lfs_stats = self._push_lfs_objects(mirror_path, target_url)

            return {"mirror": "success", **lfs_stats}

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _push_lfs_objects(self, mirror_path: str, target_url: str) -> dict:
        """Push LFS objects to target (if any exist)."""
        try:
            result = subprocess.run(
                ["git", "lfs", "ls-files"],
                capture_output=True, text=True, cwd=mirror_path, timeout=60,
            )
            lfs_count = len(result.stdout.strip().splitlines()) if result.stdout.strip() else 0
            if lfs_count == 0:
                return {"lfs_objects": 0}

            result = subprocess.run(
                ["git", "lfs", "push", "--all", target_url],
                capture_output=True, text=True, cwd=mirror_path, timeout=3600,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if result.returncode != 0:
                log.warning("LFS push had errors: %s", result.stderr[:300])
                return {"lfs_objects": lfs_count, "lfs_push": "partial"}

            return {"lfs_objects": lfs_count, "lfs_push": "success"}
        except FileNotFoundError:
            log.warning("git-lfs not installed, skipping LFS push")
            return {"lfs_objects": -1, "lfs_push": "skipped_no_lfs_binary"}
        except Exception as exc:
            log.warning("LFS push failed: %s", exc)
            return {"lfs_objects": -1, "lfs_push": f"error: {exc}"}

    def _run_gei_migration(self, repo: RepoConfig, source: dict) -> dict:
        """Execute migration using GitHub Enterprise Importer (gh gei)."""
        ado_org = self.cfg.get("ado_org_url", "").rstrip("/").split("/")[-1]
        gh_token = self.gh.token_manager.get_token()
        ado_pat = self.ado.pat

        cmd = [
            "gh", "gei", "migrate-repo",
            "--ado-org", ado_org,
            "--ado-team-project", repo.ado_project,
            "--ado-repo", repo.ado_repo,
            "--github-org", repo.gh_org,
            "--github-repo", repo.gh_repo,
            "--wait",
        ]

        env = {
            **os.environ,
            "ADO_PAT": ado_pat,
            "GH_PAT": gh_token,
        }

        log.info("Running: gh gei migrate-repo %s/%s → %s/%s",
                 repo.ado_project, repo.ado_repo, repo.gh_org, repo.gh_repo)

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=7200, env=env,
        )

        if result.returncode != 0:
            raise RuntimeError(f"gh gei failed (exit {result.returncode}): {result.stderr[:500]}")

        return {
            "gei": "success",
            "gei_output": result.stdout[:1000],
        }

    # ── WORK ITEMS ──────────────────────────────────────────────────────────

    def _migrate_work_items(self, repo: RepoConfig, **_kw: Any) -> dict:
        log.info("work_items: %s/%s → %s/%s%s",
                 repo.ado_project, repo.ado_repo,
                 repo.gh_org, repo.gh_repo,
                 " [DRY RUN]" if self.dry_run else "")

        work_items = self.ado.list_work_items(repo.ado_project)
        stats = {"total": len(work_items), "created": 0, "skipped": 0}

        if self.dry_run:
            stats["dry_run"] = True
            return stats

        wi_types = {wi.get("fields", {}).get("System.WorkItemType", "Task")
                    for wi in work_items}
        for label in wi_types:
            self.gh.create_label(repo.gh_org, repo.gh_repo, f"ado:{label}")

        for wi in work_items:
            fields = wi.get("fields", {})
            title = fields.get("System.Title", "Untitled")
            wi_type = fields.get("System.WorkItemType", "Task")
            state = fields.get("System.State", "")
            desc = fields.get("System.Description", "") or ""
            body = (
                f"**Migrated from Azure DevOps**\n\n"
                f"- **Type:** {wi_type}\n"
                f"- **State:** {state}\n"
                f"- **ADO ID:** {wi.get('id', '')}\n\n"
                f"{desc}"
            )
            try:
                self.gh.create_issue(
                    repo.gh_org, repo.gh_repo, title,
                    body=body, labels=[f"ado:{wi_type}"],
                )
                stats["created"] += 1
            except Exception as exc:
                log.warning("work-item %s failed: %s", wi.get("id"), exc)
                stats["skipped"] += 1

        return stats

    # ── PIPELINES ───────────────────────────────────────────────────────────

    def _migrate_pipelines(self, repo: RepoConfig, *,
                           pipeline_parallel: int = 8,
                           wave_id: int = 0, **_kw: Any) -> dict:
        pipelines = self.db.get_pipelines_for_repo(repo.ado_project, repo.ado_repo)
        if repo.pipeline_filter:
            pat = re.compile(repo.pipeline_filter)
            pipelines = [p for p in pipelines if pat.search(p.pipeline_name)]

        stats: dict[str, Any] = {
            "total": len(pipelines), "completed": 0,
            "failed": 0, "skipped": 0, "warnings": [],
        }

        if not pipelines:
            return stats

        # Pre-create GitHub environments
        env_names: set[str] = set()
        for pipe in pipelines:
            for env in pipe.environments:
                env_names.add(env.name)
            for stage in pipe.stages:
                if stage.environment:
                    env_names.add(stage.environment.name)

        if not self.dry_run:
            for env_name in sorted(env_names):
                try:
                    self.gh.create_environment(repo.gh_org, repo.gh_repo, env_name)
                except Exception as exc:
                    log.warning("env create %s failed: %s", env_name, exc)

        existing = self.db.get_wave_pipeline_migrations(wave_id)
        completed_ids = {
            r["pipeline_id"] for r in existing
            if r["status"] == MigrationStatus.COMPLETED.value
               and r["project"] == repo.ado_project
        }
        pending = [p for p in pipelines if p.pipeline_id not in completed_ids]
        stats["skipped"] = len(pipelines) - len(pending)

        if self.dry_run:
            stats["dry_run"] = True
            return stats

        def _transform_one(pipe: PipelineMetadata) -> dict:
            self.db.upsert_pipeline_migration(
                wave_id, pipe, repo.gh_org, repo.gh_repo,
                MigrationStatus.IN_PROGRESS,
            )
            try:
                result = self.transformer.transform(pipe)
                self.db.upsert_pipeline_migration(
                    wave_id, pipe, repo.gh_org, repo.gh_repo,
                    MigrationStatus.COMPLETED,
                    workflow_file=result.get("workflow_yaml", ""),
                    warnings=result.get("warnings", []),
                    unsupported=result.get("unsupported_tasks", []),
                    transform_stats=result.get("stats"),
                )
                return {"pipeline_id": pipe.pipeline_id, "status": "completed"}
            except Exception as exc:
                self.db.upsert_pipeline_migration(
                    wave_id, pipe, repo.gh_org, repo.gh_repo,
                    MigrationStatus.FAILED, error=str(exc),
                )
                return {"pipeline_id": pipe.pipeline_id, "status": "failed",
                        "error": str(exc)}

        with ThreadPoolExecutor(max_workers=min(pipeline_parallel, max(1, len(pending)))) as pool:
            futures = {pool.submit(_transform_one, p): p for p in pending}
            for fut in as_completed(futures):
                res = fut.result()
                if res["status"] == "completed":
                    stats["completed"] += 1
                else:
                    stats["failed"] += 1
                    stats["warnings"].append(res.get("error", "unknown"))

        return stats

    # ── WIKI ────────────────────────────────────────────────────────────────

    def _migrate_wiki(self, repo: RepoConfig, **_kw: Any) -> dict:
        wikis = self.ado.list_wiki_pages(repo.ado_project)
        stats = {"wiki_count": len(wikis), "pages": 0}
        if self.dry_run:
            stats["dry_run"] = True
            return stats
        out = Path(f"output/wikis/{repo.gh_org}/{repo.gh_repo}")
        out.mkdir(parents=True, exist_ok=True)
        for wiki_data in wikis:
            root = wiki_data.get("root", {})
            self._write_wiki_page(root, out, stats)
        return stats

    def _write_wiki_page(self, page: dict, parent: Path, stats: dict):
        title = (page.get("path", "/Home").split("/")[-1]) or "Home"
        safe = re.sub(r"[^a-zA-Z0-9\-_. ]", "_", title)
        (parent / f"{safe}.md").write_text(
            page.get("content", f"# {title}\n"), encoding="utf-8"
        )
        stats["pages"] += 1
        sub_dir = parent / safe
        for sub in page.get("subPages", []):
            sub_dir.mkdir(exist_ok=True)
            self._write_wiki_page(sub, sub_dir, stats)

    # ── SECRETS ─────────────────────────────────────────────────────────────

    def _migrate_secrets(self, repo: RepoConfig, **_kw: Any) -> dict:
        var_groups = self.ado.list_variable_groups(repo.ado_project)
        svc_conns = self.ado.list_service_connections(repo.ado_project)
        stats = {"variable_groups": len(var_groups),
                 "service_connections": len(svc_conns)}
        if self.dry_run:
            stats["dry_run"] = True
            return stats

        # Generate secrets mapping manifest (values cannot be migrated)
        out = Path(f"output/secrets/{repo.gh_org}/{repo.gh_repo}")
        out.mkdir(parents=True, exist_ok=True)
        import json
        mapping = {
            "instructions": (
                f"Secret VALUES cannot be read from ADO API. "
                f"Use: gh secret set SECRET_NAME --body VALUE "
                f"--repo {repo.gh_org}/{repo.gh_repo}"
            ),
            "variable_groups": [
                {"name": vg.get("name"), "type": vg.get("type"),
                 "variables": [
                     {"name": k, "is_secret": v.get("isSecret", False)}
                     for k, v in vg.get("variables", {}).items()
                 ]}
                for vg in var_groups
            ],
            "service_connections": [
                {"name": sc.get("name"), "type": sc.get("type"),
                 "suggestion": self._suggest_gh_secret(sc)}
                for sc in svc_conns
            ],
        }
        (out / "secrets_mapping.json").write_text(json.dumps(mapping, indent=2))
        stats["manifest_path"] = str(out / "secrets_mapping.json")
        return stats

    def _suggest_gh_secret(self, sc: dict) -> str:
        t = sc.get("type", "").lower()
        if "azure" in t:
            return "AZURE_CREDENTIALS or use OIDC (azure/login@v2 with federated credentials)"
        if "docker" in t:
            return "DOCKERHUB_USERNAME + DOCKERHUB_TOKEN"
        if "github" in t:
            return "GH_TOKEN (already available)"
        if "aws" in t:
            return "AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (or OIDC with aws-actions/configure-aws-credentials)"
        if "kubernetes" in t:
            return "KUBE_CONFIG (base64-encoded kubeconfig)"
        if "npm" in t:
            return "NPM_TOKEN"
        if "nuget" in t:
            return "NUGET_API_KEY"
        return f"Review manually — type: {sc.get('type', 'unknown')}"

    # ── BRANCH POLICIES ─────────────────────────────────────────────────────

    def _migrate_branch_policies(self, repo: RepoConfig, **_kw: Any) -> dict:
        source = self.ado.get_repo(repo.ado_project, repo.ado_repo)
        repo_id = source.get("id", "")
        policies = self.ado.list_branch_policies(repo.ado_project, repo_id)
        stats = {"policies_found": len(policies), "rules_created": 0}

        if self.dry_run:
            stats["dry_run"] = True
            return stats

        for policy in policies:
            settings = policy.get("settings", {})
            scope_list = settings.get("scope", [])

            for scope_entry in scope_list:
                branch = scope_entry.get("refName", "").replace("refs/heads/", "")
                if not branch:
                    continue
                try:
                    reviewers = settings.get("minimumApproverCount", 1)
                    self.gh.set_branch_protection(
                        repo.gh_org, repo.gh_repo, branch,
                        required_reviewers=reviewers,
                    )
                    stats["rules_created"] += 1
                except Exception as exc:
                    log.warning("branch protection for %s failed: %s", branch, exc)

        return stats
