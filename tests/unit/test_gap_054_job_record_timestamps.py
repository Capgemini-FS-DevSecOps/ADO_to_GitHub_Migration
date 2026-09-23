"""Regression check for register entry GAP-054: ``JobRecord`` must declare the two timestamps every store already writes.

The register entry (``specs/013-clean-code-arch-remediation/gap-register.md``,
GAP-054) faults ``ado2gh/models.py`` for declaring a ``JobRecord`` without
``created_at``/``updated_at`` while every job store passes both to the
constructor and ``DynamoDBJobStore._save`` reads ``record.created_at`` back.
Pydantic dropped them at construction, so ``_save`` raised ``AttributeError`` on
the first write and ``complete()``/``fail()`` raised ``ValueError`` on
assignment — which, through the worker's catch-then-``fail()`` fallback, killed
the worker process instead of recording one failed job.

The operator approved adding the fields (operator decision 8, approved by
operator instruction, 2026-09-13), so these tests assert the fields exist, that
they serialize, and that both terminal transitions move ``updated_at``.

``boto3`` is not installed here, so the DynamoDB table is a recording stub —
no network, no new dependency. The SQLite cases use ``tmp_path``; no file under
``data/`` is touched and every credential-shaped literal is an obvious fake
(CA-003).
"""
from __future__ import annotations

import json
import sys
import time
import types
from datetime import datetime, timezone

import pytest

from ado2gh.models import JobRecord, JobStatus
from ado2gh.models import JobTypeEnum as JobType
from ado2gh.state.job_store import SQLiteJobStore

# Obviously-fake placeholder, never a real credential (CA-003).
FAKE_TOKEN = "ghp_000000000000000000000000000000000000"
PAYLOAD = {"repo": "octo/demo", "token": FAKE_TOKEN}


# ─── Recording stand-in for the boto3 Table API ──────────────────────


