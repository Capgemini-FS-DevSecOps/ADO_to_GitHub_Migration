"""Git mirror / GEI migration scope."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Any

from ado2gh.core.scopes.base import ScopeContext, ScopeResult
from ado2gh.logging_config import log
from ado2gh.models import MigrationScope, RepoConfig


class GitScopeHandler:
    scope = MigrationScope.REPO.value

    def migrate(self, repo: RepoConfig, ctx: ScopeContext, **kwargs: Any) -> ScopeResult:
        log.info(
            "git: %s/%s -> %s/%s [strategy=%s]%s",
            repo.ado_project, repo.ado_repo, repo.gh_org, repo.gh_repo,
            ctx.strategy, " [DRY RUN]" if ctx.dry_run else "",
        )

        source = ctx.ado.get_repo(repo.ado_project, repo.ado_repo)
        clone_url = source.get("remoteUrl", "")
        default_branch = (
            source.get("defaultBranch", "refs/heads/main").replace("refs/heads/", "")
        )
        repo_stats = ctx.ado.get_repo_stats(repo.ado_project, source.get("id", ""))

        stats: dict[str, Any] = {
            "strategy": ctx.strategy,
            "source_url": clone_url,
            "default_branch": default_branch,
            "branches": repo_stats.get("branch_count", 0),
            "size_kb": source.get("size", 0),
        }

        if ctx.dry_run:
            stats["dry_run"] = True
            return ScopeResult(stats=stats)

        if not (repo.gh_org or "").strip():
            raise ValueError(
                f"GitHub org is not set for {repo.ado_project}/{repo.ado_repo}. "
                "Configure gh_org on the migration profile or global.gh_org in migration.yaml."
            )

        if not ctx.gh.repo_exists(repo.gh_org, repo.gh_repo):
            ctx.gh.create_repo(
                repo.gh_org, repo.gh_repo, private=True,
                description=f"Migrated from ADO: {repo.ado_project}/{repo.ado_repo}",
            )

        cm = kwargs.get("concurrency")
        if ctx.strategy == "gei":
            if cm:
                with cm.git_slot():
                    stats.update(self._run_gei(repo, source, ctx))
            else:
                stats.update(self._run_gei(repo, source, ctx))
        else:
            if cm:
                with cm.git_slot():
                    stats.update(self._run_mirror(repo, clone_url, ctx))
            else:
                stats.update(self._run_mirror(repo, clone_url, ctx))

        for ado_team, gh_team in repo.team_mapping.items():
            try:
                ctx.gh.add_team_to_repo(repo.gh_org, gh_team, repo.gh_repo)
            except Exception as exc:
                log.warning("team mapping %s -> %s failed: %s", ado_team, gh_team, exc)

        try:
            gh_branches = ctx.gh.list_branches(repo.gh_org, repo.gh_repo)
            gh_branch_names = [b.get("name", "") for b in gh_branches]
            stats["gh_branches"] = len(gh_branch_names)
            stats["default_branch_present"] = default_branch in gh_branch_names
        except Exception:
            stats["gh_branches"] = -1
            stats["default_branch_present"] = None

        return ScopeResult(stats=stats)

    def _run_mirror(self, repo: RepoConfig, clone_url: str, ctx: ScopeContext) -> dict:
        if "://" in clone_url:
            scheme, rest = clone_url.split("://", 1)
            host_and_path = rest.split("/", 1)
            if "@" in host_and_path[0]:
                host_and_path[0] = host_and_path[0].split("@", 1)[1]
            clone_url = f"{scheme}://{'/'.join(host_and_path)}"
        auth_url = clone_url.replace("https://", f"https://:{ctx.ado.pat}@")
        gh_token = ctx.gh.token_manager.get_token()
        target_url = (
            f"https://x-access-token:{gh_token}@github.com/"
            f"{repo.gh_org}/{repo.gh_repo}.git"
        )

        tmpdir = tempfile.mkdtemp(prefix="ado2gh_mirror_")
        mirror_path = os.path.join(tmpdir, f"{repo.ado_repo}.git")
        try:
            result = subprocess.run(
                ["git", "clone", "--mirror", auth_url, mirror_path],
                capture_output=True, text=True, timeout=1800,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if result.returncode != 0:
                raise RuntimeError(f"git clone --mirror failed: {result.stderr[:500]}")

            result = subprocess.run(
                ["git", "remote", "set-url", "origin", target_url],
                capture_output=True, text=True, cwd=mirror_path, timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git remote set-url failed: {result.stderr[:500]}")

            subprocess.run(
                ["git", "config", "--unset", "remote.origin.mirror"],
                capture_output=True, text=True, cwd=mirror_path, timeout=10,
            )
            result = subprocess.run(
                ["git", "push", "--force", "origin",
                 "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"],
                capture_output=True, text=True, cwd=mirror_path, timeout=3600,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if result.returncode != 0:
                raise RuntimeError(f"git push (heads+tags) failed: {result.stderr[:500]}")

            lfs_stats = {}
            if not repo.skip_lfs:
                lfs_stats = self._push_lfs(mirror_path, target_url)

            try:
                source_default = self._source_default_branch(mirror_path)
                if source_default:
                    current = ctx.gh.get_default_branch(repo.gh_org, repo.gh_repo)
                    if current != source_default:
                        ctx.gh.set_default_branch(
                            repo.gh_org, repo.gh_repo, source_default,
                        )
            except Exception as exc:
                log.warning("default_branch sync failed for %s/%s: %s",
                            repo.gh_org, repo.gh_repo, exc)

            return {"mirror": "success", **lfs_stats}
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @staticmethod
    def _source_default_branch(mirror_path: str) -> str:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            capture_output=True, text=True, cwd=mirror_path, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    @staticmethod
    def _push_lfs(mirror_path: str, target_url: str) -> dict:
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
                return {"lfs_objects": lfs_count, "lfs_push": "partial"}
            return {"lfs_objects": lfs_count, "lfs_push": "success"}
        except FileNotFoundError:
            return {"lfs_objects": -1, "lfs_push": "skipped_no_lfs_binary"}
        except Exception as exc:
            return {"lfs_objects": -1, "lfs_push": f"error: {exc}"}

    def _run_gei(self, repo: RepoConfig, source: dict, ctx: ScopeContext) -> dict:
        ado_org = ctx.global_cfg.get("ado_org_url", "").rstrip("/").split("/")[-1]
        gh_token = ctx.gh.token_manager.get_token()
        cmd = [
            "gh", "gei", "migrate-repo",
            "--ado-org", ado_org,
            "--ado-team-project", repo.ado_project,
            "--ado-repo", repo.ado_repo,
            "--github-org", repo.gh_org,
            "--github-repo", repo.gh_repo,
            "--wait",
        ]
        env = {**os.environ, "ADO_PAT": ctx.ado.pat, "GH_PAT": gh_token}
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200, env=env)
        if result.returncode != 0:
            raise RuntimeError(
                f"gh gei failed (exit {result.returncode}): {result.stderr[:500]}"
            )
        return {"gei": "success", "gei_output": result.stdout[:1000]}
