"""Git mirror / GEI migration scope."""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

from ado2gh.core.gei_runtime import gei_subprocess_env
from ado2gh.core.scopes.base import ScopeContext, ScopeResult
from ado2gh.logging_config import log
from ado2gh.models import MigrationScope, RepoConfig


@dataclass
class FeasibilityReport:
    """Repository migration feasibility analysis."""
    repo_id: str
    size_kb: int
    size_mb: float
    size_gb: float
    branch_count: int
    lfs_objects: int
    lfs_size_gb: float
    strategy: str  # "mirror", "gei", "manual"
    status: str  # "ok", "warn", "fail_soft", "fail_hard"
    warnings: list[str]
    gei_available: bool


def _clean_clone_url(clone_url: str) -> str:
    """Strip embedded credentials from ADO remoteUrl (e.g. https://org@dev.azure.com/...)."""
    if "://" not in clone_url:
        return clone_url
    scheme, rest = clone_url.split("://", 1)
    host_and_path = rest.split("/", 1)
    if "@" in host_and_path[0]:
        host_and_path[0] = host_and_path[0].split("@", 1)[1]
    return f"{scheme}://{'/'.join(host_and_path)}"


def _ado_git_env(pat: str) -> dict[str, str]:
    """Git subprocess env using Basic auth header (reliable for Azure DevOps PATs)."""
    token = base64.b64encode(f":{pat}".encode()).decode()
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {token}",
    }


