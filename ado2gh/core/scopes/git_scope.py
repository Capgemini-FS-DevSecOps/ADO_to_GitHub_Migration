"""Git mirror / GEI migration scope."""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

from ado2gh.audit import redact_payload
from ado2gh.core.concurrency import ConcurrencyManager
from ado2gh.core.gei_runtime import build_gei_subprocess_env
from ado2gh.core.scopes.base import ScopeContext, ScopeResult
from ado2gh.logging_config import log
from ado2gh.models import ExecutionMode, MigrationScope, RepoConfig


def _redact(text: str, *secrets: str) -> str:
    """Strip known secret values out of subprocess output.

    Exact-value stripping for the credentials we hold (Azure DevOps personal
    access tokens and GitHub tokens), then the platform's single masking choke
    point (FR-025) for shapes we do not hold — a PAT echoed by git for some
    *other* remote is still a leak.

    Args:
        text: Raw subprocess output, which may quote a credential back at us.
        secrets: Credential values the caller is holding; each one is replaced
            wherever it occurs. Empty values are ignored.

    Returns:
        The same text with every credential the caller passed replaced by a
        fixed mask, and any remaining secret-shaped substring masked too, so it
        is safe to put in an error message, a log line, or an audit record.
    """
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    return str(redact_payload(redacted))


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
    """Strip embedded credentials from an Azure DevOps remote URL.

    Args:
        clone_url: Remote URL as reported by Azure DevOps, which normally
            carries a userinfo prefix in front of the host.

    Returns:
        The same URL with any userinfo component removed, so it can be logged
        and handed to git without leaking whatever was encoded there. A value
        that is not a URL is returned unchanged.
    """
    if "://" not in clone_url:
        return clone_url
    scheme, rest = clone_url.split("://", 1)
    host_and_path = rest.split("/", 1)
    if "@" in host_and_path[0]:
        host_and_path[0] = host_and_path[0].split("@", 1)[1]
    return f"{scheme}://{'/'.join(host_and_path)}"


def _build_git_auth_env(secret: str, *, username: str = "", url: str = "") -> dict[str, str]:
    """Build a git subprocess environment carrying HTTP basic credentials.

    The credential travels in `GIT_CONFIG_*` environment variables rather than
    inside a remote URL, so it never reaches subprocess argv and never appears
    in the process table (GAP-030).

    Args:
        secret: Credential value to authenticate with. It is only encoded into
            the returned environment; it is never logged, and `_redact` strips
            it from any subprocess output that quotes it back.
        username: Userinfo half of the basic credential. Azure DevOps accepts
            an empty user; GitHub expects `x-access-token`.
        url: Remote the header applies to. When set, the git config key is
            scoped to that URL so the header is only sent there; when empty the
            header is sent on every request git makes.

    Returns:
        A copy of the current process environment with interactive credential
        prompts disabled and a single git config override that supplies the
        authorization header.
    """
    encoded = base64.b64encode(f"{username}:{secret}".encode()).decode()
    key = f"http.{url}.extraHeader" if url else "http.extraHeader"
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": key,
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {encoded}",
    }


def _build_ado_git_env(pat: str) -> dict[str, str]:
    """Build the git subprocess environment that authenticates against Azure DevOps.

    Basic authentication through an HTTP extra header is used because it is the
    form Azure DevOps accepts reliably for personal access tokens.

    Args:
        pat: Azure DevOps personal access token.

    Returns:
        The environment described by `_build_git_auth_env`, with the header
        unscoped because the Azure DevOps host varies with the organisation
        and every remote this environment is used against is that host.
    """
    return _build_git_auth_env(pat)


