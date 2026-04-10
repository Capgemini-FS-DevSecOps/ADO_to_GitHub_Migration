"""Git-based publisher for generated workflow artifacts.

This keeps us MCP/tool-server friendly: a single small capability that can be
called by an AI agent or by MigrationEngine.

We intentionally use `git` CLI rather than GitHub Contents API to avoid
limitations and to match the existing repo-migration approach.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitCommitResult:
    repo_dir: str
    branch: str
    committed: bool
    commit_sha: str | None
    files_added: int
    files_updated: int
    message: str


def _run(cmd: list[str], *, cwd: str | Path | None = None, timeout: int = 300, env: dict | None = None) -> str:
    r = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {r.stderr.strip()[:500]}")
    return r.stdout.strip()


def publish_workflows_via_git(
    *,
    remote_url: str,
    artifacts_dir: Path,
    branch: str = "main",
    commit_message: str = "chore: add migrated GitHub Actions workflows",
    author_name: str = "ado2gh",
    author_email: str = "ado2gh@local",
) -> GitCommitResult:
    """Clone the target repo, copy workflow artifacts, commit and push.

    Parameters
    - remote_url: authenticated https URL (x-access-token) to GitHub repo
    - artifacts_dir: directory containing generated workflow files
                     (typically output/workflows/<org>/<repo>)
    - branch: branch to commit into; will be created if missing

    Returns
    - GitCommitResult

    Notes
    - We commit into `/.github/workflows`.
    - If there are no changes, we don't create a commit.
    """
    artifacts_dir = Path(artifacts_dir)
    if not artifacts_dir.exists():
        return GitCommitResult(
            repo_dir="",
            branch=branch,
            committed=False,
            commit_sha=None,
            files_added=0,
            files_updated=0,
            message=f"Artifacts dir not found: {artifacts_dir}",
        )

    tmpdir = Path(tempfile.mkdtemp(prefix="ado2gh_workflows_"))
    try:
        _run(["git", "clone", "--no-tags", "--depth", "1", remote_url, str(tmpdir)], timeout=1800)
        _run(["git", "config", "user.name", author_name], cwd=tmpdir)
        _run(["git", "config", "user.email", author_email], cwd=tmpdir)

        # Ensure branch exists locally.
        # If remote branch doesn't exist, create it from current HEAD.
        existing_remote = _run(["git", "ls-remote", "--heads", "origin", branch], cwd=tmpdir)
        if existing_remote:
            _run(["git", "checkout", branch], cwd=tmpdir)
            _run(["git", "pull", "--ff-only", "origin", branch], cwd=tmpdir, timeout=600)
        else:
            _run(["git", "checkout", "-b", branch], cwd=tmpdir)

        workflows_dst = tmpdir / ".github" / "workflows"
        workflows_dst.mkdir(parents=True, exist_ok=True)

        # Copy files (flat) from artifacts_dir into workflows dir.
        # We only copy *.yml/*.yaml and *_migration_notes.md.
        copied = 0
        for p in artifacts_dir.glob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in {".yml", ".yaml"} or p.name.endswith("_migration_notes.md"):
                (workflows_dst / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
                copied += 1

        if copied == 0:
            return GitCommitResult(
                repo_dir=str(tmpdir),
                branch=branch,
                committed=False,
                commit_sha=None,
                files_added=0,
                files_updated=0,
                message=f"No workflow artifacts found in {artifacts_dir}",
            )

        _run(["git", "add", ".github/workflows"], cwd=tmpdir)

        status = _run(["git", "status", "--porcelain"], cwd=tmpdir)
        if not status:
            return GitCommitResult(
                repo_dir=str(tmpdir),
                branch=branch,
                committed=False,
                commit_sha=None,
                files_added=0,
                files_updated=0,
                message="No changes to commit",
            )

        # Rough counts
        files_added = sum(1 for line in status.splitlines() if line.startswith("A "))
        files_updated = sum(1 for line in status.splitlines() if line[0] == "M" or line.startswith(" M"))

        _run(["git", "commit", "-m", commit_message], cwd=tmpdir)
        sha = _run(["git", "rev-parse", "HEAD"], cwd=tmpdir)

        _run(["git", "push", "origin", f"HEAD:{branch}"], cwd=tmpdir, timeout=900)

        return GitCommitResult(
            repo_dir=str(tmpdir),
            branch=branch,
            committed=True,
            commit_sha=sha,
            files_added=files_added,
            files_updated=files_updated,
            message="Committed and pushed workflows",
        )
    finally:
        # Cleanup best effort on Windows as well.
        try:
            for root, dirs, files in os.walk(tmpdir, topdown=False):
                for name in files:
                    try:
                        os.chmod(Path(root) / name, 0o666)
                    except Exception:
                        pass
        except Exception:
            pass
        # Don't delete tmpdir if caller wants to inspect; keep by default? Here we delete.
        # If you want to keep it for debugging, change this behavior.
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
