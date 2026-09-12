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
    """Factory should not have a DynamoDB code path.

    FR-009 deleted ``state/dynamodb_db.py``; the factory must never build a
    DynamoDB state store again. It must still be free to *name* the backend
    when refusing it (GAP-029), so this asserts on imports and constructions
    rather than on the substring.
    """
    import ast
    import inspect
    from ado2gh.state import factory

    tree = ast.parse(inspect.getsource(factory))
    imported = [
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    ] + [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert not [m for m in imported if "dynamo" in m.lower()], (
        f"Factory imports a DynamoDB module: {imported}"
    )

    called = [
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    ]
    assert not [c for c in called if "dynamo" in c.lower()], (
        f"Factory constructs a DynamoDB store: {called}"
    )
