from ado2gh.core.scopes.base import ScopeContext, ScopeHandler, ScopeResult
from ado2gh.core.scopes.registry import SCOPE_REGISTRY, get_scope_handler
from ado2gh.models import MigrationScope

DEFAULT_SCOPES = [s.value for s in MigrationScope]


def build_scope_registry():
    return SCOPE_REGISTRY


__all__ = [
    "ScopeContext",
    "ScopeHandler",
    "ScopeResult",
    "DEFAULT_SCOPES",
    "SCOPE_REGISTRY",
    "build_scope_registry",
    "get_scope_handler",
]