class GitScopeHandler:
    """Move the repository's git history to GitHub, by mirror push or by GEI."""

    scope = MigrationScope.REPO.value

    def _analyze_feasibility(self, repo: RepoConfig, source: dict, repo_stats: dict, ctx: ScopeContext) -> FeasibilityReport:
        """Analyze repository migration feasibility and recommend a strategy.

        Args:
            repo: Repository being assessed.
            source: Azure DevOps repository record; the repository size is read
                from it.
            repo_stats: Azure DevOps repository statistics; the branch count is
                read from it.
            ctx: Scope context, whose configured strategy is the starting
                recommendation before the size thresholds are applied.

        Returns:
            A report pairing the measured size and branch count with a
            recommended strategy ("mirror", "gei" or "manual"), an overall
            status from "ok" through "fail_hard", and the human-readable
            warnings that justify that status. The LFS object count is reported
            as -1 because it cannot be known without cloning.
        """
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

    def migrate(self, repo: RepoConfig, ctx: ScopeContext, **kwargs: object) -> ScopeResult:
        """Migrate the repository contents to GitHub and map its teams.

        Args:
            repo: Repository to migrate.
            ctx: Shared clients, state store and execution mode.
            **kwargs: Per-dispatch extras from `MigrationEngine`; `concurrency`
                supplies the git slot manager when one is in use.

        Returns:
            A `ScopeResult` carrying the chosen strategy, the feasibility report
            and the branch counts observed on both sides. In
            `ExecutionMode.DRY_RUN` no git command runs, nothing is created on
            GitHub and the stats carry `dry_run: True`.

        Raises:
            ValueError: The repository has no GitHub organisation configured.
            RuntimeError: `git` is missing, a git command failed, or the target
                repository already exists with a different HEAD.
        """
        log.info(
            "git: %s/%s -> %s/%s [strategy=%s]%s",
            repo.ado_project, repo.ado_repo, repo.gh_org, repo.gh_repo,
            ctx.strategy, " [DRY RUN]" if ctx.mode is ExecutionMode.DRY_RUN else "",
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

        if ctx.mode is ExecutionMode.DRY_RUN:
            stats["dry_run"] = True
            return ScopeResult(stats=stats)

        if not (repo.gh_org or "").strip():
            raise ValueError(
                f"GitHub org is not set for {repo.ado_project}/{repo.ado_repo}. "
                "Configure gh_org on the migration profile or global.gh_org in migration.yaml."
            )

        concurrency = kwargs.get("concurrency")
        cm = concurrency if isinstance(concurrency, ConcurrencyManager) else None
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
                ctx.gh.create_private_repo(
                    repo.gh_org, repo.gh_repo,
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
        """Decide whether a pre-existing GitHub repo already holds this migration.

        Args:
            repo: Repository being migrated.
            source: Azure DevOps repository record, read for the repository id
                and the default branch name.
            ctx: Scope context supplying the Azure DevOps and GitHub clients.

        Returns:
            Stats recording the import as already done — including the matching
            short commit SHAs from both sides — when the target repo exists and
            its default-branch HEAD equals the Azure DevOps HEAD. `None` when
            the target repo does not exist, or could not be checked, meaning the
            caller should go ahead and run the import.

        Raises:
            RuntimeError: The target repo exists but its HEAD differs from Azure
                DevOps, or its HEAD could not be read at all. GEI cannot import
                into an existing repo, so the operator has to delete it or pick
                a different target name.
        """
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
        """Mirror the Azure DevOps repository onto GitHub with git clone and push.

        Clones into a temporary bare mirror, repoints the remote at GitHub,
        force-pushes every branch and tag, pushes LFS objects unless the
        repository opts out, and syncs the default branch. The temporary clone
        is always removed.

        Args:
            repo: Repository being migrated, including its LFS opt-out flag.
            clone_url: Azure DevOps remote URL; any embedded userinfo is
                stripped before git sees it.
            ctx: Scope context supplying the Azure DevOps personal access token
                and the GitHub token that authenticates the push. Both reach git
                through its subprocess environment only, never through a remote
                URL or any other argument, so neither is visible in the process
                table. Neither value is logged, and both are masked out of any
                error raised from the captured subprocess output.

        Returns:
            Stats marking the mirror as successful, merged with the LFS
            counters produced by `_push_lfs`.

        Raises:
            RuntimeError: `git` is not on PATH, or the clone, the remote
                rewrite, or the push of branches and tags failed. The failure
                text is redacted before it is raised.
        """
        clone_url = _clean_clone_url(clone_url)
        git_env = _build_ado_git_env(ctx.ado.pat)
        gh_token = ctx.gh.token_manager.get_token()
        target_url = f"https://github.com/{repo.gh_org}/{repo.gh_repo}.git"
        gh_env = _build_git_auth_env(gh_token, username="x-access-token", url=target_url)

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
                raise RuntimeError(
                    f"git clone --mirror failed: {_redact(result.stderr[:500], ctx.ado.pat)}"
                )

            result = subprocess.run(
                [git_exe, "remote", "set-url", "origin", target_url],
                capture_output=True, text=True, cwd=mirror_path, timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git remote set-url failed: {_redact(result.stderr[:500], gh_token)}")

            subprocess.run(
                [git_exe, "config", "--unset", "remote.origin.mirror"],
                capture_output=True, text=True, cwd=mirror_path, timeout=10,
            )
            result = subprocess.run(
                [git_exe, "push", "--force", "origin",
                 "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"],
                capture_output=True, text=True, cwd=mirror_path, timeout=3600,
                env=gh_env,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git push (heads+tags) failed: {_redact(result.stderr[:500], gh_token)}")

            lfs_stats = {}
            if not repo.skip_lfs:
                lfs_stats = self._push_lfs(mirror_path, target_url, gh_token, gh_env)

            try:
                source_default = self._get_source_default_branch(mirror_path)
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
    def _get_source_default_branch(mirror_path: str) -> str:
        """Read the default branch name recorded in a local mirror clone.

        Args:
            mirror_path: Path to the bare mirror clone.

        Returns:
            The short branch name that HEAD points at, or an empty string when
            git cannot resolve one, in which case the caller leaves the GitHub
            default branch untouched.
        """
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            capture_output=True, text=True, cwd=mirror_path, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    @staticmethod
    def _push_lfs(mirror_path: str, target_url: str, token: str, env: dict[str, str]) -> dict:
        """Push Git LFS objects from a mirror clone up to the GitHub remote.

        Args:
            mirror_path: Path to the bare mirror clone.
            target_url: GitHub push URL. It carries no credential, so it is
                safe to pass as a subprocess argument (GAP-030).
            token: The GitHub token `env` authenticates with, passed separately
                so it can be stripped from an exception message.
            env: Subprocess environment from `_build_git_auth_env` that supplies
                the authorization header for `target_url`.

        Returns:
            Counters describing the transfer: how many LFS objects the clone
            holds, and whether the push succeeded, was only partial, or was
            skipped because the git-lfs binary is not installed. A count of -1
            means the objects could not be enumerated at all.
        """
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
                env=env,
            )
            if result.returncode != 0:
                return {"lfs_objects": lfs_count, "lfs_push": "partial"}
            return {"lfs_objects": lfs_count, "lfs_push": "success"}
        except FileNotFoundError:
            return {"lfs_objects": -1, "lfs_push": "skipped_no_lfs_binary"}
        except Exception as exc:
            return {"lfs_objects": -1, "lfs_push": f"error: {_redact(str(exc), token)}"}

    def _run_gei(self, repo: RepoConfig, source: dict, ctx: ScopeContext) -> dict:
        """Import the repository using the GitHub Enterprise Importer CLI.

        Args:
            repo: Repository being migrated.
            source: Azure DevOps repository record, used only when the importer
                declines and the existing target has to be verified.
            ctx: Scope context supplying the Azure DevOps personal access token
                and the GitHub token. Both reach the importer through its
                subprocess environment only, and both are masked out of the
                captured output before it can appear in an error or in the
                returned stats.

        Returns:
            Stats marking the import as successful together with the redacted
            head of the importer's output. If the importer declined because the
            target repo already matches Azure DevOps, the skip stats from
            `_verify_existing_target_repo` are returned instead.

        Raises:
            RuntimeError: The importer exited non-zero, printed its usage help
                (which means the gh-ado2gh extension is not installed
                correctly), or refused to import into an existing target repo
                that does not match Azure DevOps.
        """
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
        env = build_gei_subprocess_env(ADO_PAT=ctx.ado.pat, GH_PAT=gh_token)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200, env=env)
        combined = _redact(f"{result.stdout}\n{result.stderr}", ctx.ado.pat, gh_token)
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
        return {"gei": "success", "gei_output": _redact(result.stdout, ctx.ado.pat, gh_token)[:1000]}
