"""Factory for migration state store — backend selected via environment."""
from __future__ import annotations

from typing import TYPE_CHECKING, Union

from ado2gh.state.sqlite_db import SQLiteStateDB
from ado2gh.state.storage_config import StorageBackend, StorageConfig

if TYPE_CHECKING:
    from ado2gh.state.postgres_db import PostgresStateDB

StateStore = Union[SQLiteStateDB, "PostgresStateDB"]


def create_state_db(db_path: str = "migration_state.db") -> StateStore:
    """Return the configured state store.

    Local / development (default): SQLite file.
    Production: PostgreSQL via ``ADO2GH_STORAGE_BACKEND``.
    """
    cfg = StorageConfig.from_env(sqlite_default=db_path)

    if cfg.backend == StorageBackend.SQLITE:
        return SQLiteStateDB(cfg.sqlite_path)

    if cfg.backend == StorageBackend.POSTGRES:
        from ado2gh.state.postgres_db import PostgresStateDB
        return PostgresStateDB(cfg.database_url)

    raise ValueError(f"Unsupported storage backend: {cfg.backend}")