class GitScopeHandler:
    scope = MigrationScope.REPO.value

    def _analyze_feasibility(self, repo: RepoConfig, source: dict, repo_stats: dict, ctx: ScopeContext) -> FeasibilityReport:
        """Analyze repository migration feasibility and recommend strategy."""
        size_kb = source.get("size", 0)
        size_mb = size_kb / 1024
        size_gb = size_mb / 1024
        branch_count = repo_stats.get("branch_count", 0)
        repo_id = f"{repo.ado_project}/{repo.ado_repo}"

        # Check GEI availability
        gei_available = shutil.which("gh") is not None

        warnings = []
        status = "ok"
        strategy = ctx.strategy or "mirror"

        # Size thresholds per research.md R-001
        if size_gb > 10:
            status = "fail_hard"
            strategy = "manual"
            warnings.append(f"Repository size ({size_gb:.2f} GB) exceeds 10 GB threshold - manual migration required")
        elif size_gb > 2:
            status = "fail_soft"
            strategy = "gei" if gei_available else "manual"
            warnings.append(f"Repository size ({size_gb:.2f} GB) exceeds 2 GB - GEI recommended if available")
        elif size_gb > 0.5:
            status = "warn"
            warnings.append(f"Repository size ({size_gb:.2f} GB) is large - consider GEI for faster migration")

        # LFS check (simplified - actual LFS size requires cloning)
        lfs_objects = -1  # Unknown without clone
        lfs_size_gb = 0

        # Branch count warning
        if branch_count > 500:
            status = "warn" if status == "ok" else status
            warnings.append(f"High branch count ({branch_count}) - may require longer migration time")

        return FeasibilityReport(
            repo_id=repo_id,
            size_kb=size_kb,
            size_mb=size_mb,
            size_gb=size_gb,
            branch_count=branch_count,
            lfs_objects=lfs_objects,
            lfs_size_gb=lfs_size_gb,
            strategy=strategy,
            status=status,
            warnings=warnings,
            gei_available=gei_available,
        )

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

        # Run feasibility analysis
        feasibility = self._analyze_feasibility(repo, source, repo_stats, ctx)

        stats: dict[str, Any] = {
            "strategy": ctx.strategy or feasibility.strategy,
            "source_url": clone_url,
            "default_branch": default_branch,
            "branches": repo_stats.get("branch_count", 0),
            "size_kb": source.get("size", 0),
            "feasibility_report": {
                "size_gb": feasibility.size_gb,
                "status": feasibility.status,
                "strategy": feasibility.strategy,
                "warnings": feasibility.warnings,
            },
        }

        if ctx.dry_run:
            stats["dry_run"] = True
            return ScopeResult(stats=stats)

        if not (repo.gh_org or "").strip():
            raise ValueError(
                f"GitHub org is not set for {repo.ado_project}/{repo.ado_repo}. "
                "Configure gh_org on the migration profile or global.gh_org in migration.yaml."
            )

        cm = kwargs.get("concurrency")
        if ctx.strategy == "gei":
            existing = self._verify_existing_target_repo(repo, source, ctx)
            if existing:
                stats.update(existing)
            elif cm:
                with cm.git_slot():
                    stats.update(self._run_gei(repo, source, ctx))
            else:
                stats.update(self._run_gei(repo, source, ctx))
        else:
            if not ctx.gh.repo_exists(repo.gh_org, repo.gh_repo):
                ctx.gh.create_repo(
                    repo.gh_org, repo.gh_repo, private=True,
                    description=f"Migrated from ADO: {repo.ado_project}/{repo.ado_repo}",
                )
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

    def _verify_existing_target_repo(
        self,
        repo: RepoConfig,
        source: dict,
        ctx: ScopeContext,
    ) -> dict[str, Any] | None:
        """Skip GEI when the GitHub repo exists and default-branch HEAD matches ADO."""
        try:
            if not ctx.gh.repo_exists(repo.gh_org, repo.gh_repo):
                return None
        except Exception:
            return None

        default_branch = (
            source.get("defaultBranch", "refs/heads/main").replace("refs/heads/", "")
        )
        repo_id = source.get("id", "")
        ado_sha = ""
        try:
            commits = ctx.ado.get_repo_commits(
                repo.ado_project, repo_id, top=1, branch=default_branch,
            )
            ado_sha = commits[0].get("commitId", "") if commits else ""
        except Exception as exc:
            log.warning("could not read ADO HEAD for %s/%s: %s",
                        repo.ado_project, repo.ado_repo, exc)

        gh_sha = ""
        try:
            for branch in ctx.gh.list_branches(repo.gh_org, repo.gh_repo):
                if branch.get("name") == default_branch:
                    gh_sha = branch.get("commit", {}).get("sha", "")
                    break
        except Exception as exc:
            log.warning("could not read GitHub HEAD for %s/%s: %s",
                        repo.gh_org, repo.gh_repo, exc)

        if ado_sha and gh_sha and ado_sha == gh_sha:
            return {
                "gei": "skipped",
                "mirror": "already_migrated",
                "message": (
                    "Target repo exists and default-branch HEAD matches ADO — "
                    "skipping GEI import"
                ),
                "ado_sha": ado_sha[:12],
                "gh_sha": gh_sha[:12],
            }

        if gh_sha:
            raise RuntimeError(
                f"Target repo {repo.gh_org}/{repo.gh_repo} already exists on GitHub "
                f"but HEAD commit does not match ADO "
                f"(ADO={ado_sha[:12] if ado_sha else 'unknown'}, "
                f"GH={gh_sha[:12]}). Delete the GitHub repo or choose a different name."
            )

        raise RuntimeError(
            f"Target repo {repo.gh_org}/{repo.gh_repo} already exists on GitHub. "
            "GEI cannot import into an existing repo. Delete it or choose a different "
            "github_repo name."
        )

    def _run_mirror(self, repo: RepoConfig, clone_url: str, ctx: ScopeContext) -> dict:
        clone_url = _clean_clone_url(clone_url)
        git_env = _ado_git_env(ctx.ado.pat)
        gh_token = ctx.gh.token_manager.get_token()
        target_url = (
            f"https://x-access-token:{gh_token}@github.com/"
            f"{repo.gh_org}/{repo.gh_repo}.git"
        )

        tmpdir = tempfile.mkdtemp(prefix="ado2gh_mirror_")
        mirror_path = os.path.join(tmpdir, f"{repo.ado_repo}.git")
        git_exe = shutil.which("git")
        if not git_exe:
            raise RuntimeError(
                "git executable not found on PATH. Install Git for Windows "
                "(https://git-scm.com/download/win) and restart your terminal."
            )
        try:
            result = subprocess.run(
                [git_exe, "clone", "--mirror", clone_url, mirror_path],
                capture_output=True, text=True, timeout=1800,
                env=git_env,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git clone --mirror failed: {result.stderr[:500]}")

            result = subprocess.run(
                [git_exe, "remote", "set-url", "origin", target_url],
                capture_output=True, text=True, cwd=mirror_path, timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git remote set-url failed: {result.stderr[:500]}")

            subprocess.run(
                [git_exe, "config", "--unset", "remote.origin.mirror"],
                capture_output=True, text=True, cwd=mirror_path, timeout=10,
            )
            result = subprocess.run(
                [git_exe, "push", "--force", "origin",
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
            "gh", "ado2gh", "migrate-repo",
            "--ado-org", ado_org,
            "--ado-team-project", repo.ado_project,
            "--ado-repo", repo.ado_repo,
            "--github-org", repo.gh_org,
            "--github-repo", repo.gh_repo,
        ]
        env = gei_subprocess_env(ADO_PAT=ctx.ado.pat, GH_PAT=gh_token)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200, env=env)
        combined = f"{result.stdout}\n{result.stderr}"
        if result.returncode != 0:
            raise RuntimeError(
                f"gh ado2gh migrate-repo failed (exit {result.returncode}): "
                f"{combined[:800]}"
            )
        if "Usage:" in combined and "migrate-repo" in combined:
            raise RuntimeError(
                "gh ado2gh migrate-repo printed usage help — check gh-ado2gh extension install"
            )
        if "no operation will be performed" in combined.lower():
            existing = self._verify_existing_target_repo(repo, source, ctx)
            if existing:
                return existing
            raise RuntimeError(
                "gh ado2gh skipped migration: target repo already exists on GitHub. "
                "Delete the empty target repo or choose a different github_repo name."
            )
        return {"gei": "success", "gei_output": result.stdout[:1000]}
