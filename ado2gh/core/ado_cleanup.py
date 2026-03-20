"""Post-migration ADO cleanup — disable pipelines, archive repos, add redirect."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ado2gh.clients.ado_client import ADOClient
from ado2gh.logging_config import console, log
from ado2gh.models import RepoConfig
from ado2gh.state.db import StateDB


class ADOCleanup:
    """Post-migration cleanup actions on the ADO side.

    After successful migration + validation, this class can:
    1. Disable all build pipelines for migrated repos
    2. Add a redirect README to the ADO repo pointing to GitHub
    3. Archive (disable) the ADO repo to prevent further commits
    """

    def __init__(self, ado: ADOClient, db: StateDB, dry_run: bool = False):
        self.ado = ado
        self.db = db
        self.dry_run = dry_run

    def cleanup_repos(self, repos: list[RepoConfig],
                      disable_pipelines: bool = True,
                      add_redirect: bool = True,
                      archive_repo: bool = False,
                      max_workers: int = 4) -> list[dict]:
        """Run cleanup actions on migrated ADO repos.

        Args:
            repos: Repos to clean up (should be successfully migrated).
            disable_pipelines: Disable all build/release pipelines.
            add_redirect: Push a README pointing to the new GitHub repo.
            archive_repo: Disable the ADO repo (no further pushes).
            max_workers: Parallelism for cleanup operations.
        """
        results: list[dict] = []
        console.print(f"[bold]ADO Cleanup: {len(repos)} repos[/bold] "
                       f"[pipelines={'Y' if disable_pipelines else 'N'} "
                       f"redirect={'Y' if add_redirect else 'N'} "
                       f"archive={'Y' if archive_repo else 'N'}]"
                       f"{'  [DRY RUN]' if self.dry_run else ''}")

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    self._cleanup_one, repo,
                    disable_pipelines, add_redirect, archive_repo,
                ): repo
                for repo in repos
            }
            for future in as_completed(futures):
                repo = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    log.error("cleanup failed for %s/%s: %s",
                              repo.ado_project, repo.ado_repo, exc)
                    results.append({
                        "ado_project": repo.ado_project,
                        "ado_repo": repo.ado_repo,
                        "status": "error",
                        "error": str(exc),
                    })

        ok = sum(1 for r in results if r.get("status") == "completed")
        console.print(f"[bold]Cleanup complete:[/bold] {ok}/{len(results)} repos processed")
        return results

    def _cleanup_one(self, repo: RepoConfig,
                     disable_pipelines: bool,
                     add_redirect: bool,
                     archive_repo: bool) -> dict:
        result: dict[str, Any] = {
            "ado_project": repo.ado_project,
            "ado_repo": repo.ado_repo,
            "gh_target": f"{repo.gh_org}/{repo.gh_repo}",
            "status": "completed",
            "actions": {},
        }

        if disable_pipelines:
            result["actions"]["disable_pipelines"] = self._disable_pipelines(repo)

        if add_redirect:
            result["actions"]["redirect"] = self._add_redirect_readme(repo)

        if archive_repo:
            result["actions"]["archive"] = self._archive_repo(repo)

        return result

    def _disable_pipelines(self, repo: RepoConfig) -> dict:
        """Disable all build pipelines associated with this repo."""
        stats = {"disabled": 0, "failed": 0, "total": 0}

        pipelines = list(self.ado.list_all_pipelines(repo.ado_project))
        # Filter to pipelines that belong to this repo
        repo_pipelines = []
        for pipe in pipelines:
            try:
                defn = self.ado.get_build_definition_full(
                    repo.ado_project, pipe["id"])
                pipe_repo = defn.get("repository", {}).get("name", "")
                if pipe_repo == repo.ado_repo:
                    repo_pipelines.append(defn)
            except Exception:
                pass

        stats["total"] = len(repo_pipelines)

        if self.dry_run:
            stats["dry_run"] = True
            return stats

        for defn in repo_pipelines:
            try:
                # Disable by setting queueStatus to "disabled"
                pipe_id = defn.get("id", 0)
                url = (f"{self.ado.org_url}/{self.ado._p(repo.ado_project)}"
                       f"/_apis/build/definitions/{pipe_id}?{self.ado.API}")
                defn["queueStatus"] = "disabled"
                r = self.ado.session.put(url, json=defn, timeout=30)
                if r.ok:
                    stats["disabled"] += 1
                    log.info("Disabled pipeline: %s/%s (#%d)",
                             repo.ado_project, defn.get("name", ""), pipe_id)
                else:
                    stats["failed"] += 1
            except Exception as exc:
                log.warning("Failed to disable pipeline %d: %s",
                            defn.get("id", 0), exc)
                stats["failed"] += 1

        return stats

    def _add_redirect_readme(self, repo: RepoConfig) -> dict:
        """Push a MIGRATION_NOTICE.md to the ADO repo pointing to GitHub."""
        if self.dry_run:
            return {"dry_run": True}

        notice_content = (
            f"# Repository Migrated\n\n"
            f"This repository has been migrated to GitHub.\n\n"
            f"**New location:** https://github.com/{repo.gh_org}/{repo.gh_repo}\n\n"
            f"Please update your remotes:\n"
            f"```\n"
            f"git remote set-url origin https://github.com/{repo.gh_org}/{repo.gh_repo}.git\n"
            f"```\n\n"
            f"This ADO repository is now read-only.\n\n"
            f"_Migrated on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}_\n"
        )

        try:
            import base64
            source = self.ado.get_repo(repo.ado_project, repo.ado_repo)
            repo_id = source.get("id", "")
            default_branch = (source.get("defaultBranch", "refs/heads/main")
                              .replace("refs/heads/", ""))

            # Get the latest commit on default branch for the push
            url = (f"{self.ado.org_url}/{self.ado._p(repo.ado_project)}"
                   f"/_apis/git/repositories/{repo_id}/refs"
                   f"?filter=heads/{default_branch}&{self.ado.API}")
            refs = self.ado._get(url).get("value", [])
            if not refs:
                return {"status": "skipped", "reason": "no default branch ref"}

            old_object_id = refs[0].get("objectId", "")

            # Create a push with the new file
            push_body = {
                "refUpdates": [
                    {"name": f"refs/heads/{default_branch}",
                     "oldObjectId": old_object_id}
                ],
                "commits": [{
                    "comment": "Add migration notice — repo migrated to GitHub",
                    "changes": [{
                        "changeType": "add",
                        "item": {"path": "/MIGRATION_NOTICE.md"},
                        "newContent": {
                            "content": notice_content,
                            "contentType": "rawtext",
                        },
                    }],
                }],
            }

            push_url = (f"{self.ado.org_url}/{self.ado._p(repo.ado_project)}"
                        f"/_apis/git/repositories/{repo_id}/pushes?{self.ado.API}")
            r = self.ado.session.post(push_url, json=push_body, timeout=30)
            if r.ok:
                log.info("Added MIGRATION_NOTICE.md to %s/%s",
                         repo.ado_project, repo.ado_repo)
                return {"status": "added"}
            else:
                return {"status": "failed", "http_code": r.status_code}

        except Exception as exc:
            log.warning("redirect notice failed for %s/%s: %s",
                        repo.ado_project, repo.ado_repo, exc)
            return {"status": "error", "error": str(exc)}

    def _archive_repo(self, repo: RepoConfig) -> dict:
        """Disable the ADO repo to prevent further pushes."""
        if self.dry_run:
            return {"dry_run": True}

        try:
            source = self.ado.get_repo(repo.ado_project, repo.ado_repo)
            repo_id = source.get("id", "")

            url = (f"{self.ado.org_url}/{self.ado._p(repo.ado_project)}"
                   f"/_apis/git/repositories/{repo_id}?{self.ado.API}")
            r = self.ado.session.patch(
                url, json={"isDisabled": True}, timeout=30,
            )
            if r.ok:
                log.info("Archived (disabled) ADO repo: %s/%s",
                         repo.ado_project, repo.ado_repo)
                return {"status": "archived"}
            else:
                return {"status": "failed", "http_code": r.status_code}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
