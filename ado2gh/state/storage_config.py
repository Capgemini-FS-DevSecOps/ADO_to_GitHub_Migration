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


@dataclass(frozen=True)
class CheckpointStorageSettings:
    """Resolved settings for the LangGraph checkpoint store used by the agent graph.

    This is a separate store from the migration state database that
    :class:`StorageConfig` resolves: the checkpoint database keeps the agent
    conversation and plan-execute-validate loop (PEV) state, defaults to its
    own path, and is picked independently. The fields here mirror, one for
    one, the environment variables ``ado2gh/agents/migration_agent/graph/builder.py``
    reads directly today; the defaults are its current hardcoded literals.
    """

    backend: str
    """The lowercased ``ADO2GH_STORAGE_BACKEND`` value. ``"postgresql"`` and
    ``"postgres"`` both select PostgreSQL; anything else falls back to SQLite."""

    sqlite_path: str
    """Where the SQLite checkpoint database file lives."""

    database_url: str
    """A full PostgreSQL connection string. When empty, the individual
    ``pg_*`` fields are combined into one by :meth:`postgres_dsn`."""

    pg_host: str
    """PostgreSQL host, used only when ``database_url`` is empty."""

    pg_port: int
    """PostgreSQL port, used only when ``database_url`` is empty."""

    pg_user: str
    """PostgreSQL user, used only when ``database_url`` is empty."""

    pg_password: str
    """PostgreSQL password, used only when ``database_url`` is empty."""

    pg_dbname: str
    """PostgreSQL database name, used only when ``database_url`` is empty."""

    @classmethod
    def from_env(cls) -> CheckpointStorageSettings:
        """Resolve checkpoint storage settings from the environment.

        Reads exactly the variables the graph builder reads today
        (``ADO2GH_STORAGE_BACKEND``, ``ADO2GH_DATABASE_URL``,
        ``ADO2GH_SQLITE_PATH``, ``PGHOST``, ``PGPORT``, ``PGUSER``,
        ``PGPASSWORD``, ``PGDATABASE``); adds no new ones.

        Returns:
            The resolved settings for whichever backend
            ``ADO2GH_STORAGE_BACKEND`` selects.
        """
        return cls(
            backend=(os.environ.get("ADO2GH_STORAGE_BACKEND") or "sqlite").lower(),
            sqlite_path=os.environ.get("ADO2GH_SQLITE_PATH", "data/agent_checkpoints.db"),
            database_url=os.environ.get("ADO2GH_DATABASE_URL", ""),
            pg_host=os.environ.get("PGHOST", "localhost"),
            pg_port=int(os.environ.get("PGPORT", 5432)),
            pg_user=os.environ.get("PGUSER", "ado2gh"),
            pg_password=os.environ.get("PGPASSWORD", ""),
            pg_dbname=os.environ.get("PGDATABASE", "ado2gh"),
        )

    def is_postgres(self) -> bool:
        """Whether the resolved backend selects PostgreSQL.

        Returns:
            True for ``"postgres"`` or the ``"postgresql"`` alias, matching
            what the graph builder accepts today.
        """
        return self.backend in ("postgresql", "postgres")

    def postgres_dsn(self) -> str:
        """Build the connection string used when ``database_url`` is not set.

        Returns:
            A ``postgresql://user:password@host:port/dbname`` string built
            from the individual ``pg_*`` fields.
        """
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_dbname}"
        )
