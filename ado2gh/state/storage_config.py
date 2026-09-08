"""Storage backend selection: SQLite for local use, PostgreSQL or DynamoDB in production."""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class StorageBackend(str, Enum):
    """Persistence backends selectable through ``ADO2GH_STORAGE_BACKEND``."""

    SQLITE = "sqlite"
    POSTGRES = "postgres"
    DYNAMODB = "dynamodb"


@dataclass(frozen=True)
class StorageConfig:
    """Resolved storage settings; only the fields of the chosen backend are meaningful.

    ``database_url`` carries database credentials and must not be logged.
    """

    backend: StorageBackend
    sqlite_path: str
    database_url: str
    dynamodb_table: str
    aws_region: str

    @classmethod
    def from_env(cls, sqlite_default: str = "migration_state.db") -> StorageConfig:
        """Build the configuration from ``ADO2GH_*`` and ``AWS_*`` environment variables.

        Args:
            sqlite_default: SQLite path used when ``ADO2GH_SQLITE_PATH`` is unset.

        Raises:
            ValueError: The backend name is unknown, or the backend's required
                setting (``ADO2GH_DATABASE_URL`` or ``ADO2GH_DYNAMODB_TABLE``)
                is missing.
        """
        raw = (os.environ.get("ADO2GH_STORAGE_BACKEND") or "sqlite").strip().lower()
        try:
            backend = StorageBackend(raw)
        except ValueError as exc:
            raise ValueError(
                f"Invalid ADO2GH_STORAGE_BACKEND={raw!r}. "
                f"Use: sqlite, postgres, dynamodb"
            ) from exc

        sqlite_path = os.environ.get("ADO2GH_SQLITE_PATH", sqlite_default)
        database_url = os.environ.get("ADO2GH_DATABASE_URL", "")
        dynamodb_table = os.environ.get("ADO2GH_DYNAMODB_TABLE", "ado2gh")
        aws_region = os.environ.get(
            "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        )

        if backend == StorageBackend.POSTGRES and not database_url.startswith("postgres"):
            raise ValueError(
                "ADO2GH_STORAGE_BACKEND=postgres requires ADO2GH_DATABASE_URL "
                "(postgresql://...)"
            )

        if backend == StorageBackend.DYNAMODB and not dynamodb_table:
            raise ValueError(
                "ADO2GH_STORAGE_BACKEND=dynamodb requires ADO2GH_DYNAMODB_TABLE"
            )

        return cls(
            backend=backend,
            sqlite_path=sqlite_path,
            database_url=database_url,
            dynamodb_table=dynamodb_table,
            aws_region=aws_region,
        )
