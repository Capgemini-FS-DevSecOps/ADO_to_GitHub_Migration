"""Test that a shared base class exists and both backends inherit from it."""
from __future__ import annotations


def test_base_class_exists():
    """ado2gh.state.base should contain an abstract base class."""
    from ado2gh.state.base import StateDBBase
    assert StateDBBase is not None


def test_sqlite_inherits_base():
    """SQLiteStateDB should inherit from StateDBBase."""
    from ado2gh.state.base import StateDBBase
    from ado2gh.state.sqlite_db import SQLiteStateDB
    assert issubclass(SQLiteStateDB, StateDBBase)


def test_postgres_inherits_base():
    """PostgresStateDB should inherit from StateDBBase."""
    from ado2gh.state.base import StateDBBase
    from ado2gh.state.postgres_db import PostgresStateDB
    assert issubclass(PostgresStateDB, StateDBBase)
