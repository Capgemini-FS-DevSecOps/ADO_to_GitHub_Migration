"""GAP-029: ``create_state_db()`` must select its backend explicitly and losslessly.

The register entry (``specs/013-clean-code-arch-remediation/gap-register.md``,
GAP-029) faults ``ado2gh/state/factory.py`` on three counts:

1. the caller's ``db_path`` (the ``--db`` value) reaches only the SQLite branch,
   so under ``ADO2GH_STORAGE_BACKEND=postgres`` an operator who asks for a
   scratch file is *silently* redirected to the shared production database;
2. ``ADO2GH_STORAGE_BACKEND=dynamodb`` — a value ``StorageConfig.from_env()``
   validates and returns — falls through to ``raise ValueError("Unsupported
   storage backend: ...")``, a message that never names the variable that
   caused it;
3. the backend is never visible at the call site.

The fix keeps ``db_path`` first and positional (88 call sites across 54 files
pass it that way) and adds a keyword-only ``backend`` that defaults to the
environment, so the silence is replaced by a warning and the crash by a message
that says what to do.

Parity is asserted from **declared DDL**, never a live server: ``SCHEMA`` is a
class constant on both state stores, and the Postgres store is stubbed out
wherever the factory would construct one. ``DynamoDBJobStore`` is introspected
statically (``JobStore.__abstractmethods__`` and the AST of ``_save``) because
``boto3`` is not installed in this environment and ``job_store.py`` is out of
this fix's edit scope.

T081 says "the same 25 tables"; both stores in fact declare **13** — the count
in the task text predates the state/session split. The assertion below is set
equality plus the measured count, so it fails either way if the two drift.
"""
from __future__ import annotations

import ast
import inspect
import re
import sqlite3
import textwrap
from typing import TYPE_CHECKING

import pytest

from ado2gh.state.factory import create_state_db
from ado2gh.state.job_store import DynamoDBJobStore, JobStore, SQLiteJobStore
from ado2gh.state.postgres_db import PostgresStateDB
from ado2gh.state.sqlite_db import SQLiteStateDB
from ado2gh.state.storage_config import StorageBackend

if TYPE_CHECKING:
    from pathlib import Path

