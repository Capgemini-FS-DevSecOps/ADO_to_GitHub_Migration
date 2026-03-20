"""Post-migration validation — content-level comparison between ADO source and GitHub target.

v5.1: Compares commit SHAs (not just counts), default branch verification,
and generates actionable pass/fail report.
"""
from __future__ import annotations

import csv
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ado2gh.clients.ado_client import ADOClient
from ado2gh.clients.gh_client import GHClient
from ado2gh.logging_config import console, log
from ado2gh.models import RepoConfig
from ado2gh.state.db import StateDB

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


class PostMigrationValidator:
    """Content-level comparison between ADO source and GitHub target.

    Checks:
    1. Repo exists on GitHub
    2. Default branch matches and has same HEAD commit SHA
    3. Branch count comparison
    4. Latest commit SHA match (proves code actually transferred)
    5. Pipeline workflow files present
    6. Branch protection rules applied
    """

    def __init__(self, ado: ADOClient, gh: GHClient, db: StateDB):
        self.ado = ado
        self.gh = gh
        self.db = db

    def validate(self, repos: list[RepoConfig],
                 output_path: str = None,
                 max_workers: int = 6) -> list[dict]:
        results: list[dict] = []

        console.print(f"[bold]Validating {len(repos)} repos...[/bold]")

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._validate_one, repo): repo
                for repo in repos
            }
            for future in as_completed(futures):
                repo = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    log.error("Validation error %s/%s: %s",
                              repo.ado_project, repo.ado_repo, exc)
                    results.append({
                        "ado_project": repo.ado_project,
                        "ado_repo": repo.ado_repo,
                        "gh_target": f"{repo.gh_org}/{repo.gh_repo}",
                        "overall": FAIL,
                        "error": str(exc),
                        "checks": {},
                    })

        results.sort(key=lambda r: (r.get("ado_project", ""), r.get("ado_repo", "")))

        if output_path:
            self._write_report(results, output_path)

        return results

    def _validate_one(self, repo: RepoConfig) -> dict:
        result: dict[str, Any] = {
            "ado_project": repo.ado_project,
            "ado_repo": repo.ado_repo,
            "gh_target": f"{repo.gh_org}/{repo.gh_repo}",
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "overall": PASS,
            "checks": {},
        }

        checks = result["checks"]

        # 1. Does the GH repo exist?
        checks["repo_exists"] = self._check_repo_exists(repo)

        if checks["repo_exists"]["verdict"] == FAIL:
            result["overall"] = FAIL
            return result

        # 2. Default branch match + HEAD commit SHA
        checks["default_branch"] = self._check_default_branch(repo)

        # 3. HEAD commit SHA match (the real proof)
        checks["head_commit"] = self._check_head_commit_sha(repo)

        # 4. Branch count
        checks["branches"] = self._check_branch_count(repo)

        # 5. Workflows present (if pipelines scope was migrated)
        if "pipelines" in (repo.scopes or []):
            checks["workflows"] = self._check_workflows(repo)

        # 6. Branch protection (if branch_policies scope was migrated)
        if "branch_policies" in (repo.scopes or []):
            checks["branch_protection"] = self._check_branch_protection(repo)

        # Derive overall
        verdicts = [c["verdict"] for c in checks.values()]
        if FAIL in verdicts:
            result["overall"] = FAIL
        elif WARN in verdicts:
            result["overall"] = WARN

        return result

    def _check_repo_exists(self, repo: RepoConfig) -> dict:
        try:
            exists = self.gh.repo_exists(repo.gh_org, repo.gh_repo)
            return {
                "verdict": PASS if exists else FAIL,
                "detail": "GitHub repo exists" if exists else "GitHub repo NOT FOUND",
            }
        except Exception as exc:
            return {"verdict": FAIL, "detail": f"Error checking repo: {exc}"}

    def _check_default_branch(self, repo: RepoConfig) -> dict:
        """Verify default branch name matches between ADO and GH."""
        try:
            ado_repo = self.ado.get_repo(repo.ado_project, repo.ado_repo)
            ado_default = (ado_repo.get("defaultBranch", "refs/heads/main")
                           .replace("refs/heads/", ""))
        except Exception:
            return {"verdict": WARN, "detail": "Cannot read ADO default branch",
                    "ado_branch": None, "gh_branch": None}

        try:
            gh_repo = self.gh.get_repo(repo.gh_org, repo.gh_repo)
            gh_default = gh_repo.get("default_branch", "main")
        except Exception:
            return {"verdict": WARN, "detail": "Cannot read GH default branch",
                    "ado_branch": ado_default, "gh_branch": None}

        match = ado_default == gh_default
        return {
            "verdict": PASS if match else WARN,
            "ado_branch": ado_default,
            "gh_branch": gh_default,
            "detail": ("Default branch matches" if match
                       else f"Branch mismatch: ADO={ado_default}, GH={gh_default}"),
        }

    def _check_head_commit_sha(self, repo: RepoConfig) -> dict:
        """Compare the HEAD commit SHA of the default branch — proves code transferred."""
        try:
            ado_repo = self.ado.get_repo(repo.ado_project, repo.ado_repo)
            ado_default = (ado_repo.get("defaultBranch", "refs/heads/main")
                           .replace("refs/heads/", ""))
            repo_id = ado_repo.get("id", "")

            commits = self.ado.get_repo_commits(repo.ado_project, repo_id, top=1)
            ado_sha = commits[0].get("commitId", "") if commits else ""
        except Exception:
            return {"verdict": WARN, "detail": "Cannot read ADO HEAD commit",
                    "ado_sha": None, "gh_sha": None}

        try:
            gh_branches = self.gh.list_branches(repo.gh_org, repo.gh_repo)
            gh_sha = ""
            for b in gh_branches:
                if b.get("name") == ado_default:
                    gh_sha = b.get("commit", {}).get("sha", "")
                    break
        except Exception:
            return {"verdict": WARN, "detail": "Cannot read GH HEAD commit",
                    "ado_sha": ado_sha, "gh_sha": None}

        if not ado_sha:
            return {"verdict": WARN, "detail": "Empty ADO repo",
                    "ado_sha": "", "gh_sha": gh_sha}

        match = ado_sha == gh_sha
        return {
            "verdict": PASS if match else FAIL,
            "ado_sha": ado_sha[:12],
            "gh_sha": gh_sha[:12] if gh_sha else "",
            "detail": ("HEAD commit SHA matches — code verified" if match
                       else f"SHA MISMATCH: ADO={ado_sha[:12]} GH={gh_sha[:12] if gh_sha else 'missing'}"),
        }

    def _check_branch_count(self, repo: RepoConfig) -> dict:
        try:
            ado_repo = self.ado.get_repo(repo.ado_project, repo.ado_repo)
            ado_stats = self.ado.get_repo_stats(
                repo.ado_project, ado_repo.get("id", ""))
            ado_count = ado_stats.get("branch_count", 0)
        except Exception:
            ado_count = -1

        try:
            gh_branches = self.gh.list_branches(repo.gh_org, repo.gh_repo)
            gh_count = len(gh_branches)
        except Exception:
            gh_count = -1

        if ado_count < 0 or gh_count < 0:
            return {"verdict": WARN, "ado_count": ado_count, "gh_count": gh_count,
                    "detail": "Cannot retrieve branch count"}
        if gh_count >= ado_count:
            return {"verdict": PASS, "ado_count": ado_count, "gh_count": gh_count,
                    "detail": "All branches present"}
        if gh_count >= ado_count * 0.9:
            return {"verdict": WARN, "ado_count": ado_count, "gh_count": gh_count,
                    "detail": f"{ado_count - gh_count} branches missing (minor)"}
        return {"verdict": FAIL, "ado_count": ado_count, "gh_count": gh_count,
                "detail": f"{ado_count - gh_count} branches missing"}

    def _check_workflows(self, repo: RepoConfig) -> dict:
        ado_count = self.db.inventory_count_for_repo(repo.ado_project, repo.ado_repo)
        try:
            gh_workflows = self.gh.list_workflows(repo.gh_org, repo.gh_repo)
            gh_count = len(gh_workflows)
        except Exception:
            gh_count = -1

        if ado_count == 0:
            return {"verdict": PASS, "ado_pipelines": 0, "gh_workflows": gh_count,
                    "detail": "No pipelines to convert"}
        if gh_count < 0:
            return {"verdict": WARN, "ado_pipelines": ado_count, "gh_workflows": -1,
                    "detail": "Cannot read GH workflows"}
        if gh_count >= ado_count:
            return {"verdict": PASS, "ado_pipelines": ado_count,
                    "gh_workflows": gh_count,
                    "detail": "All pipelines have corresponding workflows"}
        return {"verdict": WARN, "ado_pipelines": ado_count, "gh_workflows": gh_count,
                "detail": f"{ado_count - gh_count} workflows missing"}

    def _check_branch_protection(self, repo: RepoConfig) -> dict:
        try:
            gh_repo = self.gh.get_repo(repo.gh_org, repo.gh_repo)
            default_branch = gh_repo.get("default_branch", "main")
            # Try to read branch protection
            self.gh._get(
                f"/repos/{repo.gh_org}/{repo.gh_repo}/branches/{default_branch}/protection"
            )
            return {"verdict": PASS, "detail": "Branch protection configured"}
        except Exception:
            return {"verdict": WARN,
                    "detail": "No branch protection on default branch (may be intentional)"}

    def _write_report(self, results: list[dict], output_path: str):
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # CSV summary
        csv_path = str(out)
        if csv_path.endswith(".json"):
            csv_path = csv_path.replace(".json", ".csv")
        elif not csv_path.endswith(".csv"):
            csv_path += ".csv"

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "ado_project", "ado_repo", "gh_target", "overall",
                "repo_exists", "default_branch", "head_commit",
                "branches", "workflows", "branch_protection", "detail",
            ])
            for r in results:
                checks = r.get("checks", {})
                writer.writerow([
                    r.get("ado_project", ""),
                    r.get("ado_repo", ""),
                    r.get("gh_target", ""),
                    r.get("overall", ""),
                    checks.get("repo_exists", {}).get("verdict", ""),
                    checks.get("default_branch", {}).get("verdict", ""),
                    checks.get("head_commit", {}).get("verdict", ""),
                    checks.get("branches", {}).get("verdict", ""),
                    checks.get("workflows", {}).get("verdict", ""),
                    checks.get("branch_protection", {}).get("verdict", ""),
                    checks.get("head_commit", {}).get("detail", ""),
                ])

        # JSON detail
        json_path = csv_path.replace(".csv", ".json")
        Path(json_path).write_text(
            json.dumps(results, indent=2, default=str), encoding="utf-8"
        )

        log.info("Validation report: %s + %s", csv_path, json_path)

    def print_summary(self, results: list[dict]):
        from rich.table import Table
        from rich import box

        t = Table(title="Post-Migration Validation", box=box.ROUNDED)
        t.add_column("Repo", style="cyan", max_width=35)
        t.add_column("GH Target", style="green", max_width=30)
        t.add_column("Overall", width=8)
        t.add_column("Commit", width=10)
        t.add_column("Branches", width=10)
        t.add_column("Detail", overflow="fold", max_width=40)

        for r in results:
            checks = r.get("checks", {})
            overall = r.get("overall", "")
            color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}.get(overall, "white")

            commit_check = checks.get("head_commit", {})
            branch_check = checks.get("branches", {})

            t.add_row(
                r.get("ado_repo", ""),
                r.get("gh_target", ""),
                f"[{color}]{overall}[/{color}]",
                commit_check.get("verdict", "-"),
                branch_check.get("verdict", "-"),
                commit_check.get("detail", r.get("error", "")),
            )

        console.print(t)
        passed = sum(1 for r in results if r.get("overall") == PASS)
        total = len(results)
        console.print(f"\n[bold]{passed}/{total} repos passed validation[/bold]")
