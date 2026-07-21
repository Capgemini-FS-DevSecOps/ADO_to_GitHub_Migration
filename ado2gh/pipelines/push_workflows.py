"""Push generated workflow YAML to destination GitHub repos."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import requests

from ado2gh.clients.gh_client import GHClient
from ado2gh.logging_config import console
from ado2gh.models import RepoConfig


def remote_workflow_files(
    gh: GHClient,
    repo: RepoConfig,
    branch: str,
) -> list[str]:
    """Return workflow filenames on GitHub for the given branch, if any."""
    try:
        gh.get_branch_sha(repo.gh_org, repo.gh_repo, branch)
    except Exception:
        return []
    entries = gh.list_directory(
        repo.gh_org, repo.gh_repo, ".github/workflows", branch,
    )
    return sorted(
        e["name"] for e in entries
        if e.get("type") == "file"
        and str(e.get("name", "")).endswith((".yml", ".yaml"))
    )


def push_repo_workflows(
    gh: GHClient,
    repo: RepoConfig,
    workflows_dir: str,
    *,
    branch: str = "ado2gh/migrated-workflows",
    base: str | None = None,
    pr_title: str = "Add migrated GitHub Actions workflows",
    dry_run: bool = False,
    readiness_ok: bool = True,
    approver_ok: bool = False,
) -> dict[str, Any]:
    """Push workflow YAML for one repo. Returns structured result for UI/logging."""
    wf_root = Path(workflows_dir) / repo.gh_org / repo.gh_repo / ".github" / "workflows"
    result: dict[str, Any] = {
        "pushed": False,
        "repo": f"{repo.gh_org}/{repo.gh_repo}",
        "workflow_branch": branch,
        "workflow_files": [],
        "pr_url": "",
        "error": "",
        "local_dir": str(wf_root),
    }
    if not dry_run and not readiness_ok:
        result["error"] = "workflow readiness check failed"
        return result
    if not dry_run and not approver_ok:
        result["error"] = "live workflow push requires approval"
        return result
    if not wf_root.exists():
        result["error"] = f"no local workflows at {wf_root}"
        return result

    wf_files = sorted(list(wf_root.glob("*.yml")) + list(wf_root.glob("*.yaml")))
    result["workflow_files"] = [f.name for f in wf_files]
    if not wf_files:
        result["error"] = f"no workflow YAML in {wf_root}"
        return result

    console.print(
        f"\n[bold]{repo.gh_org}/{repo.gh_repo}[/bold] ({len(wf_files)} workflow(s))",
    )
    for f in wf_files:
        console.print(f"  - {f.name}")
    if dry_run:
        console.print("[yellow]  [DRY RUN] not pushed[/yellow]")
        result["pushed"] = False
        result["dry_run"] = True
        return result

    try:
        base_branch = base or gh.get_default_branch(repo.gh_org, repo.gh_repo)
        try:
            base_sha = gh.get_branch_sha(repo.gh_org, repo.gh_repo, base_branch)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 409:
                result["error"] = (
                    f"destination repo is empty (no commits on {base_branch}); "
                    "run repository migration first"
                )
                return result
            raise
        try:
            gh.create_branch(repo.gh_org, repo.gh_repo, branch, base_sha)
            console.print(
                f"[green]  branch {branch} created off {base_branch}@{base_sha[:7]}[/green]",
            )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 422:
                console.print(f"[yellow]  branch {branch} already exists; updating[/yellow]")
            else:
                raise

        for f in wf_files:
            rel_path = f".github/workflows/{f.name}"
            existing_sha = gh.get_file_sha(repo.gh_org, repo.gh_repo, rel_path, branch)
            content_b64 = base64.b64encode(f.read_bytes()).decode("ascii")
            gh.put_file(
                repo.gh_org, repo.gh_repo, rel_path, content_b64, branch,
                f"Add migrated workflow: {f.name}", sha=existing_sha,
            )
            console.print(f"  pushed {rel_path}")

        pr_body = (
            "Auto-generated GitHub Actions workflows migrated from ADO "
            "pipelines via `ado2gh`.\n\n"
            "**Review needed** — generated YAML may contain `TODO` placeholders."
        )
        try:
            pr = gh.create_pull_request(
                repo.gh_org, repo.gh_repo, pr_title, pr_body,
                head=branch, base=base_branch,
            )
            result["pr_url"] = pr.get("html_url", "")
            console.print(f"[green]  PR opened: {result['pr_url']}[/green]")
            result["pushed"] = True
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 422:
                console.print(
                    f"[yellow]  PR already open for {branch} -> {base_branch}[/yellow]",
                )
                result["pushed"] = True
            else:
                raise
    except Exception as exc:
        result["error"] = str(exc)
        console.print(f"[red]  failed: {exc}[/red]")

    return result


def push_workflows_for_repos(
    gh: GHClient,
    repos: list[RepoConfig],
    workflows_dir: str,
    branch: str = "ado2gh/migrated-workflows",
    base: str | None = None,
    pr_title: str = "Add migrated GitHub Actions workflows",
    dry_run: bool = False,
    readiness_ok: bool = True,
    approver_ok: bool = False,
) -> int:
    """Commit local workflow YAML to destination repos. Returns count pushed."""
    pushed_repos = 0
    for r in repos:
        outcome = push_repo_workflows(
            gh, r, workflows_dir,
            branch=branch, base=base, pr_title=pr_title,
            dry_run=dry_run, readiness_ok=readiness_ok, approver_ok=approver_ok,
        )
        if outcome.get("pushed"):
            pushed_repos += 1
    return pushed_repos
