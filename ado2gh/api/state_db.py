"""Shared state DB accessor for API modules."""
from __future__ import annotations

from ado2gh.api.settings_store import SettingsStore
from ado2gh.state.factory import StateStore, create_state_db


def get_state_db(db_path: str | None = None) -> StateStore:
    """Open the migration state store for API modules.

    Args:
        db_path: SQLite path to use. When omitted, the path saved in the UI's
            advanced settings is used instead.

    Returns:
        The configured state store — a ``SQLiteStateDB`` by default, or a
        ``PostgresStateDB`` when the environment selects the Postgres backend.
        The resolved path only applies to the SQLite backend.
    """
    if db_path:
        return create_state_db(db_path)
    adv = SettingsStore().load().advanced
    return create_state_db(adv.db_path)
