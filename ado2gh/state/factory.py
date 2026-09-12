"""Factory for the migration state store; the backend is the environment's unless named."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Union

from ado2gh.state.sqlite_db import SQLiteStateDB
from ado2gh.state.storage_config import StorageBackend, StorageConfig

if TYPE_CHECKING:
    from ado2gh.state.postgres_db import PostgresStateDB

StateStore = Union[SQLiteStateDB, "PostgresStateDB"]

DEFAULT_SQLITE_PATH = "migration_state.db"

logger = logging.getLogger(__name__)


def create_state_db(
    db_path: str = DEFAULT_SQLITE_PATH,
    *,
    backend: StorageBackend | None = None,
) -> StateStore:
    """Return the state store for ``backend``, or for the configured one.

    Args:
        db_path: SQLite file to use. ``ADO2GH_SQLITE_PATH`` still outranks it
            (deployments set that variable to a mounted volume), and the
            Postgres backend cannot honour it at all — passing a non-default
            path under Postgres is logged as a warning rather than dropped in
            silence.
        backend: Backend to build. Omitted, it comes from
            ``ADO2GH_STORAGE_BACKEND`` via ``StorageConfig.from_env``.

    Returns:
        A ``SQLiteStateDB``, or a ``PostgresStateDB`` for ``StorageBackend.POSTGRES``.

    Raises:
        ValueError: ``StorageBackend.DYNAMODB`` was selected — DynamoDB backs
            the job store only, not the state store — or Postgres was selected
            without ``ADO2GH_DATABASE_URL``.
    """
    cfg = StorageConfig.from_env(sqlite_default=db_path)
    selected = backend or cfg.backend

    if selected == StorageBackend.SQLITE:
        return SQLiteStateDB(cfg.sqlite_path)

    if db_path != DEFAULT_SQLITE_PATH:
        logger.warning(
            "Ignoring db_path %r: ADO2GH_STORAGE_BACKEND selects the %s backend, "
            "which has no local database file.",
            db_path,
            selected.value,
        )

    if selected == StorageBackend.POSTGRES:
        if not cfg.database_url.startswith("postgres"):
            raise ValueError(
                "The postgres state store requires ADO2GH_DATABASE_URL (postgresql://...)"
            )
        from ado2gh.state.postgres_db import PostgresStateDB
        return PostgresStateDB(cfg.database_url)

    raise ValueError(
        f"ADO2GH_STORAGE_BACKEND={selected.value} has no state store. DynamoDB backs "
        "the job store only (JobStoreFactory); use sqlite or postgres for state."
    )
