"""CheckpointStorageSettings must resolve to the same defaults the graph
builder's `_get_checkpointer` hardcodes today, from the same environment
variables, so the builder can be pointed at this resolver without changing
behaviour.
"""
import pytest

from ado2gh.state.storage_config import CheckpointStorageSettings


@pytest.fixture(autouse=True)
def clear_checkpoint_env(monkeypatch):
    for key in (
        "ADO2GH_STORAGE_BACKEND",
        "ADO2GH_SQLITE_PATH",
        "ADO2GH_DATABASE_URL",
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "PGDATABASE",
    ):
        monkeypatch.delenv(key, raising=False)


def test_defaults_match_the_builders_current_literals():
    settings = CheckpointStorageSettings.from_env()
    assert settings.backend == "sqlite"
    assert settings.sqlite_path == "data/agent_checkpoints.db"
    assert settings.database_url == ""
    assert settings.pg_host == "localhost"
    assert settings.pg_port == 5432
    assert settings.pg_user == "ado2gh"
    assert settings.pg_password == ""
    assert settings.pg_dbname == "ado2gh"
    assert settings.is_postgres() is False


def test_postgresql_alias_is_recognized_same_as_postgres(monkeypatch):
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "postgresql")
    assert CheckpointStorageSettings.from_env().is_postgres() is True
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "postgres")
    assert CheckpointStorageSettings.from_env().is_postgres() is True


def test_sqlite_path_reads_ado2gh_sqlite_path(monkeypatch):
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", "custom/checkpoints.db")
    assert CheckpointStorageSettings.from_env().sqlite_path == "custom/checkpoints.db"


def test_postgres_dsn_built_from_pg_fields(monkeypatch):
    monkeypatch.setenv("PGHOST", "db.example.com")
    monkeypatch.setenv("PGPORT", "6543")
    monkeypatch.setenv("PGUSER", "alice")
    monkeypatch.setenv("PGPASSWORD", "secret")
    monkeypatch.setenv("PGDATABASE", "mydb")
    settings = CheckpointStorageSettings.from_env()
    assert settings.postgres_dsn() == "postgresql://alice:secret@db.example.com:6543/mydb"


def test_database_url_read_verbatim(monkeypatch):
    monkeypatch.setenv("ADO2GH_DATABASE_URL", "postgresql://x:y@h/z")
    assert CheckpointStorageSettings.from_env().database_url == "postgresql://x:y@h/z"
