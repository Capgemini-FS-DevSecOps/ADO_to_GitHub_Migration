"""Regression check for register entry GAP-069: the audit destination is configurable, and a DynamoDB job store refuses to start without one.

The register entry (``specs/013-clean-code-arch-remediation/gap-register.md``,
GAP-069) faults ``ado2gh/state/job_store.py`` for building its audit writer from
``create_state_db()``: ``ADO2GH_STORAGE_BACKEND`` picks the job store and the
state store together, so the one value that reaches that code — ``dynamodb`` —
is exactly the one ``ado2gh/state/factory.py`` refuses. A DynamoDB deployment
therefore had nowhere to record a refused claim (CA-004).

``ADO2GH_AUDIT_DESTINATION`` now names where audit events go, independently of
the state backend, and the DynamoDB job store refuses to start when it is unset.

``boto3`` is not installed here, so the fake modules are injected into
``sys.modules`` the way ``tests/unit/test_gap_028_dynamo_double_claim.py`` does
it. No network, no real data directory, and every value is an obvious fake
(CA-003).
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from ado2gh.audit import AuditWriter, create_audit_destination
from ado2gh.audit.destinations import (
    AUDIT_DESTINATION_ENV_VAR,
    DYNAMODB_CREATED_AT_FIELD,
    DYNAMODB_NEW_ITEM_CONDITION,
    DynamoDbAuditDestination,
)

# Obviously-fake placeholders, never real credentials (CA-003).
FAKE_TOKEN = "ghp_000000000000000000000000000000000000"
FAKE_TABLE = "fake-audit-table"


class _ClientError(Exception):
    """Stand-in for ``botocore.exceptions.ClientError``."""

    def __init__(self, response: dict, operation_name: str) -> None:
        super().__init__(response["Error"]["Message"])
        self.response = response
        self.operation_name = operation_name


class FakeAuditTable:
    """In-memory stand-in for the boto3 DynamoDB ``Table`` resource."""

    def __init__(self, missing: bool = False) -> None:
        self.puts: list[dict] = []
        self.missing = missing

    def put_item(self, Item: dict, ConditionExpression: object = None, **_: object) -> dict:  # noqa: N803
        """Record the write, or fail the way a missing table does."""
        if self.missing:
            raise _ClientError(
                {"Error": {"Code": "ResourceNotFoundException",
                           "Message": "Requested resource not found"}},
                "PutItem",
            )
        self.puts.append({"Item": dict(Item), "ConditionExpression": ConditionExpression})
        return {}


class RecordingDestination:
    """Captures the audit rows a writer would persist."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert_audit_event(self, **kwargs: object) -> None:
        """Capture one audit row instead of writing it anywhere."""
        self.rows.append(dict(kwargs))


