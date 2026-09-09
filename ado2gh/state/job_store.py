"""Job queue persistence: SQLite (development), PostgreSQL and DynamoDB (production).

Every store implements :class:`JobStore` with identical signatures; the
factory picks one from ``ADO2GH_STORAGE_BACKEND``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ado2gh.api.contracts import JobRecord, JobStatus
from ado2gh.api.contracts import JobTypeEnum as JobType

if TYPE_CHECKING:
    from boto3.resources.base import ServiceResource
    from psycopg2.extensions import connection as PgConnection


class JobStore(ABC):
    """Abstract queue of background jobs with at-most-once claiming."""

    @abstractmethod
    def enqueue(
        self,
        job_type: JobType,
        payload: dict,
        idempotency_key: str | None = None,
    ) -> JobRecord:
        """Add a ``pending`` job, or return the existing job with the same idempotency key.

        Args:
            job_type: What the worker should run.
            payload: Job arguments, stored as JSON.
            idempotency_key: Optional unique key that makes repeated calls return
                the first job instead of creating another.
        """

    @abstractmethod
    def get(self, job_id: str) -> JobRecord | None:
        """Return the job with this id, or ``None``."""

    @abstractmethod
    def claim_next(self) -> JobRecord | None:
        """Move the oldest ``pending`` job to ``running`` and return it, or ``None`` when idle."""

    @abstractmethod
    def complete(self, job_id: str, result: dict | None = None) -> None:
        """Mark a job ``completed`` and store its result."""

    @abstractmethod
    def fail(self, job_id: str, error: str) -> None:
        """Mark a job ``failed`` and store the error message."""


class SQLiteJobStore(JobStore):
    """Job store backed by a ``jobs`` table in a SQLite file."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        job_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        payload TEXT NOT NULL DEFAULT '{}',
        result TEXT,
        error TEXT,
        idempotency_key TEXT UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """

    def __init__(self, db_path: str) -> None:
        """Open (or create) the database file and apply the schema."""
        self.db_path = db_path
        with self._conn() as conn:
            conn.executescript(self.SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        """Return a fresh connection with dict-like rows."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def enqueue(self, job_type: JobType, payload: dict,
                idempotency_key: str | None = None) -> JobRecord:
        """Add a ``pending`` job; see :meth:`JobStore.enqueue`.

        Returns:
            The stored job record with ``pending`` status, or the job created by an
            earlier call when ``idempotency_key`` matches one.
        """
        now = datetime.now(timezone.utc).isoformat()
        if idempotency_key:
            existing = self.get_by_idempotency(idempotency_key)
            if existing:
                return existing

        job_id = str(uuid.uuid4())
        record = JobRecord(
            id=job_id,
            job_type=job_type,
            status=JobStatus.PENDING,
            payload=payload,
            idempotency_key=idempotency_key,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO jobs (id, job_type, status, payload, idempotency_key,
                   created_at, updated_at) VALUES (?,?,?,?,?,?,?)""",
                (job_id, job_type.value, JobStatus.PENDING.value,
                 json.dumps(payload), idempotency_key, now, now),
            )
        return record

    def get_by_idempotency(self, key: str) -> JobRecord | None:
        """Return the job created with this idempotency key, or ``None``."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE idempotency_key=?", (key,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def get(self, job_id: str) -> JobRecord | None:
        """Return the job with this id, or ``None``."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def claim_next(self) -> JobRecord | None:
        """Claim the oldest ``pending`` job; see :meth:`JobStore.claim_next`.

        Returns:
            The claimed job with its status set to ``running``, or ``None`` when no
            job is pending.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY created_at LIMIT 1",
                (JobStatus.PENDING.value,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE jobs SET status=?, updated_at=? WHERE id=?",
                (JobStatus.RUNNING.value, now, row["id"]),
            )
        rec = self._row_to_record(row)
        rec.status = JobStatus.RUNNING
        return rec

    def complete(self, job_id: str, result: dict | None = None) -> None:
        """Mark a job ``completed`` and store its result."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, result=?, updated_at=? WHERE id=?",
                (JobStatus.COMPLETED.value, json.dumps(result or {}), now, job_id),
            )

    def fail(self, job_id: str, error: str) -> None:
        """Mark a job ``failed`` and store the error message."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=?",
                (JobStatus.FAILED.value, error, now, job_id),
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> JobRecord:
        """Build a ``JobRecord`` from a ``jobs`` row, decoding the JSON columns."""
        return JobRecord(
            id=row["id"],
            job_type=JobType(row["job_type"]),
            status=JobStatus(row["status"]),
            payload=json.loads(row["payload"] or "{}"),
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
            idempotency_key=row["idempotency_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class PostgresJobStore(JobStore):
    """Job store backed by a ``jobs`` table in PostgreSQL, claiming with ``SKIP LOCKED``."""

    def __init__(self, dsn: str) -> None:
        """Connect with ``psycopg2`` and create the ``jobs`` table.

        Args:
            dsn: A ``postgresql://`` connection string; it carries the
                credentials and is never logged.
        """
        import psycopg2
        import psycopg2.extras
        self._psycopg2 = psycopg2
        self._extras = psycopg2.extras
        self.dsn = dsn
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY,
                        job_type TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        payload JSONB NOT NULL DEFAULT '{}',
                        result JSONB,
                        error TEXT,
                        idempotency_key TEXT UNIQUE,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                """)
            conn.commit()

    def _conn(self) -> PgConnection:
        """Return a new connection; callers commit explicitly."""
        return self._psycopg2.connect(self.dsn)

    def enqueue(self, job_type: JobType, payload: dict,
                idempotency_key: str | None = None) -> JobRecord:
        """Add a ``pending`` job; see :meth:`JobStore.enqueue`.

        Returns:
            The stored job record with ``pending`` status, or the job created by an
            earlier call when ``idempotency_key`` matches one.
        """
        if idempotency_key:
            existing = self.get_by_idempotency(idempotency_key)
            if existing:
                return existing
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO jobs (id, job_type, status, payload, idempotency_key,
                       created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (job_id, job_type.value, JobStatus.PENDING.value,
                     json.dumps(payload), idempotency_key, now, now),
                )
            conn.commit()
        return JobRecord(
            id=job_id, job_type=job_type, status=JobStatus.PENDING,
            payload=payload, idempotency_key=idempotency_key,
            created_at=now, updated_at=now,
        )

    def get_by_idempotency(self, key: str) -> JobRecord | None:
        """Return the job created with this idempotency key, or ``None``."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM jobs WHERE idempotency_key=%s", (key,))
                row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def get(self, job_id: str) -> JobRecord | None:
        """Return the job with this id, or ``None``."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM jobs WHERE id=%s", (job_id,))
                row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def claim_next(self) -> JobRecord | None:
        """Claim the oldest ``pending`` job under a row lock; see :meth:`JobStore.claim_next`.

        Returns:
            The claimed job with its status set to ``running``, or ``None`` when no
            job is pending.
        """
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM jobs WHERE status=%s ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED",
                    (JobStatus.PENDING.value,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cur.execute(
                    "UPDATE jobs SET status=%s, updated_at=%s WHERE id=%s",
                    (JobStatus.RUNNING.value, now, row["id"]),
                )
            conn.commit()
        rec = self._row_to_record(row)
        rec.status = JobStatus.RUNNING
        return rec

    def complete(self, job_id: str, result: dict | None = None) -> None:
        """Mark a job ``completed`` and store its result."""
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET status=%s, result=%s, updated_at=%s WHERE id=%s",
                    (JobStatus.COMPLETED.value, json.dumps(result or {}), now, job_id),
                )
            conn.commit()

    def fail(self, job_id: str, error: str) -> None:
        """Mark a job ``failed`` and store the error message."""
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET status=%s, error=%s, updated_at=%s WHERE id=%s",
                    (JobStatus.FAILED.value, error, now, job_id),
                )
            conn.commit()

    @staticmethod
    def _row_to_record(row: dict) -> JobRecord:
        """Build a ``JobRecord`` from a ``jobs`` row; JSONB columns may already be decoded."""
        return JobRecord(
            id=row["id"],
            job_type=JobType(row["job_type"]),
            status=JobStatus(row["status"]),
            payload=row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"] or "{}"),
            result=row["result"] if isinstance(row.get("result"), dict) else (
                json.loads(row["result"]) if row.get("result") else None
            ),
            error=row.get("error"),
            idempotency_key=row.get("idempotency_key"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class DynamoDBJobStore(JobStore):
    """Job store backed by one DynamoDB table keyed by job id."""

    def __init__(self, table_name: str, region: str = "us-east-1", endpoint_url: str | None = None) -> None:
        """Connect with ``boto3`` and create the table if it does not exist.

        Args:
            table_name: DynamoDB table holding the jobs.
            region: AWS region of the table.
            endpoint_url: Override for a local DynamoDB endpoint.
        """
        import boto3

        kwargs: dict = {"region_name": region}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        self._table_name = table_name
        self._dynamodb = boto3.resource("dynamodb", **kwargs)
        self._client = boto3.client("dynamodb", **kwargs)
        self._ensure_table()

    def _table(self) -> ServiceResource:
        """Return the ``Table`` resource for the jobs table."""
        return self._dynamodb.Table(self._table_name)

    def _ensure_table(self) -> None:
        """Create the jobs table with pay-per-request billing when it is missing."""
        if self._table_name in self._client.list_tables().get("TableNames", []):
            return
        self._client.create_table(
            TableName=self._table_name,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        self._client.get_waiter("table_exists").wait(TableName=self._table_name)

    def _save(self, record: JobRecord) -> JobRecord:
        """Write the whole record as one item and return it."""
        self._table().put_item(Item={
            "id": record.id,
            "job_type": record.job_type.value,
            "status": record.status.value,
            "payload": json.dumps(record.payload),
            "result": json.dumps(record.result) if record.result else None,
            "error": record.error,
            "idempotency_key": record.idempotency_key,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        })
        return record

    def _load(self, job_id: str) -> JobRecord | None:
        """Read one item by id and rebuild the record, or ``None`` when absent."""
        resp = self._table().get_item(Key={"id": job_id})
        item = resp.get("Item")
        if not item:
            return None
        return JobRecord(
            id=item["id"],
            job_type=JobType(item["job_type"]),
            status=JobStatus(item["status"]),
            payload=json.loads(item.get("payload") or "{}"),
            result=json.loads(item["result"]) if item.get("result") else None,
            error=item.get("error"),
            idempotency_key=item.get("idempotency_key"),
            created_at=datetime.fromisoformat(item["created_at"]),
            updated_at=datetime.fromisoformat(item["updated_at"]),
        )

    def enqueue(self, job_type: JobType, payload: dict,
                idempotency_key: str | None = None) -> JobRecord:
        """Add a ``pending`` job; see :meth:`JobStore.enqueue`.

        Returns:
            The stored job record with ``pending`` status, or the job created by an
            earlier call when ``idempotency_key`` matches one.
        """
        if idempotency_key:
            existing = self.get_by_idempotency(idempotency_key)
            if existing:
                return existing
        now = datetime.now(timezone.utc)
        record = JobRecord(
            id=str(uuid.uuid4()),
            job_type=job_type,
            status=JobStatus.PENDING,
            payload=payload,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        return self._save(record)

    def get_by_idempotency(self, key: str) -> JobRecord | None:
        """Return the job created with this idempotency key, or ``None`` (table scan)."""
        from boto3.dynamodb.conditions import Attr
        resp = self._table().scan(FilterExpression=Attr("idempotency_key").eq(key), Limit=1)
        items = resp.get("Items", [])
        if not items:
            return None
        return self._load(items[0]["id"])

    def get(self, job_id: str) -> JobRecord | None:
        """Return the job with this id, or ``None``."""
        return self._load(job_id)

    def claim_next(self) -> JobRecord | None:
        """Claim a ``pending`` job found by table scan; see :meth:`JobStore.claim_next`.

        Returns:
            The claimed job with its status set to ``running``, or ``None`` when no
            job is pending.
        """
        from boto3.dynamodb.conditions import Attr
        resp = self._table().scan(
            FilterExpression=Attr("status").eq(JobStatus.PENDING.value),
            Limit=1,
        )
        items = resp.get("Items", [])
        if not items:
            return None
        rec = self._load(items[0]["id"])
        if not rec:
            return None
        rec.status = JobStatus.RUNNING
        rec.updated_at = datetime.now(timezone.utc)
        return self._save(rec)

    def complete(self, job_id: str, result: dict | None = None) -> None:
        """Mark a job ``completed`` and store its result; unknown ids are ignored."""
        rec = self.get(job_id)
        if not rec:
            return
        rec.status = JobStatus.COMPLETED
        rec.result = result or {}
        rec.updated_at = datetime.now(timezone.utc)
        self._save(rec)

    def fail(self, job_id: str, error: str) -> None:
        """Mark a job ``failed`` and store the error message; unknown ids are ignored."""
        rec = self.get(job_id)
        if not rec:
            return
        rec.status = JobStatus.FAILED
        rec.error = error
        rec.updated_at = datetime.now(timezone.utc)
        self._save(rec)


class JobStoreFactory:
    """Builds the job store selected by the environment."""

    @staticmethod
    def from_env(sqlite_fallback: str = "migration_state.db") -> JobStore:
        """Return the configured job store.

        Args:
            sqlite_fallback: SQLite path used when no backend or job database
                (``ADO2GH_JOB_DB``) is configured.
        """
        from ado2gh.state.storage_config import StorageBackend, StorageConfig

        cfg = StorageConfig.from_env(sqlite_default=sqlite_fallback)
        endpoint = os.environ.get("ADO2GH_DYNAMODB_ENDPOINT")

        if cfg.backend == StorageBackend.POSTGRES:
            return PostgresJobStore(cfg.database_url)

        if cfg.backend == StorageBackend.DYNAMODB:
            jobs_table = os.environ.get("ADO2GH_DYNAMODB_JOBS_TABLE", f"{cfg.dynamodb_table}-jobs")
            return DynamoDBJobStore(jobs_table, cfg.aws_region, endpoint)

        job_path = os.environ.get("ADO2GH_JOB_DB", cfg.sqlite_path)
        return SQLiteJobStore(job_path)