# Obviously-fake DSN; no server is ever contacted (CA-003).
FAKE_DSN = "postgresql://stub:stub@postgres.invalid:5432/stub"
TABLE_DDL = re.compile(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", re.IGNORECASE)
EXPECTED_TABLE_COUNT = 13


@pytest.fixture(autouse=True)
def clear_storage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop every storage variable, including the one ``tests/conftest.py`` sets.

    ``ADO2GH_SQLITE_PATH`` outranks an explicit ``db_path`` (GAP-051 relies on
    that, and this fix deliberately leaves it alone), so it has to go before
    any assertion about ``db_path`` means anything.
    """
    for key in (
        "ADO2GH_STORAGE_BACKEND",
        "ADO2GH_SQLITE_PATH",
        "ADO2GH_DATABASE_URL",
        "ADO2GH_DYNAMODB_TABLE",
        "ADO2GH_DYNAMODB_ENDPOINT",
    ):
        monkeypatch.delenv(key, raising=False)


class StubPostgresStateDB:
    """Stand-in for ``PostgresStateDB``; records the DSN instead of connecting."""

    def __init__(self, dsn: str) -> None:
        """Remember the connection string the factory chose."""
        self.dsn = dsn


@pytest.fixture
def postgres(monkeypatch: pytest.MonkeyPatch) -> type[StubPostgresStateDB]:
    """Select the Postgres backend with its store stubbed out."""
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("ADO2GH_DATABASE_URL", FAKE_DSN)
    monkeypatch.setattr(
        "ado2gh.state.postgres_db.PostgresStateDB", StubPostgresStateDB
    )
    return StubPostgresStateDB


# ─── (a) schema parity between the two state backends ────────────────


def test_sqlite_and_postgres_declare_the_same_tables() -> None:
    """Both state backends must create one identical set of tables."""
    sqlite_tables = set(TABLE_DDL.findall(SQLiteStateDB.SCHEMA))
    postgres_tables = set(TABLE_DDL.findall(PostgresStateDB.SCHEMA))

    assert sqlite_tables == postgres_tables, (
        f"only in SQLite: {sorted(sqlite_tables - postgres_tables)}; "
        f"only in Postgres: {sorted(postgres_tables - sqlite_tables)}"
    )
    assert len(sqlite_tables) == EXPECTED_TABLE_COUNT


def test_a_real_sqlite_file_holds_exactly_the_declared_tables(tmp_path: Path) -> None:
    """The DDL above is what a constructed store actually writes to disk."""
    db_path = tmp_path / "parity.db"
    create_state_db(str(db_path))

    with sqlite3.connect(db_path) as conn:
        live = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row[0].startswith("sqlite_")  # sqlite_sequence, from AUTOINCREMENT
        }

    assert live == set(TABLE_DDL.findall(PostgresStateDB.SCHEMA))


# ─── (b) job-store parity: DynamoDB against the JobStore ABC ─────────


def test_dynamodb_job_store_implements_every_abstract_method() -> None:
    """``DynamoDBJobStore`` must leave no ``JobStore`` abstract method unimplemented."""
    assert JobStore.__abstractmethods__, "JobStore declares no abstract methods"
    assert DynamoDBJobStore.__abstractmethods__ == frozenset(), (
        f"not implemented: {sorted(DynamoDBJobStore.__abstractmethods__)}"
    )


@pytest.mark.parametrize("name", sorted(JobStore.__abstractmethods__))
def test_dynamodb_job_store_keeps_the_abstract_signature(name: str) -> None:
    """Each implementation must be callable exactly as the ABC promises."""
    expected = inspect.signature(getattr(JobStore, name))
    assert inspect.signature(getattr(DynamoDBJobStore, name)) == expected
    assert inspect.signature(getattr(SQLiteJobStore, name)) == expected


def test_dynamodb_job_store_persists_the_same_audit_fields() -> None:
    """The DynamoDB item must carry every column the SQLite ``jobs`` table has.

    ``created_at``/``updated_at``/``status``/``error`` are the audit trail of a
    job; a backend that drops one loses it for that deployment only.
    """
    columns = set()
    for raw in SQLiteJobStore.SCHEMA.splitlines():
        line = raw.strip()
        if line and not line.upper().startswith(("CREATE", ")", ";")):
            columns.add(line.split()[0])

    saved = ast.parse(textwrap.dedent(inspect.getsource(DynamoDBJobStore._save)))
    item_keys = {
        key.value
        for node in ast.walk(saved)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }

    assert columns <= item_keys, f"DynamoDB drops: {sorted(columns - item_keys)}"


# ─── (c) the GAP-029 behaviours ──────────────────────────────────────


def test_db_path_is_honoured_for_sqlite(tmp_path: Path) -> None:
    """The caller's path is the SQLite file when nothing in the environment wins."""
    db_path = str(tmp_path / "scratch.db")
    assert create_state_db(db_path).db_path == db_path


def test_explicit_db_path_under_postgres_is_not_silently_discarded(
    postgres: type[StubPostgresStateDB],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A scratch ``--db`` that Postgres cannot honour must be reported, not dropped."""
    with caplog.at_level("WARNING", logger="ado2gh.state.factory"):
        store = create_state_db("scratch.db")

    assert isinstance(store, postgres)
    assert caplog.records, "the discarded db_path was not reported at all"
    message = " ".join(record.getMessage() for record in caplog.records)
    assert "scratch.db" in message
    assert "ADO2GH_STORAGE_BACKEND" in message
    assert FAKE_DSN not in message, "the warning leaked the database URL (CA-003)"


def test_default_db_path_under_postgres_is_quiet(
    postgres: type[StubPostgresStateDB],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Callers that never chose a path must not be warned on every call."""
    with caplog.at_level("WARNING", logger="ado2gh.state.factory"):
        create_state_db()

    assert not caplog.records, f"warned about a path nobody asked for: {caplog.records}"


def test_dynamodb_backend_names_the_variable_and_the_job_store() -> None:
    """DynamoDB has no state store; the error must say so and name the variable.

    ``StorageConfig.from_env()`` accepts ``dynamodb`` because the *job* store
    supports it (``JobStoreFactory.from_env``); only the state store does not.
    """
    with pytest.raises(ValueError) as excinfo:  # noqa: PT011 — message is the assertion
        create_state_db(backend=StorageBackend.DYNAMODB)

    message = str(excinfo.value)
    assert "ADO2GH_STORAGE_BACKEND" in message
    assert "dynamodb" in message.lower()
    assert "job" in message.lower(), f"does not mention the job store: {message}"


def test_explicit_backend_overrides_the_environment(tmp_path: Path) -> None:
    """Passing the backend makes the selection visible at the call site."""
    db_path = str(tmp_path / "explicit.db")
    store = create_state_db(db_path, backend=StorageBackend.SQLITE)

    assert isinstance(store, SQLiteStateDB)
    assert store.db_path == db_path


def test_explicit_postgres_without_a_url_is_rejected() -> None:
    """An explicit Postgres request still has to be configured."""
    with pytest.raises(ValueError, match="ADO2GH_DATABASE_URL"):
        create_state_db(backend=StorageBackend.POSTGRES)
