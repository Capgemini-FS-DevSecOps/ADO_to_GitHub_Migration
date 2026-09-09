"""Wiki export scope."""
from __future__ import annotations

import re
from pathlib import Path

from ado2gh.core.scopes.base import ScopeContext, ScopeResult
from ado2gh.models import ExecutionMode, MigrationScope, RepoConfig
from ado2gh.output_dirs import output_base


class WikiScopeHandler:
    """Export the project's Azure DevOps wiki pages to local markdown files."""

    scope = MigrationScope.WIKI.value

    def migrate(self, repo: RepoConfig, ctx: ScopeContext, **kwargs: object) -> ScopeResult:  # noqa: ARG002 - shared scope-handler signature (FR-011)
        """Write every wiki page, and its sub-pages, under the run's output directory.

        Args:
            repo: Repository whose project wiki is being exported.
            ctx: Shared clients, state store and execution mode.
            **kwargs: Per-dispatch extras from `MigrationEngine`; unused here.

        Returns:
            A `ScopeResult` counting the wikis found and the pages written. In
            `ExecutionMode.DRY_RUN` nothing is written and the stats carry
            `dry_run: True`.
        """
        wikis = ctx.ado.list_wiki_pages(repo.ado_project)
        stats = {"wiki_count": len(wikis), "pages": 0}
        if ctx.mode is ExecutionMode.DRY_RUN:
            stats["dry_run"] = True
            return ScopeResult(stats=stats)

        out = output_base() / "wikis" / repo.gh_org / repo.gh_repo
        out.mkdir(parents=True, exist_ok=True)
        for wiki_data in wikis:
            root = wiki_data.get("root", {})
            self._write_page(root, out, stats)
        return ScopeResult(stats=stats)

    def _write_page(self, page: dict, parent: Path, stats: dict) -> None:
        """Write one wiki page and recurse into its sub-pages.

        Args:
            page: Wiki page payload from the Azure DevOps API.
            parent: Directory the page's markdown file is written into.
            stats: Counter dict the page count is accumulated in.
        """
        title = (page.get("path", "/Home").split("/")[-1]) or "Home"
        safe = re.sub(r"[^a-zA-Z0-9\-_. ]", "_", title)
        (parent / f"{safe}.md").write_text(
            page.get("content", f"# {title}\n"), encoding="utf-8",
        )
        stats["pages"] += 1
        sub_dir = parent / safe
        for sub in page.get("subPages", []):
            sub_dir.mkdir(exist_ok=True)
            self._write_page(sub, sub_dir, stats)
