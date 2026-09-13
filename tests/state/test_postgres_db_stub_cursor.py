"""The SQL the PostgreSQL backend emits, recorded against a stub cursor (COV-DRIFT-005).

`CLAUDE.md` § State Persistence names PostgreSQL as the production backend while
the suite exercises SQLite only: 149 of `postgres_db.py`'s 186 statements were
unexecuted after this feature churned 456 lines across the Postgres modules. A
SQL-level defect — a renamed column in one backend, a parameter order that
drifted — would pass CI and surface first in a production migration run.

No server is contacted and `psycopg2` is never asked to connect: `connect` is
replaced with a recording double, so what is asserted is the statement text, the
bound parameters and the transaction handling. `psycopg2` happens to be
installed here, but nothing in this module depends on that beyond the import
inside `PostgresStateDB.__init__`.

The DSN literal is obviously fake and is never logged (CA-003).
"""
from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest

from ado2gh.models import (
    MigrationStatus,
    PipelineComplexity,
    PipelineMetadata,
    PipelineType,
    RepoConfig,
)
from ado2gh.state.postgres_db import PostgresStateDB

FAKE_DSN = "postgresql://stub:stub@postgres.invalid:5432/stub"
REPO = RepoConfig(
    ado_project="Contoso", ado_repo="payments",
    gh_org="fake-gh-org", gh_repo="payments",
)


def _meta() -> PipelineMetadata:
    return PipelineMetadata(
        pipeline_id=7,
        pipeline_name="Payments CI",
        pipeline_type=PipelineType.YAML,
        project="Contoso",
        repo_id="repo-guid",
        repo_name="payments",
        folder="\\CI",
        complexity=PipelineComplexity.MEDIUM,
    )


