"""Regression check for register entry GAP-051: the suite must not open the developer's real agent checkpoint DB.

``_get_checkpointer`` resolves its SQLite file from ``ADO2GH_SQLITE_PATH`` and
falls back to ``data/agent_checkpoints.db`` in the working directory. The
session fixture in ``tests/conftest.py`` points that variable at a temporary
file; this test asks the opened connection which file it actually holds.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ado2gh.agents.migration_agent.graph import builder

REPO_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def test_checkpointer_does_not_open_the_repository_data_dir():
    async def opened_file() -> Path:
        checkpointer = await builder._get_checkpointer()
        async with checkpointer.conn.execute("PRAGMA database_list") as cursor:
            rows = await cursor.fetchall()
        return Path(rows[0][2]).resolve()

    db_file = asyncio.run(opened_file())
    builder.reset_compiled_graph()

    assert not db_file.is_relative_to(REPO_DATA_DIR), db_file
    assert db_file.name != "agent_checkpoints.db", db_file
