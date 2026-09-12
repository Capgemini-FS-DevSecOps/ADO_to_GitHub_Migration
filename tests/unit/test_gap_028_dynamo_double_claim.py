"""GAP-028: ``DynamoDBJobStore`` must not double-claim a job under concurrency.

The register entry (``specs/013-clean-code-arch-remediation/gap-register.md``,
GAP-028) faults ``DynamoDBJobStore.claim_next()`` for scanning for a ``pending``
job and then writing it back with **no** ``ConditionExpression``, and
``get_by_idempotency()`` for an eventually-consistent ``scan()`` with no
``ConsistentRead=True``. Two workers can therefore claim the same job "with no
error, no log, and no audit entry" (CA-004).

``PostgresJobStore.claim_next()`` (``SELECT ... FOR UPDATE SKIP LOCKED``) is the
in-repo correct pattern: the loser of the race simply gets no job back.

The boto3 Table API is faked in-process — no ``moto``, no network, no new
dependency (boto3 is not installed in this environment). The race is made
deterministic with a one-shot hook that runs the second worker's whole claim
between the first worker's scan and its write.

Items are seeded into the fake table directly rather than through ``enqueue()``:
``JobRecord`` declares no ``created_at``/``updated_at``, so ``_save()`` — which
reads them back — raises ``AttributeError`` at HEAD. That is a separate latent
defect outside this fix's scope (``claim_next`` / ``get_by_idempotency`` only).
"""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone

import pytest

from ado2gh.models import JobStatus
from ado2gh.models import JobTypeEnum as JobType

# Obviously-fake placeholder, never a real credential (CA-003).
FAKE_TOKEN = "ghp_000000000000000000000000000000000000"
JOB_ID = "job-1"


# ─── Minimal fake of the boto3 Table API ─────────────────────────────


class _ClientError(Exception):
    """Stand-in for ``botocore.exceptions.ClientError``."""

    def __init__(self, response: dict, operation_name: str) -> None:
        super().__init__(response["Error"]["Message"])
        self.response = response
        self.operation_name = operation_name


def _conditional_failure(operation: str) -> _ClientError:
    """Build the error DynamoDB raises when a ``ConditionExpression`` is false."""
    return _ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException",
                   "Message": "The conditional request failed"}},
        operation,
    )


class _Eq:
    """An ``Attr(name).eq(value)`` condition the fake table can evaluate."""

    def __init__(self, name: str, value: object) -> None:
        self.name = name
        self.value = value

    def matches(self, item: dict) -> bool:
        """Return True when ``item`` satisfies this equality condition."""
        return item.get(self.name) == self.value


class _Attr:
    """Stand-in for ``boto3.dynamodb.conditions.Attr``."""

    def __init__(self, name: str) -> None:
        self._name = name

    def eq(self, value: object) -> _Eq:
        """Return an equality condition on this attribute."""
        return _Eq(self._name, value)


class FakeTable:
    """In-memory stand-in for a boto3 DynamoDB ``Table`` resource."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.after_scan = None  # one-shot hook, used to interleave two workers

    def put_item(self, Item: dict, ConditionExpression: object = None, **_: object) -> dict:  # noqa: N803
        """Store a whole item, honouring an optional condition."""
        self.calls.append(("put_item", {"Item": Item, "ConditionExpression": ConditionExpression}))
        if ConditionExpression is not None and not ConditionExpression.matches(
            self.items.get(Item["id"]) or {}
        ):
            raise _conditional_failure("PutItem")
        self.items[Item["id"]] = dict(Item)
        return {}

    def get_item(self, Key: dict, **_: object) -> dict:  # noqa: N803
        """Return ``{"Item": ...}`` for a stored id, or an empty response."""
        item = self.items.get(Key["id"])
        return {"Item": dict(item)} if item else {}

    def scan(self, FilterExpression: object = None, Limit: int | None = None,  # noqa: N803
             ConsistentRead: bool = False, **_: object) -> dict:  # noqa: N803
        """Filter the stored items, then fire the one-shot interleave hook."""
        self.calls.append(("scan", {"ConsistentRead": ConsistentRead}))
        found = [dict(i) for i in self.items.values()
                 if FilterExpression is None or FilterExpression.matches(i)]
        if Limit:
            found = found[:Limit]
        hook, self.after_scan = self.after_scan, None
        if hook:
            hook()
        return {"Items": found}

    def update_item(self, Key: dict, UpdateExpression: str,  # noqa: N803
                    ExpressionAttributeValues: dict | None = None,  # noqa: N803
                    ExpressionAttributeNames: dict | None = None,  # noqa: N803
                    ConditionExpression: object = None, **_: object) -> dict:  # noqa: N803
        """Apply a ``SET a = :b, c = :d`` update when the condition holds."""
        self.calls.append(("update_item", {"Key": Key, "ConditionExpression": ConditionExpression}))
        item = self.items.get(Key["id"])
        if item is None or (
            ConditionExpression is not None and not ConditionExpression.matches(item)
        ):
            raise _conditional_failure("UpdateItem")
        for assignment in UpdateExpression.removeprefix("SET ").split(","):
            lhs, rhs = (part.strip() for part in assignment.split("="))
            item[(ExpressionAttributeNames or {}).get(lhs, lhs)] = (
                ExpressionAttributeValues or {}
            )[rhs]
        return {}


class _FakeResource:
    """Stand-in for ``boto3.resource("dynamodb")``."""

    def __init__(self, table: FakeTable) -> None:
        self._table = table

    def Table(self, _name: str) -> FakeTable:  # noqa: N802
        """Return the single fake table."""
        return self._table


class _FakeClient:
    """Stand-in for ``boto3.client("dynamodb")``; the table always exists."""

    def list_tables(self) -> dict:
        """Report the jobs table as already created."""
        return {"TableNames": ["jobs"]}


class RecordingAuditWriter:
    """Captures the audit events the store writes instead of persisting them."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def write(self, event_type: str, profile_id: str = "", actor: str = "",
              payload: dict | None = None) -> str:
        """Record one event and return a fake audit id."""
        self.events.append((event_type, dict(payload or {})))
        return "aud_test"