class RecordingTable:
    """In-memory DynamoDB ``Table`` that records every write it is given."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.put_items: list[dict] = []
        self.updates: list[dict] = []

    def put_item(self, Item: dict, **_: object) -> dict:  # noqa: N803
        """Store one whole item and keep a copy of what was written."""
        self.put_items.append(dict(Item))
        self.items[Item["id"]] = dict(Item)
        return {}

    def get_item(self, Key: dict, **_: object) -> dict:  # noqa: N803
        """Return ``{"Item": ...}`` for a stored id, or an empty response."""
        item = self.items.get(Key["id"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, Key: dict, **kwargs: object) -> dict:  # noqa: N803
        """Record a conditional update and apply its ``SET`` assignments."""
        self.updates.append({"Key": Key, **kwargs})
        item = self.items[Key["id"]]
        names = kwargs.get("ExpressionAttributeNames") or {}
        values = kwargs.get("ExpressionAttributeValues") or {}
        for assignment in str(kwargs["UpdateExpression"]).removeprefix("SET ").split(","):
            lhs, rhs = (part.strip() for part in assignment.split("="))
            item[names.get(lhs, lhs)] = values[rhs]
        return {}

    def scan(self, **_: object) -> dict:
        """Return every stored item; the filter is irrelevant to these tests."""
        return {"Items": [dict(i) for i in self.items.values()]}


class _FakeResource:
    """Stand-in for ``boto3.resource("dynamodb")``."""

    def __init__(self, table: RecordingTable) -> None:
        self._table = table

    def Table(self, _name: str) -> RecordingTable:  # noqa: N802
        """Return the single recording table."""
        return self._table


class _FakeClient:
    """Stand-in for ``boto3.client("dynamodb")``; the table always exists."""

    def list_tables(self) -> dict:
        """Report the jobs table as already created."""
        return {"TableNames": ["jobs"]}


def _install_fake_boto3(monkeypatch: pytest.MonkeyPatch, table: RecordingTable) -> None:
    """Put a fake ``boto3`` in ``sys.modules`` for the duration of one test."""
    conditions = types.ModuleType("boto3.dynamodb.conditions")
    conditions.Attr = lambda name: types.SimpleNamespace(eq=lambda value: (name, value))
    dynamodb = types.ModuleType("boto3.dynamodb")
    dynamodb.conditions = conditions
    boto3 = types.ModuleType("boto3")
    boto3.dynamodb = dynamodb
    boto3.resource = lambda *_a, **_k: _FakeResource(table)
    boto3.client = lambda *_a, **_k: _FakeClient()
    for name, module in {
        "boto3": boto3,
        "boto3.dynamodb": dynamodb,
        "boto3.dynamodb.conditions": conditions,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


@pytest.fixture
def dynamo_table() -> RecordingTable:
    """One recording table shared by the store under test."""
    return RecordingTable()


@pytest.fixture
def dynamo_store(monkeypatch: pytest.MonkeyPatch, dynamo_table: RecordingTable):
    """A ``DynamoDBJobStore`` talking to the recording table."""
    _install_fake_boto3(monkeypatch, dynamo_table)
    from ado2gh.state.job_store import DynamoDBJobStore

    return DynamoDBJobStore("jobs")


@pytest.fixture
def sqlite_store(tmp_path) -> SQLiteJobStore:
    """A SQLite job store in a throwaway file."""
    return SQLiteJobStore(str(tmp_path / "jobs.db"))


# ─── The model ───────────────────────────────────────────────────────


def test_job_record_declares_both_timestamps():
    """A record built without timestamps still carries UTC defaults."""
    rec = JobRecord(id="job-1", job_type=JobType.MIGRATE_REPO, status=JobStatus.PENDING)

    assert isinstance(rec.created_at, datetime)
    assert isinstance(rec.updated_at, datetime)
    assert rec.created_at.tzinfo is not None, "created_at must be timezone-aware"
    assert rec.created_at.utcoffset() == timezone.utc.utcoffset(None)


def test_constructor_keeps_the_timestamps_it_is_given():
    """The stores pass both timestamps; neither may be silently dropped."""
    moment = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    rec = JobRecord(
        id="job-1", job_type=JobType.MIGRATE_REPO, status=JobStatus.PENDING,
        created_at=moment, updated_at=moment,
    )

    assert rec.created_at == moment
    assert rec.updated_at == moment


def test_assigning_updated_at_no_longer_raises():
    """``complete()``/``fail()`` assign this attribute; it must be a declared field."""
    rec = JobRecord(id="job-1", job_type=JobType.MIGRATE_REPO, status=JobStatus.PENDING)
    later = datetime.now(timezone.utc)

    rec.updated_at = later

    assert rec.updated_at == later


def test_serialization_includes_both_timestamps():
    """The job payload the accelerator returns carries both ISO-8601 fields."""
    rec = JobRecord(id="job-1", job_type=JobType.MIGRATE_REPO, status=JobStatus.PENDING)

    assert {"created_at", "updated_at"} <= set(rec.model_dump())
    as_json = rec.model_dump(mode="json")
    assert datetime.fromisoformat(as_json["created_at"]).tzinfo is not None
    assert datetime.fromisoformat(as_json["updated_at"]).tzinfo is not None


# ─── SQLite: the shipped default backend ─────────────────────────────


def test_sqlite_complete_moves_updated_at(sqlite_store: SQLiteJobStore):
    """A completed job keeps its creation time and gets a later update time."""
    job = sqlite_store.enqueue(JobType.MIGRATE_REPO, PAYLOAD)
    time.sleep(0.01)

    sqlite_store.complete(job.id, {"repos": 1})

    stored = sqlite_store.get(job.id)
    assert stored is not None
    assert stored.status is JobStatus.COMPLETED
    assert stored.created_at == job.created_at
    assert stored.updated_at > stored.created_at


def test_sqlite_fail_moves_updated_at(sqlite_store: SQLiteJobStore):
    """A failed job records the error and a later update time."""
    job = sqlite_store.enqueue(JobType.MIGRATE_REPO, PAYLOAD)
    time.sleep(0.01)

    sqlite_store.fail(job.id, "push rejected")

    stored = sqlite_store.get(job.id)
    assert stored is not None
    assert stored.status is JobStatus.FAILED
    assert stored.error == "push rejected"
    assert stored.updated_at > stored.created_at


def test_sqlite_claim_returns_the_timestamp_it_wrote(sqlite_store: SQLiteJobStore):
    """The claimed record must not report the update time it replaced."""
    job = sqlite_store.enqueue(JobType.MIGRATE_REPO, PAYLOAD)
    time.sleep(0.01)

    claimed = sqlite_store.claim_next()

    assert claimed is not None
    assert claimed.status is JobStatus.RUNNING
    assert claimed.updated_at > job.updated_at
    assert claimed.updated_at == sqlite_store.get(job.id).updated_at


# ─── DynamoDB: the backend the missing fields broke outright ─────────


def test_dynamo_save_writes_both_timestamps(dynamo_store, dynamo_table: RecordingTable):
    """``_save`` must build an item carrying both ISO-8601 timestamps."""
    job = dynamo_store.enqueue(JobType.MIGRATE_REPO, PAYLOAD)

    assert dynamo_table.put_items, "enqueue() wrote no item"
    item = dynamo_table.put_items[-1]
    assert item["created_at"] == job.created_at.isoformat()
    assert item["updated_at"] == job.updated_at.isoformat()


def test_dynamo_complete_persists_a_later_updated_at(dynamo_store, dynamo_table):
    """``complete()`` must not raise, and must persist a moved update time."""
    job = dynamo_store.enqueue(JobType.MIGRATE_REPO, PAYLOAD)
    time.sleep(0.01)

    dynamo_store.complete(job.id, {"repos": 1})

    stored = dynamo_store.get(job.id)
    assert stored.status is JobStatus.COMPLETED
    assert stored.result == {"repos": 1}
    assert stored.created_at == job.created_at
    assert stored.updated_at > stored.created_at
    assert dynamo_table.items[job.id]["updated_at"] == stored.updated_at.isoformat()


def test_dynamo_fail_persists_a_later_updated_at(dynamo_store):
    """``fail()`` is the worker's fail-safe fallback; it must not raise either."""
    job = dynamo_store.enqueue(JobType.MIGRATE_REPO, PAYLOAD)
    time.sleep(0.01)

    dynamo_store.fail(job.id, "push rejected")

    stored = dynamo_store.get(job.id)
    assert stored.status is JobStatus.FAILED
    assert stored.error == "push rejected"
    assert stored.updated_at > stored.created_at


def test_dynamo_round_trip_keeps_the_payload_out_of_the_timestamps(dynamo_table, dynamo_store):
    """The stored item is the job, not a credential dump; CA-003 holds."""
    job = dynamo_store.enqueue(JobType.MIGRATE_REPO, PAYLOAD)
    item = dynamo_table.items[job.id]

    assert FAKE_TOKEN not in json.dumps(
        {k: v for k, v in item.items() if k != "payload"}
    ), "a credential leaked outside the payload column"
