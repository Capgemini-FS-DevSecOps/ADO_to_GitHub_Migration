"""Base types for migration scope handlers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from ado2gh.models import DEFAULT_MIGRATION_STRATEGY, ExecutionMode, RepoConfig

if TYPE_CHECKING:
    from ado2gh.clients import ADOClient, GHClient
    from ado2gh.state.base import StateDBBase


@dataclass
class ScopeContext:
    """Everything a scope handler needs that is not the repository itself.

    Attributes:
        global_cfg: Parsed `global` block of migration.yaml.
        ado: Azure DevOps client for the source organisation.
        gh: GitHub client for the target organisation.
        db: State store the handler records its rows against.
        mode: `ExecutionMode.DRY_RUN` (the default) previews without writing to either side;
            `ExecutionMode.LIVE` performs the migration (CA-001).
        strategy: Repository migration strategy, `mirror` or `gei`.
        wave_id: Wave the resulting rows belong to.
        pipeline_parallel: Worker count for the pipelines scope.
    """

    global_cfg: dict
    ado: ADOClient
    gh: GHClient
    db: StateDBBase
    mode: ExecutionMode = ExecutionMode.DRY_RUN
    strategy: str = DEFAULT_MIGRATION_STRATEGY
    wave_id: int = 0
    pipeline_parallel: int = 8


@dataclass
class ScopeResult:
    """Outcome of one scope handler run.

    Attributes:
        stats: Handler-specific counters and messages, surfaced to the caller.
        failed: Number of items that failed; zero means the scope succeeded.
    """

    stats: dict = field(default_factory=dict)
    failed: int = 0


class ScopeHandler(Protocol):
    """The single signature every scope handler in this package shares (FR-011)."""

    scope: str

    def migrate(self, repo: RepoConfig, ctx: ScopeContext, **kwargs: object) -> ScopeResult:
        """Migrate one scope of one repository.

        This protocol imposes no idempotency requirement, and `MigrationEngine`
        dispatches here whether or not the state store already holds a
        completed row for the scope. A handler that would do damage on a second
        run has to guard itself — and they do not agree today: the pipelines
        handler de-duplicates against the state store, the git handler
        re-force-pushes under the mirror strategy, and the work-items handler
        duplicates every issue. Say which one a new handler is in its own
        docstring (GAP-027).

        Args:
            repo: Repository to migrate.
            ctx: Shared clients, state store and execution mode.
            **kwargs: Optional per-dispatch extras. `MigrationEngine` passes
                `concurrency`, and for the pipelines scope also
                `pipeline_parallel` and `wave_id`; handlers that do not need
                them ignore the mapping.

        Returns:
            A `ScopeResult` carrying the handler's stats and failure count.
        """
        ...
