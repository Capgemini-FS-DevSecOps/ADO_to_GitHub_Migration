"""Isolate the suite from developer-local runtime state.

The agent and accelerator read onboarded LLM models, cloud credentials and UI
settings out of ``$ADO2GH_DATA_DIR`` (default: the current directory, i.e. the
repo root). On a developer machine that directory holds real, *enabled* models,
so session and message tests issue live LLM calls — against a local Ollama they
do not fail, they answer, and the suite hangs for minutes per test. CI has no
such directory; pointing every test at an empty one restores parity.

``$ADO2GH_SQLITE_PATH`` gets the same treatment, per test. Unset, the
LangGraph checkpointer opens ``data/agent_checkpoints.db`` — the developer's
real agent session store — and the auth, live-approval and profile stores open
``migration_state.db`` in the repo root. Any test that reaches ``/health`` or
``get_compiled_graph()`` would otherwise write into live migration state, and
a hot WAL left behind by a killed run wedges the next one on open. The path is
function-scoped because ``create_state_db()`` lets the variable override an
explicit ``db_path``: one session-wide file would make every test that passes
its own path share a single database.

Tests that need their own data dir or database keep monkeypatching these
variables; a test's own monkeypatch overrides these defaults.

The same fixture also closes the cached LangGraph checkpointer at session end.
Its aiosqlite connection owns a *non-daemon* worker thread that blocks on its
queue until the connection is closed, so an abandoned one wedges
``threading._shutdown`` and pytest never exits after the last test.
"""
from __future__ import annotations

import os

import pytest



@pytest.fixture(scope="session", autouse=True)
def _isolated_data_dir(tmp_path_factory):
    previous = os.environ.get("ADO2GH_DATA_DIR")
    os.environ["ADO2GH_DATA_DIR"] = str(tmp_path_factory.mktemp("ado2gh-data"))
    yield
    from ado2gh.agents.migration_agent.graph.builder import reset_compiled_graph

    reset_compiled_graph()
    if previous is None:
        os.environ.pop("ADO2GH_DATA_DIR", None)
    else:
        os.environ["ADO2GH_DATA_DIR"] = previous


@pytest.fixture(autouse=True)
def _isolated_sqlite_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "migration_state.db"))