def _install_fake_boto3(monkeypatch: pytest.MonkeyPatch, table: FakeAuditTable) -> dict:
    """Put fake ``boto3``/``botocore`` modules in ``sys.modules`` for one test.

    Returns:
        The keyword arguments the code passed to ``boto3.resource``, filled in
        when it is called, so a test can assert on region and endpoint.
    """
    resource_kwargs: dict = {}
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = _ClientError
    botocore = types.ModuleType("botocore")
    botocore.exceptions = exceptions

    def _resource(*_a: object, **kwargs: object) -> types.SimpleNamespace:
        resource_kwargs.update(kwargs)
        return types.SimpleNamespace(Table=lambda _n: table)

    boto3 = types.ModuleType("boto3")
    boto3.resource = _resource
    boto3.client = lambda *_a, **_k: types.SimpleNamespace(
        list_tables=lambda: {"TableNames": ["ado2gh-jobs", FAKE_TABLE]},
    )

    for name, module in {
        "botocore": botocore,
        "botocore.exceptions": exceptions,
        "boto3": boto3,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return resource_kwargs


@pytest.fixture(autouse=True)
def _no_inherited_setting(monkeypatch: pytest.MonkeyPatch):
    """Start every test with the setting unset, whatever the environment holds."""
    monkeypatch.delenv(AUDIT_DESTINATION_ENV_VAR, raising=False)
    monkeypatch.delenv("ADO2GH_DYNAMODB_ENDPOINT", raising=False)
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")


# ─── Choosing a destination ──────────────────────────────────────────


def test_unset_setting_keeps_the_configured_state_store(monkeypatch):
    """No setting means exactly today's behaviour: the configured state store."""
    from ado2gh.state.sqlite_db import SQLiteStateDB

    sentinel = SQLiteStateDB(":memory:")
    monkeypatch.setattr("ado2gh.state.factory.create_state_db", lambda *a, **k: sentinel)

    assert create_audit_destination() is sentinel


def test_the_literal_state_value_means_the_same_thing(monkeypatch):
    """``state`` is the spelled-out form of leaving the setting unset."""
    from ado2gh.state.sqlite_db import SQLiteStateDB

    sentinel = SQLiteStateDB(":memory:")
    monkeypatch.setattr("ado2gh.state.factory.create_state_db", lambda *a, **k: sentinel)
    monkeypatch.setenv(AUDIT_DESTINATION_ENV_VAR, "state")

    assert create_audit_destination() is sentinel


def test_unset_setting_writes_the_same_insert_arguments(monkeypatch):
    """The arguments reaching the state store are unchanged by this feature."""
    recorder = RecordingDestination()
    monkeypatch.setattr("ado2gh.state.factory.create_state_db", lambda *a, **k: recorder)

    event_id = AuditWriter(create_audit_destination()).write(
        "job.claim_conflict", profile_id="lightweight", actor="job-worker",
        payload={"job_id": "job-1"},
    )

    assert recorder.rows == [{
        "event_id": event_id,
        "event_type": "job.claim_conflict",
        "profile_id": "lightweight",
        "actor": "job-worker",
        "payload_json": json.dumps({"job_id": "job-1"}),
    }]


def test_an_explicit_setting_overrides_the_state_backend(monkeypatch, tmp_path):
    """The setting wins over ``ADO2GH_STORAGE_BACKEND``, which is what GAP-069 needed."""
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "dynamodb")  # has no state store at all
    monkeypatch.setenv(AUDIT_DESTINATION_ENV_VAR, f"sqlite://{tmp_path / 'audit.db'}")

    destination = create_audit_destination()

    from ado2gh.state.sqlite_db import SQLiteStateDB
    assert isinstance(destination, SQLiteStateDB)
    assert (tmp_path / "audit.db").exists()


def test_a_dynamodb_setting_builds_the_dynamodb_destination(monkeypatch):
    """``dynamodb://<table>`` names the audit table, never the job table."""
    _install_fake_boto3(monkeypatch, FakeAuditTable())
    monkeypatch.setenv(AUDIT_DESTINATION_ENV_VAR, f"dynamodb://{FAKE_TABLE}")

    destination = create_audit_destination()

    assert isinstance(destination, DynamoDbAuditDestination)
    assert destination.table_name == FAKE_TABLE


def test_a_dynamodb_setting_honours_the_local_endpoint(monkeypatch):
    """The job store already honours ``ADO2GH_DYNAMODB_ENDPOINT``; audit must agree.

    A local DynamoDB deployment that set only the job store's endpoint would
    otherwise send its audit writes to the real service instead.
    """
    seen = _install_fake_boto3(monkeypatch, FakeAuditTable())
    monkeypatch.setenv("ADO2GH_DYNAMODB_ENDPOINT", "http://localhost:8000")
    monkeypatch.setenv(AUDIT_DESTINATION_ENV_VAR, f"dynamodb://{FAKE_TABLE}")

    create_audit_destination()

    assert seen.get("endpoint_url") == "http://localhost:8000"
    assert seen.get("region_name"), "the region must still be passed"


@pytest.mark.parametrize("value", ["postgres://", "postgresql://"])
def test_a_bare_postgres_scheme_is_a_request_for_libpq_defaults(monkeypatch, value):
    """``postgres://`` with nothing after it is a valid connection string, not a typo."""
    built: dict = {}

    class _FakePostgresStateDB:
        def __init__(self, dsn: str) -> None:
            built["dsn"] = dsn

    monkeypatch.setattr("ado2gh.state.postgres_db.PostgresStateDB", _FakePostgresStateDB)
    monkeypatch.setenv(AUDIT_DESTINATION_ENV_VAR, value)

    create_audit_destination()

    assert built["dsn"] == value, "the whole setting must be handed over as the connection string"


def test_an_in_memory_sqlite_audit_database_is_refused(monkeypatch):
    """A destination that empties itself on exit is not a durable audit trail."""
    monkeypatch.setenv(AUDIT_DESTINATION_ENV_VAR, "sqlite://:memory:")

    with pytest.raises(ValueError, match="in-memory"):
        create_audit_destination()


