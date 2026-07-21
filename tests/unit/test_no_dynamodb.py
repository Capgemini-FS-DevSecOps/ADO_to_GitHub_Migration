"""Test that DynamoDB backend is fully removed."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_dynamodb_db_file_deleted():
    """ado2gh/state/dynamodb_db.py must not exist."""
    assert not (REPO_ROOT / "ado2gh" / "state" / "dynamodb_db.py").exists()


def test_no_dynamodb_imports_remain():
    """No Python file in ado2gh/ or services/ should import from ado2gh.state.dynamodb_db."""
    import ast

    for root_dir in ("ado2gh", "services"):
        for dirpath, dirs, files in Path(REPO_ROOT / root_dir).walk():
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if not f.endswith(".py"):
                    continue
                filepath = dirpath / f
                try:
                    tree = ast.parse(filepath.read_text(encoding="utf-8"), str(filepath))
                except Exception:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            assert "ado2gh.state.dynamodb" not in alias.name, (
                                f"{filepath} imports {alias.name}"
                            )
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        assert "ado2gh.state.dynamodb" not in node.module, (
                            f"{filepath} imports from {node.module}"
                        )
