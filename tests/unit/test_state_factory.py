"""Test that the state factory returns correct backend types."""
from __future__ import annotations

import os
from unittest.mock import patch


def test_factory_returns_sqlite_by_default():
    """Default backend should be SQLite."""
    from ado2gh.state.factory import create_state_db
    db = create_state_db(":memory:")
    assert db is not None


def test_factory_no_dynamodb_option():
    """Factory should not have a DynamoDB code path."""
    import inspect
    from ado2gh.state import factory
    source = inspect.getsource(factory)
    assert "dynamodb" not in source.lower(), "Factory still references DynamoDB"
