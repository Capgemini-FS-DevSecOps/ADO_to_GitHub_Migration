"""Push generated workflow YAML to destination GitHub repos."""
from __future__ import annotations

import base64
from pathlib import Path

import requests

from ado2gh.clients.gh_client import GHClient
from ado2gh.logging_config import console
from ado2gh.models import RepoConfig


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
    if not dry_run and not readiness_ok:
        console.print("[red]push_workflows blocked: workflow readiness failed[/red]")
        return 0
    if not dry_run and not approver_ok:
        console.print("[red]push_workflows blocked: Approver approval required[/red]")
        return 0
    pushed_repos = 0
    for r in repos:
        wf_root = Path(workflows_dir) / r.gh_org / r.gh_repo / ".github" / "workflows"
        if not wf_root.exists():
            console.print(
                f"[yellow]skip {r.gh_org}/{r.gh_repo}: no local workflows at {wf_root}[/yellow]"
            )
            continue
        wf_files = sorted(list(wf_root.glob("*.yml")) + list(wf_root.glob("*.yaml")))
        if not wf_files:
            console.print(
                f"[yellow]skip {r.gh_org}/{r.gh_repo}: no .yml files in {wf_root}[/yellow]"
            )
            continue

        console.print(f"\n[bold]{r.gh_org}/{r.gh_repo}[/bold] ({len(wf_files)} workflow(s))")
        for f in wf_files:
            console.print(f"  - {f.name}")
        if dry_run:
            console.print("[yellow]  [DRY RUN] not pushed[/yellow]")
            continue

        try:
            base_branch = base or gh.get_default_branch(r.gh_org, r.gh_repo)
            try:
                base_sha = gh.get_branch_sha(r.gh_org, r.gh_repo, base_branch)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 409:
                    console.print(
                        f"[red]  failed: destination repo {r.gh_org}/{r.gh_repo} "
                        f"is empty (no commits on {base_branch}). "
                        f"Run migration first.[/red]"
                    )
                    continue
                raise
            try:
                gh.create_branch(r.gh_org, r.gh_repo, branch, base_sha)
                console.print(
                    f"[green]  branch {branch} created off {base_branch}@{base_sha[:7]}[/green]"
                )
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 422:
                    console.print(f"[yellow]  branch {branch} already exists; updating[/yellow]")
                else:
                    raise

            for f in wf_files:
                rel_path = f".github/workflows/{f.name}"
                existing_sha = gh.get_file_sha(r.gh_org, r.gh_repo, rel_path, branch)
                content_b64 = base64.b64encode(f.read_bytes()).decode("ascii")
                gh.put_file(
                    r.gh_org, r.gh_repo, rel_path, content_b64, branch,
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
                    r.gh_org, r.gh_repo, pr_title, pr_body,
                    head=branch, base=base_branch,
                )
                console.print(f"[green]  PR opened: {pr['html_url']}[/green]")
                pushed_repos += 1
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 422:
                    console.print(f"[yellow]  PR already open for {branch} -> {base_branch}[/yellow]")
                    pushed_repos += 1
                else:
                    raise
        except Exception as exc:
            console.print(f"[red]  failed: {exc}[/red]")

    return pushed_repos
