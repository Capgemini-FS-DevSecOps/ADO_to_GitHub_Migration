"""Discovery scanner — enumerate ADO org and output structured CSV/JSON inventory.

The discovery output is designed for humans to review and select repos for migration.
Users copy the repos they want into an input file (in/repos.txt or in/repos.csv).
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ado2gh.clients import ADOClient
from ado2gh.logging_config import console, log
from ado2gh.models import PipelineMetadata, PipelineType
from ado2gh.state.db import StateDB


class DiscoveryScanner:
    """Scan an Azure DevOps organisation and output actionable discovery reports.

    Outputs:
    - repos.csv          — one row per repo (for user to select from)
    - pipelines.csv      — one row per pipeline (linked to repos)
    - discovery.json     — full JSON inventory
    - repos_template.txt — pre-formatted input file template with all discovered repos
    """

    def __init__(self, ado: ADOClient, db: StateDB = None):
        self.ado = ado
        self.db = db

    def scan(self, output_dir: str = "output/discovery") -> dict:
        """Run full discovery scan and write reports.

        Returns summary dict with counts.
        """
        log.info("Starting ADO discovery scan...")
        start = time.monotonic()

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        projects = self.ado.list_projects()
        log.info("Found %d projects", len(projects))

        all_repos: list[dict] = []
        all_pipelines: list[dict] = []

        for proj in projects:
            proj_name = proj.get("name", "")
            console.print(f"  Scanning [cyan]{proj_name}[/cyan]...")

            repos = self.ado.list_repos(proj_name)
            for repo in repos:
                repo_id = repo.get("id", "")
                repo_name = repo.get("name", "")
                default_branch = (repo.get("defaultBranch", "refs/heads/main")
                                  .replace("refs/heads/", ""))

                # Branch count
                try:
                    stats = self.ado.get_repo_stats(proj_name, repo_id)
                    branch_count = stats.get("branch_count", 0)
                except Exception:
                    branch_count = 0

                # Last commit
                last_commit_date = ""
                try:
                    commits = self.ado.get_repo_commits(proj_name, repo_id, top=1)
                    if commits:
                        last_commit_date = (commits[0].get("author", {}).get("date", "")
                                            or commits[0].get("committer", {}).get("date", ""))
                except Exception:
                    pass

                all_repos.append({
                    "project": proj_name,
                    "repo_name": repo_name,
                    "repo_id": repo_id,
                    "size_kb": repo.get("size", 0),
                    "default_branch": default_branch,
                    "branch_count": branch_count,
                    "last_commit_date": last_commit_date[:10] if last_commit_date else "",
                    "is_disabled": repo.get("isDisabled", False),
                })

            # Pipelines
            try:
                for pipe in self.ado.list_all_pipelines(proj_name):
                    defn = self.ado.get_build_definition_full(proj_name, pipe["id"])
                    pipe_repo = defn.get("repository", {})
                    process_type = defn.get("process", {}).get("type", 1)
                    all_pipelines.append({
                        "project": proj_name,
                        "pipeline_id": pipe["id"],
                        "pipeline_name": pipe.get("name", ""),
                        "pipeline_type": "yaml" if process_type == 2 else "classic",
                        "repo_name": pipe_repo.get("name", ""),
                        "folder": pipe.get("folder", ""),
                        "yaml_path": defn.get("process", {}).get("yamlFilename", ""),
                    })
            except Exception as exc:
                log.warning("Pipeline scan failed for %s: %s", proj_name, exc)

            # Release pipelines
            try:
                for rel in self.ado.list_all_release_pipelines(proj_name):
                    envs = rel.get("environments", [])
                    all_pipelines.append({
                        "project": proj_name,
                        "pipeline_id": rel["id"],
                        "pipeline_name": rel.get("name", ""),
                        "pipeline_type": "release",
                        "repo_name": "",
                        "folder": rel.get("path", ""),
                        "yaml_path": "",
                        "environments": ";".join(e.get("name", "") for e in envs),
                    })
            except Exception as exc:
                log.warning("Release pipeline scan failed for %s: %s", proj_name, exc)

        elapsed = round(time.monotonic() - start, 1)

        # ── Write outputs ────────────────────────────────────────────────────

        # repos.csv
        self._write_repos_csv(all_repos, out / "repos.csv")

        # pipelines.csv
        self._write_pipelines_csv(all_pipelines, out / "pipelines.csv")

        # discovery.json (full detail)
        (out / "discovery.json").write_text(json.dumps({
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_sec": elapsed,
            "summary": {
                "projects": len(projects),
                "repos": len(all_repos),
                "pipelines": len(all_pipelines),
            },
            "repos": all_repos,
            "pipelines": all_pipelines,
        }, indent=2, default=str))

        # repos_template.txt — input file template with all repos (commented out)
        self._write_input_template(all_repos, out / "repos_template.txt")

        stats = {
            "projects": len(projects),
            "repos": len(all_repos),
            "pipelines": len(all_pipelines),
            "elapsed_sec": elapsed,
        }

        console.print(f"\n[bold green]Discovery complete[/bold green]")
        console.print(f"  Projects:  {stats['projects']}")
        console.print(f"  Repos:     {stats['repos']}")
        console.print(f"  Pipelines: {stats['pipelines']}")
        console.print(f"  Duration:  {elapsed}s")
        console.print(f"\n  Output: [bold]{out}[/bold]")
        console.print(f"    repos.csv           — review and select repos")
        console.print(f"    pipelines.csv       — pipeline inventory")
        console.print(f"    repos_template.txt  — copy to in/repos.txt, uncomment repos to migrate")
        console.print(f"    discovery.json      — full JSON detail")

        return stats

    def _write_repos_csv(self, repos: list[dict], path: Path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "project", "repo_name", "size_kb", "default_branch",
                "branch_count", "last_commit_date", "is_disabled",
            ])
            writer.writeheader()
            for r in sorted(repos, key=lambda x: (x["project"], x["repo_name"])):
                writer.writerow({k: r.get(k, "") for k in writer.fieldnames})

    def _write_pipelines_csv(self, pipelines: list[dict], path: Path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "project", "pipeline_id", "pipeline_name", "pipeline_type",
                "repo_name", "folder", "yaml_path",
            ])
            writer.writeheader()
            for p in sorted(pipelines, key=lambda x: (x["project"], x["pipeline_name"])):
                writer.writerow({k: p.get(k, "") for k in writer.fieldnames})

    def _write_input_template(self, repos: list[dict], path: Path):
        """Write a repos.txt template with all discovered repos commented out."""
        lines = [
            "# ado2gh — Repo Input File (generated by discover)",
            "# Uncomment the repos you want to migrate.",
            "# Format: project/repo",
            "#",
        ]
        for r in sorted(repos, key=lambda x: (x["project"], x["repo_name"])):
            if r.get("is_disabled"):
                lines.append(f"# [DISABLED] {r['project']}/{r['repo_name']}")
            else:
                lines.append(f"# {r['project']}/{r['repo_name']}")

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
