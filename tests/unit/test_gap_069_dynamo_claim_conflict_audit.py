"""Regression check for register entry GAP-069: a DynamoDB claim conflict must reach an audit writer, or say it cannot.

The register entry (``specs/013-clean-code-arch-remediation/gap-register.md``,
GAP-069) faults ``DynamoDBJobStore._audit()`` for building its writer as
``AuditWriter(create_state_db())``: the factory resolves the backend from
``ADO2GH_STORAGE_BACKEND``, and the one value that reaches this code —
``dynamodb`` — is exactly the one ``ado2gh/state/factory.py`` refuses. The
``ValueError`` was swallowed by a blanket ``except Exception``, so the
``job.claim_conflict`` event GAP-028 added (CA-004) could never be written and
nothing said so.

The writer is now injected at construction, by ``JobStoreFactory`` from the
configured state store, and a store without one logs ``job.audit_unavailable``
instead of silently losing the event.

``boto3`` is not installed here, so the table is a stub that fails the
conditional write the way DynamoDB does. No network, no new dependency, and the
job payload carries an obvious fake credential (CA-003).
"""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone

import pytest

from ado2gh.audit import AuditWriter
from ado2gh.models import JobStatus
from ado2gh.models import JobTypeEnum as JobType

# Obviously-fake placeholder, never a real credential (CA-003).
FAKE_TOKEN = "ghp_000000000000000000000000000000000000"
JOB_ID = "job-1"


class _ClientError(Exception):
    """Stand-in for ``botocore.exceptions.ClientError``."""

    def __init__(self, response: dict, operation_name: str) -> None:
        super().__init__(response["Error"]["Message"])
        self.response = response
        self.operation_name = operation_name


class ConflictingTable:
    """A DynamoDB table whose conditional claim write always loses the race."""

    def __init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.item = {
            "id": JOB_ID,
            "job_type": JobType.MIGRATE_REPO.value,
            "status": JobStatus.PENDING.value,
            "payload": json.dumps({"repo": "octo/demo", "token": FAKE_TOKEN}),
            "result": None,
            "error": None,
            "idempotency_key": None,
            "created_at": now,
            "updated_at": now,
        }

    def scan(self, **_: object) -> dict:
        """Return the one pending job."""
        return {"Items": [dict(self.item)]}

    def get_item(self, Key: dict, **_: object) -> dict:  # noqa: N803
        """Return the job by id."""
        return {"Item": dict(self.item)} if Key["id"] == JOB_ID else {}

    def update_item(self, **_: object) -> dict:
        """Fail the conditional write, as DynamoDB does when another worker won."""
        raise _ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException",
                       "Message": "The conditional request failed"}},
            "UpdateItem",
        )


class StubStateDB:
    """Records the audit rows ``AuditWriter`` would persist."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert_audit_event(self, **kwargs: object) -> None:
        """Capture one audit row instead of writing it to a database."""
        self.rows.append(dict(kwargs))


def _install_fake_boto3(monkeypatch: pytest.MonkeyPatch, table: ConflictingTable) -> None:
    """Put fake ``boto3``/``botocore`` modules in ``sys.modules`` for one test."""
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = _ClientError
    botocore = types.ModuleType("botocore")
    botocore.exceptions = exceptions

    conditions = types.ModuleType("boto3.dynamodb.conditions")
    conditions.Attr = lambda name: types.SimpleNamespace(eq=lambda value: (name, value))
    dynamodb = types.ModuleType("boto3.dynamodb")
    dynamodb.conditions = conditions
    boto3 = types.ModuleType("boto3")
    boto3.dynamodb = dynamodb
    boto3.resource = lambda *_a, **_k: types.SimpleNamespace(Table=lambda _n: table)
    boto3.client = lambda *_a, **_k: types.SimpleNamespace(
        list_tables=lambda: {"TableNames": ["jobs"]},
    )

    for name, module in {
        "botocore": botocore,
        "botocore.exceptions": exceptions,
        "boto3": boto3,
        "boto3.dynamodb": dynamodb,
        "boto3.dynamodb.conditions": conditions,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


@pytest.fixture
def make_store(monkeypatch: pytest.MonkeyPatch):
    """Return a builder for stores whose claim write always conflicts."""
    _install_fake_boto3(monkeypatch, ConflictingTable())
    from ado2gh.state.job_store import DynamoDBJobStore

    def build(audit_writer: object = None) -> DynamoDBJobStore:
        return DynamoDBJobStore("jobs", audit_writer=audit_writer)

    return build


def test_claim_conflict_audits_through_the_injected_store(make_store):
    """An injected writer receives exactly one ``job.claim_conflict`` row."""
    db = StubStateDB()
    store = make_store(AuditWriter(db))

    assert store.claim_next() is None, "the lost race must not hand back a job"

    assert len(db.rows) == 1, f"expected one audit row, got {db.rows}"
    row = db.rows[0]
    assert row["event_type"] == "job.claim_conflict"
    assert row["actor"] == "job-worker"
    payload = json.loads(row["payload_json"])
    assert set(payload) == {"job_id", "job_type", "backend"}
    assert payload["backend"] == "dynamodb"
    assert FAKE_TOKEN not in row["payload_json"], "the audit row leaked the job payload (CA-003)"


def test_claim_conflict_without_a_writer_warns_and_does_not_raise(make_store, caplog):
    """No writer means a named warning, never an exception and never silence."""
    store = make_store(None)

    with caplog.at_level("WARNING", logger="ado2gh.state.job_store"):
        assert store.claim_next() is None

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "claim conflict" in messages, "the conflict itself was not logged"
    assert "job.audit_unavailable" in messages, (
        f"the unwritable audit was not reported: {messages}"
    )


def test_configured_audit_writer_reports_a_backend_with_no_state_store(monkeypatch, caplog):
    """Under ``dynamodb`` the factory cannot serve a state store — say so, once."""
    from ado2gh.state.job_store import _configured_audit_writer

    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "dynamodb")
    with caplog.at_level("WARNING", logger="ado2gh.state.job_store"):
        writer = _configured_audit_writer()

    assert writer is None
    assert "job.audit_unavailable" in " ".join(r.getMessage() for r in caplog.records)


def test_configured_audit_writer_uses_the_state_store_when_there_is_one(monkeypatch):
    """With a state store configured, the writer is real and bound to it."""
    from ado2gh.state.job_store import _configured_audit_writer

    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")  # ADO2GH_SQLITE_PATH is per-test
    writer = _configured_audit_writer()

    assert isinstance(writer, AuditWriter)
    assert writer.db is not None


def test_factory_injects_the_audit_writer_into_the_dynamo_store(monkeypatch):
    """``JobStoreFactory`` is the injection site; it must pass the writer through."""
    from ado2gh.state import job_store

    captured: dict = {}

    class _Recorder:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr(job_store, "DynamoDBJobStore", _Recorder)
    monkeypatch.setattr(job_store, "_configured_audit_writer", lambda: "writer-sentinel")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "dynamodb")
    monkeypatch.setenv("ADO2GH_DYNAMODB_TABLE", "ado2gh")

    job_store.JobStoreFactory.from_env()

    assert captured["kwargs"].get("audit_writer") == "writer-sentinel"
