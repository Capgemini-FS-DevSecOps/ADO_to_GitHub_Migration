"""Job persistence — SQLite (dev) and PostgreSQL (prod) backends."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from ado2gh.api.contracts import JobRecord, JobStatus, JobTypeEnum as JobType


class JobStore(ABC):
    @abstractmethod
    def enqueue(
        self,
        job_type: JobType,
        payload: dict,
        idempotency_key: str | None = None,
    ) -> JobRecord:
        ...

    @abstractmethod
    def get(self, job_id: str) -> JobRecord | None:
        ...

    @abstractmethod
    def claim_next(self) -> JobRecord | None:
        ...

    @abstractmethod
    def complete(self, job_id: str, result: dict | None = None) -> None:
        ...

    @abstractmethod
    def fail(self, job_id: str, error: str) -> None:
        ...


class SQLiteJobStore(JobStore):
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

    def __init__(self, db_path: str):
        self.db_path = db_path
        with self._conn() as conn:
            conn.executescript(self.SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def enqueue(self, job_type: JobType, payload: dict,
                idempotency_key: str | None = None) -> JobRecord:
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
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE idempotency_key=?", (key,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def get(self, job_id: str) -> JobRecord | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def claim_next(self) -> JobRecord | None:
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
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, result=?, updated_at=? WHERE id=?",
                (JobStatus.COMPLETED.value, json.dumps(result or {}), now, job_id),
            )

    def fail(self, job_id: str, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=?",
                (JobStatus.FAILED.value, error, now, job_id),
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> JobRecord:
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
    """PostgreSQL job store for production deployments."""

    def __init__(self, dsn: str):
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

    def _conn(self):
        return self._psycopg2.connect(self.dsn)

    def enqueue(self, job_type: JobType, payload: dict,
                idempotency_key: str | None = None) -> JobRecord:
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
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM jobs WHERE idempotency_key=%s", (key,))
                row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def get(self, job_id: str) -> JobRecord | None:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM jobs WHERE id=%s", (job_id,))
                row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def claim_next(self) -> JobRecord | None:
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
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET status=%s, result=%s, updated_at=%s WHERE id=%s",
                    (JobStatus.COMPLETED.value, json.dumps(result or {}), now, job_id),
                )
            conn.commit()

    def fail(self, job_id: str, error: str) -> None:
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
    """DynamoDB job store for serverless production deployments."""

    def __init__(self, table_name: str, region: str = "us-east-1", endpoint_url: str | None = None):
        import boto3

        kwargs: dict = {"region_name": region}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        self._table_name = table_name
        self._dynamodb = boto3.resource("dynamodb", **kwargs)
        self._client = boto3.client("dynamodb", **kwargs)
        self._ensure_table()

    def _table(self):
        return self._dynamodb.Table(self._table_name)

    def _ensure_table(self) -> None:
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
        from boto3.dynamodb.conditions import Attr
        resp = self._table().scan(FilterExpression=Attr("idempotency_key").eq(key), Limit=1)
        items = resp.get("Items", [])
        if not items:
            return None
        return self._load(items[0]["id"])

    def get(self, job_id: str) -> JobRecord | None:
        return self._load(job_id)

    def claim_next(self) -> JobRecord | None:
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
        rec = self.get(job_id)
        if not rec:
            return
        rec.status = JobStatus.COMPLETED
        rec.result = result or {}
        rec.updated_at = datetime.now(timezone.utc)
        self._save(rec)

    def fail(self, job_id: str, error: str) -> None:
        rec = self.get(job_id)
        if not rec:
            return
        rec.status = JobStatus.FAILED
        rec.error = error
        rec.updated_at = datetime.now(timezone.utc)
        self._save(rec)


class JobStoreFactory:
    @staticmethod
    def from_env(sqlite_fallback: str = "migration_state.db") -> JobStore:
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
