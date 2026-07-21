"""Scope handler registry."""
from __future__ import annotations

from ado2gh.core.scopes.base import ScopeHandler
from ado2gh.core.scopes.branch_policies_scope import BranchPoliciesScopeHandler
from ado2gh.core.scopes.git_scope import GitScopeHandler
from ado2gh.core.scopes.pipelines_scope import PipelinesScopeHandler
from ado2gh.core.scopes.secrets_scope import SecretsScopeHandler
from ado2gh.core.scopes.wiki_scope import WikiScopeHandler
from ado2gh.core.scopes.work_items_scope import WorkItemsScopeHandler

_HANDLERS: list[ScopeHandler] = [
    GitScopeHandler(),
    WorkItemsScopeHandler(),
    PipelinesScopeHandler(),
    WikiScopeHandler(),
    SecretsScopeHandler(),
    BranchPoliciesScopeHandler(),
]

SCOPE_REGISTRY: dict[str, ScopeHandler] = {h.scope: h for h in _HANDLERS}


def get_scope_handler(scope: str) -> ScopeHandler | None:
    return SCOPE_REGISTRY.get(scope)
