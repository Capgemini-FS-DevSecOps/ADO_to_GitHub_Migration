"""Base types for migration scope handlers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ado2gh.models import RepoConfig


@dataclass
class ScopeContext:
    global_cfg: dict
    ado: Any
    gh: Any
    db: Any
    dry_run: bool = False
    strategy: str = "mirror"
    wave_id: int = 0
    pipeline_parallel: int = 8


@dataclass
class ScopeResult:
    stats: dict = field(default_factory=dict)
    failed: int = 0


class ScopeHandler(Protocol):
    scope: str

    def migrate(self, repo: RepoConfig, ctx: ScopeContext, **kwargs: Any) -> ScopeResult:
        ...