class StubCursor:
    """Records every statement and answers ``fetch*`` from a prepared queue."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple | None]] = []
        self.factories: list[object] = []
        self.rows: list[dict] = []

    def __enter__(self) -> "StubCursor":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.executed.append((sql, params))

    def fetchall(self) -> list[dict]:
        return self.rows

    def fetchone(self) -> dict | None:
        return self.rows[0] if self.rows else None

    # -- helpers -------------------------------------------------------
    def statement(self, keyword: str) -> tuple[str, tuple | None]:
        """The first recorded statement whose text contains ``keyword``."""
        for sql, params in self.executed:
            if keyword in sql:
                return sql, params
        raise AssertionError(f"no statement contains {keyword!r}")

    @property
    def writes(self) -> list[tuple[str, tuple | None]]:
        """Statements recorded after schema creation."""
        return [
            (sql, params) for sql, params in self.executed
            if not sql.lstrip().upper().startswith(("CREATE", "ALTER"))
        ]


class StubConnection:
    """Hands out one cursor and records commit, rollback and close."""

    def __init__(self, cursor: StubCursor) -> None:
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self, **kwargs: object) -> StubCursor:
        self._cursor.factories.append(kwargs.get("cursor_factory"))
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


@pytest.fixture
def db():
    """A Postgres store whose driver is the recording double.

    Returns the store, its cursor and its connection. The cursor's log is
    cleared after construction so each test sees only its own statements.
    """
    cursor = StubCursor()
    connection = StubConnection(cursor)
    with patch("psycopg2.connect", return_value=connection) as connect:
        store = PostgresStateDB(FAKE_DSN)
        schema_statements = list(cursor.executed)
        cursor.executed.clear()
        connection.commits = 0
        connection.rollbacks = 0
        connection.closes = 0
        yield store, cursor, connection, connect, schema_statements


# --------------------------------------------------------------------------
# Construction and schema
# --------------------------------------------------------------------------


def test_the_dsn_is_kept_and_used_for_every_connection(db):
    store, _, _, connect, _ = db
    assert store.dsn == FAKE_DSN
    assert connect.call_args.args == (FAKE_DSN,)


def test_construction_creates_every_declared_table(db):
    _, _, _, _, schema_statements = db
    created = {
        match.group(1)
        for sql, _ in schema_statements
        for match in [re.search(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql, re.I)]
        if match
    }
    declared = set(
        re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", PostgresStateDB.SCHEMA, re.I),
    )
    assert created == declared
    assert created, "no table was created at all"


def test_construction_applies_the_platform_user_status_upgrade(db):
    _, _, _, _, schema_statements = db
    alters = [sql for sql, _ in schema_statements if sql.lstrip().upper().startswith("ALTER")]
    assert any(
        "platform_users" in sql and "ADD COLUMN IF NOT EXISTS" in sql and "status" in sql
        for sql in alters
    ), "the in-place status column upgrade was not applied"


def test_no_schema_statement_is_a_bare_comment(db):
    _, _, _, _, schema_statements = db
    assert all(not sql.lstrip().startswith("--") for sql, _ in schema_statements)


# --------------------------------------------------------------------------
# Transaction handling
# --------------------------------------------------------------------------


def test_a_successful_operation_commits_once_and_closes(db):
    store, _, connection, _, _ = db
    store.reset_failed_pipeline_migrations(1)
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closes >= 1


def test_a_failing_operation_rolls_back_closes_and_re_raises(db):
    store, cursor, connection, _, _ = db

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("constraint violated")

    cursor.execute = boom  # type: ignore[method-assign]
    closes_before = connection.closes
    with pytest.raises(RuntimeError, match="constraint violated"):
        store.reset_failed_pipeline_migrations(1)
    assert connection.rollbacks == 1
    assert connection.commits == 0
    assert connection.closes == closes_before + 1, "the connection was leaked on failure"


# --------------------------------------------------------------------------
# migrations
# --------------------------------------------------------------------------


def test_upsert_migration_names_every_column_and_the_conflict_key(db):
    store, cursor, _, _, _ = db
    store.upsert_migration(1, REPO, "repo", MigrationStatus.IN_PROGRESS)
    sql, params = cursor.statement("INSERT INTO migrations")
    for column in (
        "wave_id", "ado_project", "ado_repo", "gh_org", "gh_repo", "scope",
        "status", "started_at", "completed_at", "error_message",
        "gh_migration_id", "stats",
    ):
        assert column in sql, f"{column} is missing from the insert"
    assert "ON CONFLICT(wave_id, ado_project, ado_repo, scope)" in sql
    assert sql.count("%s") == 12, "the placeholder count no longer matches the columns"
    assert len(params) == 12


def test_upsert_migration_binds_the_repo_fields_in_column_order(db):
    store, cursor, _, _, _ = db
    store.upsert_migration(3, REPO, "pipelines", MigrationStatus.COMPLETED)
    _, params = cursor.statement("INSERT INTO migrations")
    assert params[:7] == (
        3, "Contoso", "payments", "fake-gh-org", "payments", "pipelines", "completed",
    )


@pytest.mark.parametrize(
    "status,started,completed",
    [
        (MigrationStatus.IN_PROGRESS, True, False),
        (MigrationStatus.COMPLETED, False, True),
        (MigrationStatus.FAILED, False, True),
        (MigrationStatus.ROLLED_BACK, False, True),
        (MigrationStatus.PENDING, False, False),
    ],
)
def test_the_timestamp_columns_are_set_only_for_the_statuses_that_earn_them(
    db, status, started, completed,
):
    store, cursor, _, _, _ = db
    store.upsert_migration(1, REPO, "repo", status)
    _, params = cursor.statement("INSERT INTO migrations")
    assert (params[7] is not None) is started, f"started_at wrong for {status}"
    assert (params[8] is not None) is completed, f"completed_at wrong for {status}"


def test_a_started_timestamp_already_recorded_is_never_overwritten(db):
    """A retry must not reset when the migration first began."""
    store, cursor, _, _, _ = db
    store.upsert_migration(1, REPO, "repo", MigrationStatus.COMPLETED)
    sql, _ = cursor.statement("INSERT INTO migrations")
    assert "started_at = COALESCE(migrations.started_at, EXCLUDED.started_at)" in sql


def test_the_error_and_migration_id_are_bound_verbatim(db):
    store, cursor, _, _, _ = db
    store.upsert_migration(
        1, REPO, "repo", MigrationStatus.FAILED,
        error="push rejected", gh_migration_id="RM_123",
    )
    _, params = cursor.statement("INSERT INTO migrations")
    assert params[9] == "push rejected"
    assert params[10] == "RM_123"


def test_stats_are_serialised_as_json_and_omitted_when_absent(db):
    store, cursor, _, _, _ = db
    store.upsert_migration(1, REPO, "repo", MigrationStatus.COMPLETED, stats={"branches": 4})
    _, params = cursor.statement("INSERT INTO migrations")
    assert json.loads(params[11]) == {"branches": 4}

    cursor.executed.clear()
    store.upsert_migration(1, REPO, "repo", MigrationStatus.COMPLETED)
    _, params = cursor.statement("INSERT INTO migrations")
    assert params[11] is None


def test_reading_a_waves_migrations_asks_for_dictionary_rows(db):
    store, cursor, _, _, _ = db
    cursor.rows = [{"id": 1, "status": "completed"}]
    assert store.get_wave_migrations(4) == [{"id": 1, "status": "completed"}]
    sql, params = cursor.statement("FROM migrations WHERE wave_id")
    assert params == (4,)
    assert "ORDER BY id" in sql
    assert cursor.factories[-1] is not None, "rows came back positionally, not by name"


def test_reading_every_migration_orders_by_wave_then_insertion(db):
    store, cursor, _, _, _ = db
    store.get_all_migrations()
    sql, params = cursor.statement("SELECT * FROM migrations ORDER BY")
    assert "ORDER BY wave_id, id" in sql
    assert params is None


# --------------------------------------------------------------------------
# pipeline_inventory
# --------------------------------------------------------------------------


def test_upsert_pipeline_inventory_names_every_column_and_the_conflict_key(db):
    store, cursor, _, _, _ = db
    store.upsert_pipeline_inventory(_meta())
    sql, params = cursor.statement("INSERT INTO pipeline_inventory")
    for column in (
        "project", "pipeline_id", "pipeline_name", "pipeline_type",
        "repo_id", "repo_name", "folder", "complexity", "metadata_json", "scanned_at",
    ):
        assert column in sql, f"{column} is missing from the insert"
    assert "ON CONFLICT(project, pipeline_id)" in sql
    assert sql.count("%s") == 10
    assert len(params) == 10


def test_the_inventory_row_carries_the_enum_values_not_the_enum_objects(db):
    store, cursor, _, _, _ = db
    store.upsert_pipeline_inventory(_meta())
    _, params = cursor.statement("INSERT INTO pipeline_inventory")
    assert params[:8] == (
        "Contoso", 7, "Payments CI", "yaml", "repo-guid", "payments", "\\CI", "medium",
    )


def test_the_whole_metadata_record_is_stored_as_json(db):
    store, cursor, _, _, _ = db
    store.upsert_pipeline_inventory(_meta())
    _, params = cursor.statement("INSERT INTO pipeline_inventory")
    stored = json.loads(params[8])
    assert stored["pipeline_name"] == "Payments CI"
    assert stored["pipeline_type"] == "yaml"


def test_counting_the_inventory_can_be_scoped_to_one_project(db):
    store, cursor, _, _, _ = db
    cursor.rows = [(3,)]  # the count is read positionally, not by name
    assert store.inventory_count("Contoso") == 3
    sql, params = cursor.statement("FROM pipeline_inventory")
    assert params == ("Contoso",)
    assert "project" in sql


# --------------------------------------------------------------------------
# pipeline_migrations
# --------------------------------------------------------------------------


def test_upsert_pipeline_migration_names_every_column_and_the_conflict_key(db):
    store, cursor, _, _, _ = db
    store.upsert_pipeline_migration(1, _meta(), REPO, MigrationStatus.COMPLETED)
    sql, params = cursor.statement("INSERT INTO pipeline_migrations")
    for column in (
        "wave_id", "project", "pipeline_id", "pipeline_name", "repo_name",
        "gh_org", "gh_repo", "workflow_file", "status", "started_at",
        "completed_at", "error_message", "warnings", "unsupported_tasks",
        "complexity", "transform_stats",
    ):
        assert column in sql, f"{column} is missing from the insert"
    assert "ON CONFLICT(wave_id, project, pipeline_id)" in sql
    assert sql.count("%s") == 16
    assert len(params) == 16


def test_the_pipeline_migration_row_binds_its_identity_in_column_order(db):
    store, cursor, _, _, _ = db
    store.upsert_pipeline_migration(
        2, _meta(), REPO, MigrationStatus.COMPLETED, workflow_file="ci.yml",
    )
    _, params = cursor.statement("INSERT INTO pipeline_migrations")
    assert params[:9] == (
        2, "Contoso", 7, "Payments CI", "payments",
        "fake-gh-org", "payments", "ci.yml", "completed",
    )


def test_warnings_and_unsupported_tasks_default_to_empty_json_arrays(db):
    store, cursor, _, _, _ = db
    store.upsert_pipeline_migration(1, _meta(), REPO, MigrationStatus.COMPLETED)
    _, params = cursor.statement("INSERT INTO pipeline_migrations")
    assert json.loads(params[12]) == []
    assert json.loads(params[13]) == []
    assert json.loads(params[15]) == {}


def test_warnings_and_unsupported_tasks_are_serialised_when_present(db):
    store, cursor, _, _, _ = db
    store.upsert_pipeline_migration(
        1, _meta(), REPO, MigrationStatus.FAILED,
        warnings=["classic pipeline"], unsupported=["Vendor@1"],
        transform_stats={"steps": 4},
    )
    _, params = cursor.statement("INSERT INTO pipeline_migrations")
    assert json.loads(params[12]) == ["classic pipeline"]
    assert json.loads(params[13]) == ["Vendor@1"]
    assert params[14] == "medium"
    assert json.loads(params[15]) == {"steps": 4}


@pytest.mark.parametrize(
    "status,started,completed",
    [
        (MigrationStatus.IN_PROGRESS, True, False),
        (MigrationStatus.COMPLETED, False, True),
        (MigrationStatus.FAILED, False, True),
        (MigrationStatus.ROLLED_BACK, False, False),
    ],
)
def test_the_pipeline_timestamp_columns_track_the_status(db, status, started, completed):
    store, cursor, _, _, _ = db
    store.upsert_pipeline_migration(1, _meta(), REPO, status)
    _, params = cursor.statement("INSERT INTO pipeline_migrations")
    assert (params[9] is not None) is started
    assert (params[10] is not None) is completed


def test_resetting_failed_pipeline_rows_deletes_only_the_failed_ones_of_that_wave(db):
    store, cursor, _, _, _ = db
    store.reset_failed_pipeline_migrations(9)
    sql, params = cursor.statement("DELETE FROM pipeline_migrations")
    assert params == (9,)
    assert "wave_id=%s" in sql
    assert "status='failed'" in sql


def test_reading_a_waves_pipeline_migrations_orders_by_insertion(db):
    store, cursor, _, _, _ = db
    store.get_wave_pipeline_migrations(5)
    sql, params = cursor.statement("FROM pipeline_migrations WHERE wave_id")
    assert params == (5,)
    assert "ORDER BY id" in sql


# --------------------------------------------------------------------------
# No credential ever reaches a statement
# --------------------------------------------------------------------------


def test_no_statement_or_parameter_carries_the_connection_string(db):
    store, cursor, _, _, schema_statements = db
    store.upsert_migration(1, REPO, "repo", MigrationStatus.COMPLETED)
    store.upsert_pipeline_inventory(_meta())
    for sql, params in schema_statements + cursor.executed:
        assert FAKE_DSN not in sql
        assert FAKE_DSN not in str(params)
