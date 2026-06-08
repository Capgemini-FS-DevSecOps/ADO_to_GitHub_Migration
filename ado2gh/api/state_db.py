"""Shared state DB accessor for API modules."""
from __future__ import annotations

from ado2gh.api.settings_store import SettingsStore
from ado2gh.state.factory import create_state_db


def get_state_db(db_path: str | None = None):
    """Return state DB using explicit path, UI settings, or environment."""
    if db_path:
        from ado2gh.state.db import StateDB
        return StateDB(db_path)
    adv = SettingsStore().load().advanced
    return create_state_db(adv.db_path)
