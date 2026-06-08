"""Factory for migration state store — backend selected via environment."""
from __future__ import annotations

import os
from typing import Union

from ado2gh.infra.state.storage_config import StorageBackend, StorageConfig
from ado2gh.state.db import StateDB

StateStore = Union[StateDB, "PostgresStateDB", "DynamoDBStateDB"]


def create_state_db(db_path: str = "migration_state.db") -> StateStore:
    """Return the configured state store.

    Local / development (default): SQLite file.
    Production: PostgreSQL or DynamoDB via ``ADO2GH_STORAGE_BACKEND``.
    """
    cfg = StorageConfig.from_env(sqlite_default=db_path)

    if cfg.backend == StorageBackend.SQLITE:
        return StateDB(cfg.sqlite_path)

    if cfg.backend == StorageBackend.POSTGRES:
        from ado2gh.state.postgres_db import PostgresStateDB
        return PostgresStateDB(cfg.database_url)

    if cfg.backend == StorageBackend.DYNAMODB:
        from ado2gh.state.dynamodb_db import DynamoDBStateDB
        endpoint = os.environ.get("ADO2GH_DYNAMODB_ENDPOINT")
        return DynamoDBStateDB(cfg.dynamodb_table, region=cfg.aws_region, endpoint_url=endpoint)

    raise ValueError(f"Unsupported storage backend: {cfg.backend}")