def _install_fake_boto3(monkeypatch: pytest.MonkeyPatch, table: FakeTable) -> None:
    """Put fake ``boto3``/``botocore`` modules in ``sys.modules`` for this test."""
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = _ClientError
    botocore = types.ModuleType("botocore")
    botocore.exceptions = exceptions

    conditions = types.ModuleType("boto3.dynamodb.conditions")
    conditions.Attr = _Attr
    dynamodb = types.ModuleType("boto3.dynamodb")
    dynamodb.conditions = conditions
    boto3 = types.ModuleType("boto3")
    boto3.dynamodb = dynamodb
    boto3.resource = lambda *_a, **_k: _FakeResource(table)
    boto3.client = lambda *_a, **_k: _FakeClient()

    for name, module in {
        "botocore": botocore,
        "botocore.exceptions": exceptions,
        "boto3": boto3,
        "boto3.dynamodb": dynamodb,
        "boto3.dynamodb.conditions": conditions,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


@pytest.fixture
def table() -> FakeTable:
    """One shared fake table, standing in for the real jobs table."""
    return FakeTable()


@pytest.fixture
def make_store(monkeypatch: pytest.MonkeyPatch, table: FakeTable):
    """Return a builder for stores that all talk to the same fake table."""
    _install_fake_boto3(monkeypatch, table)
    from ado2gh.state.job_store import DynamoDBJobStore

    def build(audit_writer: object = None) -> DynamoDBJobStore:
        kwargs = {"audit_writer": audit_writer} if audit_writer is not None else {}
        return DynamoDBJobStore("jobs", **kwargs)

    return build


def _seed_pending(table: FakeTable, idempotency_key: str | None = None) -> None:
    """Put one ``pending`` job in the table, in the shape ``_save()`` writes."""
    now = datetime.now(timezone.utc).isoformat()
    table.items[JOB_ID] = {
        "id": JOB_ID,
        "job_type": JobType.MIGRATE_REPO.value,
        "status": JobStatus.PENDING.value,
        "payload": json.dumps({"repo": "octo/demo", "token": FAKE_TOKEN}),
        "result": None,
        "error": None,
        "idempotency_key": idempotency_key,
        "created_at": now,
        "updated_at": now,
    }


def _claim_both(make_store, table: FakeTable, audit_writer: object = None):
    """Run two workers whose claims interleave, and return both results.

    Worker B performs its entire claim between worker A's scan and A's write —
    the exact race GAP-028 describes.

    Returns:
        The pair ``(claim_a, claim_b)``; each is a ``JobRecord`` or ``None``.
    """
    worker_a = make_store(audit_writer)
    worker_b = make_store(audit_writer)
    _seed_pending(table)

    claim_b: list = []
    table.after_scan = lambda: claim_b.append(worker_b.claim_next())
    claim_a = worker_a.claim_next()
    return claim_a, (claim_b[0] if claim_b else None)


def test_two_racing_workers_claim_at_most_one_job(make_store, table):
    """Only one of two interleaved workers may come away with the job."""
    claim_a, claim_b = _claim_both(make_store, table)

    claimed = [c for c in (claim_a, claim_b) if c is not None]
    assert len(claimed) == 1, (
        f"both workers claimed the same job: {[c.id for c in claimed]}"
    )
    assert claimed[0].status is JobStatus.RUNNING
    assert table.items[claimed[0].id]["status"] == JobStatus.RUNNING.value


def test_claim_write_carries_a_condition_expression(make_store, table):
    """The claiming write must be guarded by a status-equality condition."""
    store = make_store()
    _seed_pending(table)
    table.calls.clear()
    store.claim_next()

    writes = [(op, kw) for op, kw in table.calls if op in ("put_item", "update_item")]
    assert writes, "claim_next() performed no write"
    guarded = [kw["ConditionExpression"] for _op, kw in writes if kw["ConditionExpression"]]
    assert guarded, f"claim write carries no ConditionExpression: {writes}"
    assert any(
        c.name == "status" and c.value == JobStatus.PENDING.value for c in guarded
    ), "the condition does not guard the pending -> running transition"


def test_get_by_idempotency_reads_consistently(make_store, table):
    """The idempotency lookup must use a strongly consistent read."""
    store = make_store()
    _seed_pending(table, idempotency_key="key-1")
    table.calls.clear()

    assert store.get_by_idempotency("key-1") is not None
    scans = [kw for op, kw in table.calls if op == "scan"]
    assert scans, "get_by_idempotency() issued no scan"
    assert all(kw["ConsistentRead"] for kw in scans), (
        f"idempotency scan is eventually consistent: {scans}"
    )


def test_claim_conflict_is_logged_and_audited(make_store, table, caplog):
    """A lost race is recorded in the log and as an audit event (CA-004)."""
    writer = RecordingAuditWriter()
    with caplog.at_level("WARNING", logger="ado2gh.state.job_store"):
        _claim_both(make_store, table, writer)

    assert caplog.records, "claim conflict was not logged"
    assert any(r.levelname in ("WARNING", "ERROR") for r in caplog.records)

    assert len(writer.events) == 1, f"expected one audit event, got {writer.events}"
    event_type, payload = writer.events[0]
    assert event_type == "job.claim_conflict"
    assert set(payload) == {"job_id", "job_type", "backend"}
    assert FAKE_TOKEN not in json.dumps(payload), "audit payload leaked the job payload (CA-003)"
