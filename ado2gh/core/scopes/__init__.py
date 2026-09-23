"""Migration scope handlers and the registry that maps scope names to them."""
from ado2gh.core.scopes.base import ScopeContext, ScopeHandler, ScopeResult
from ado2gh.core.scopes.registry import SCOPE_REGISTRY, get_scope_handler
from ado2gh.models import MigrationScope

DEFAULT_SCOPES = [s.value for s in MigrationScope]


def build_scope_registry() -> dict[str, ScopeHandler]:
    """Return the registry mapping each scope name to its handler.

    Returns:
        The shared ``SCOPE_REGISTRY`` mapping, not a copy.
    """
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
