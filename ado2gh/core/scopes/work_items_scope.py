"""Work items -> GitHub issues scope."""
from __future__ import annotations

from typing import Any

from ado2gh.core.scopes.base import ScopeContext, ScopeResult
from ado2gh.logging_config import log
from ado2gh.models import MigrationScope, RepoConfig


class WorkItemsScopeHandler:
    scope = MigrationScope.WORK_ITEMS.value

    def migrate(self, repo: RepoConfig, ctx: ScopeContext, **kwargs: Any) -> ScopeResult:
        log.info(
            "work_items: %s/%s -> %s/%s%s",
            repo.ado_project, repo.ado_repo, repo.gh_org, repo.gh_repo,
            " [DRY RUN]" if ctx.dry_run else "",
        )
        work_items = ctx.ado.list_work_items(repo.ado_project)
        stats = {"total": len(work_items), "created": 0, "skipped": 0}

        if ctx.dry_run:
            stats["dry_run"] = True
            return ScopeResult(stats=stats)

        wi_types = {
            wi.get("fields", {}).get("System.WorkItemType", "Task") for wi in work_items
        }
        for label in wi_types:
            ctx.gh.create_label(repo.gh_org, repo.gh_repo, f"ado:{label}")

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
                ctx.gh.create_issue(
                    repo.gh_org, repo.gh_repo, title,
                    body=body, labels=[f"ado:{wi_type}"],
                )
                stats["created"] += 1
            except Exception as exc:
                log.warning("work-item %s failed: %s", wi.get("id"), exc)
                stats["skipped"] += 1

        return ScopeResult(stats=stats)
