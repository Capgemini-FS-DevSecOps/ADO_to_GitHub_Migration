"""Storage backend selection — SQLite (local/dev) vs PostgreSQL/DynamoDB (production)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class StorageBackend(str, Enum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"
    DYNAMODB = "dynamodb"


@dataclass(frozen=True)
class StorageConfig:
    backend: StorageBackend
    sqlite_path: str
    database_url: str
    dynamodb_table: str
    aws_region: str

    @classmethod
    def from_env(cls, sqlite_default: str = "migration_state.db") -> StorageConfig:
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