@pytest.mark.parametrize("value", ["file:///tmp/audit.log", "dynamodb", "sqlite://", "nonsense"])
def test_an_unknown_value_is_refused_with_the_accepted_forms(monkeypatch, value):
    """A typo must name what is accepted rather than fall back to something."""
    monkeypatch.setenv(AUDIT_DESTINATION_ENV_VAR, value)

    with pytest.raises(ValueError) as excinfo:
        create_audit_destination()

    message = str(excinfo.value)
    assert AUDIT_DESTINATION_ENV_VAR in message
    for form in ("state", "dynamodb://", "sqlite://", "postgres"):
        assert form in message, f"the refusal does not name {form!r}: {message}"


# ─── The DynamoDB destination ────────────────────────────────────────


def test_dynamodb_destination_writes_the_documented_item_shape(monkeypatch):
    """One conditional put, with the five event fields plus a written-at stamp."""
    table = FakeAuditTable()
    _install_fake_boto3(monkeypatch, table)

    DynamoDbAuditDestination(FAKE_TABLE, "us-east-1").insert_audit_event(
        event_id="aud_000000000001",
        event_type="job.claim_conflict",
        profile_id="lightweight",
        actor="job-worker",
        payload_json=json.dumps({"job_id": "job-1", "backend": "dynamodb"}),
    )

    assert len(table.puts) == 1, f"expected one put, got {table.puts}"
    write = table.puts[0]
    assert write["ConditionExpression"] == DYNAMODB_NEW_ITEM_CONDITION
    item = write["Item"]
    assert set(item) == {
        "id", "event_type", "profile_id", "actor", "payload_json", DYNAMODB_CREATED_AT_FIELD,
    }
    assert item["id"] == "aud_000000000001"
    assert item[DYNAMODB_CREATED_AT_FIELD].endswith("+00:00"), (
        f"the timestamp is not coordinated universal time: {item[DYNAMODB_CREATED_AT_FIELD]}"
    )


def test_a_missing_dynamodb_table_names_the_table_and_the_variable(monkeypatch):
    """The table is never created here, so say what to create and why."""
    _install_fake_boto3(monkeypatch, FakeAuditTable(missing=True))

    with pytest.raises(RuntimeError) as excinfo:
        DynamoDbAuditDestination(FAKE_TABLE, "us-east-1").insert_audit_event(
            event_id="aud_000000000001", event_type="job.claim_conflict",
            profile_id="", actor="job-worker", payload_json="{}",
        )

    message = str(excinfo.value)
    assert FAKE_TABLE in message
    assert AUDIT_DESTINATION_ENV_VAR in message


# ─── Masking holds for every destination ─────────────────────────────


@pytest.mark.parametrize("build", ["recording", "dynamodb"])
def test_every_destination_receives_only_masked_values(monkeypatch, build):
    """Masking stays in the writer, so no destination can see a raw secret (CA-003)."""
    table = FakeAuditTable()
    if build == "dynamodb":
        _install_fake_boto3(monkeypatch, table)
        destination = DynamoDbAuditDestination(FAKE_TABLE, "us-east-1")
    else:
        destination = RecordingDestination()

    AuditWriter(destination).write(
        "job.claim_conflict",
        profile_id="lightweight",
        actor="job-worker",
        payload={"token": FAKE_TOKEN, "nested": {"gh_token": FAKE_TOKEN}},
    )

    written = json.dumps(table.puts if build == "dynamodb" else destination.rows)
    assert FAKE_TOKEN not in written, f"the destination received a raw secret: {written}"


# ─── The job store fails closed ──────────────────────────────────────


def test_a_dynamodb_job_store_refuses_to_start_without_a_destination(monkeypatch):
    """The case GAP-069 named: no audit destination, so do not start at all."""
    _install_fake_boto3(monkeypatch, FakeAuditTable())
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "dynamodb")
    monkeypatch.setenv("ADO2GH_DYNAMODB_TABLE", "ado2gh")

    from ado2gh.state.job_store import JobStoreFactory

    with pytest.raises(ValueError) as excinfo:
        JobStoreFactory.from_env()

    assert AUDIT_DESTINATION_ENV_VAR in str(excinfo.value)


def test_a_dynamodb_job_store_starts_once_a_destination_is_configured(monkeypatch, tmp_path):
    """With somewhere to audit, the same deployment comes up as before."""
    _install_fake_boto3(monkeypatch, FakeAuditTable())
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "dynamodb")
    monkeypatch.setenv("ADO2GH_DYNAMODB_TABLE", "ado2gh")
    monkeypatch.setenv(AUDIT_DESTINATION_ENV_VAR, f"sqlite://{tmp_path / 'audit.db'}")

    from ado2gh.state.job_store import DynamoDBJobStore, JobStoreFactory

    store = JobStoreFactory.from_env()

    assert isinstance(store, DynamoDBJobStore)
    assert isinstance(store._audit_writer, AuditWriter)
