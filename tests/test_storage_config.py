"""Tests for storage backend configuration."""
from __future__ import annotations

import os

import pytest

from ado2gh.infra.state.storage_config import StorageBackend, StorageConfig
from ado2gh.state.factory import create_state_db
from ado2gh.state.db import StateDB


@pytest.fixture(autouse=True)
def clear_storage_env(monkeypatch):
    for key in (
        "ADO2GH_STORAGE_BACKEND",
        "ADO2GH_SQLITE_PATH",
        "ADO2GH_DATABASE_URL",
        "ADO2GH_DYNAMODB_TABLE",
        "ADO2GH_DYNAMODB_ENDPOINT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_default_backend_is_sqlite():
    cfg = StorageConfig.from_env()
    assert cfg.backend == StorageBackend.SQLITE
    assert cfg.sqlite_path == "migration_state.db"


def test_explicit_sqlite_overrides_postgres_url(monkeypatch):
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ADO2GH_DATABASE_URL", "postgresql://user:pass@localhost/db")
    cfg = StorageConfig.from_env()
    assert cfg.backend == StorageBackend.SQLITE


def test_postgres_requires_database_url(monkeypatch):
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "postgres")
    with pytest.raises(ValueError, match="ADO2GH_DATABASE_URL"):
        StorageConfig.from_env()


def test_create_state_db_returns_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", db_path)
    db = create_state_db(db_path)
    assert isinstance(db, StateDB)
    assert db.db_path == db_path


def test_postgres_schema_includes_agentic_tables():
    from ado2gh.state.postgres_db import PostgresStateDB
    assert "migration_assignments" in PostgresStateDB.SCHEMA
    assert "audit_events" in PostgresStateDB.SCHEMA
    assert "remediation_loops" in PostgresStateDB.SCHEMA
