"""Branch policy -> GitHub branch protection scope."""
from __future__ import annotations

from typing import Any

from ado2gh.core.scopes.base import ScopeContext, ScopeResult
from ado2gh.logging_config import log
from ado2gh.models import MigrationScope, RepoConfig


class BranchPoliciesScopeHandler:
    scope = MigrationScope.BRANCH_POLICIES.value

    def migrate(self, repo: RepoConfig, ctx: ScopeContext, **kwargs: Any) -> ScopeResult:
        source = ctx.ado.get_repo(repo.ado_project, repo.ado_repo)
        repo_id = source.get("id", "")
        policies = ctx.ado.list_branch_policies(repo.ado_project, repo_id)
        stats = {"policies_found": len(policies), "rules_created": 0}

        if ctx.dry_run:
            stats["dry_run"] = True
            return ScopeResult(stats=stats)

        for policy in policies:
            settings = policy.get("settings", {})
            for scope_entry in settings.get("scope", []):
                branch = scope_entry.get("refName", "").replace("refs/heads/", "")
                if not branch:
                    continue
                try:
                    reviewers = settings.get("minimumApproverCount", 1)
                    ctx.gh.set_branch_protection(
                        repo.gh_org, repo.gh_repo, branch,
                        required_reviewers=reviewers,
                    )
                    stats["rules_created"] += 1
                except Exception as exc:
                    log.warning("branch protection for %s failed: %s", branch, exc)

        return ScopeResult(stats=stats)
