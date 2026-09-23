"""Work items -> GitHub issues scope."""
from __future__ import annotations

from ado2gh.core.scopes.base import ScopeContext, ScopeResult
from ado2gh.logging_config import log
from ado2gh.models import ExecutionMode, MigrationScope, RepoConfig


class WorkItemsScopeHandler:
    """Copy Azure DevOps work items into the target repository as GitHub issues."""

    scope = MigrationScope.WORK_ITEMS.value

    def migrate(self, repo: RepoConfig, ctx: ScopeContext, **kwargs: object) -> ScopeResult:  # noqa: ARG002 - shared scope-handler signature (FR-011)
        """Create one GitHub issue per work item, plus a label per work-item type.

        There is no de-duplication: nothing here consults the state store or
        the issues already on the repository, so every run creates the full set
        again. Running this scope twice leaves one duplicate GitHub issue per
        Azure DevOps work item, and label creation is retried the same way.

        Args:
            repo: Repository whose work items are being migrated.
            ctx: Shared clients, state store and execution mode.
            **kwargs: Per-dispatch extras from `MigrationEngine`; unused here.

        Returns:
            A `ScopeResult` counting the work items found, created and skipped.
            In `ExecutionMode.DRY_RUN` nothing is written and the stats carry
            `dry_run: True`.
        """
        log.info(
            "work_items: %s/%s -> %s/%s%s",
            repo.ado_project, repo.ado_repo, repo.gh_org, repo.gh_repo,
            " [DRY RUN]" if ctx.mode is ExecutionMode.DRY_RUN else "",
        )
        work_items = ctx.ado.list_work_items(repo.ado_project)
        stats = {"total": len(work_items), "created": 0, "skipped": 0}

        if ctx.mode is ExecutionMode.DRY_RUN:
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
