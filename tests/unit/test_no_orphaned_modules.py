"""Test that no Python module in ado2gh/ or services/ is orphaned (zero inbound imports).

Excludes entry points (__init__.py, __main__.py, CLI entry, service mains) and
modules known to be imported dynamically.
"""
from __future__ import annotations

import ast
import os
from collections import defaultdict

import pytest


def _filepath_to_module(filepath: str) -> str:
    rel = filepath.replace(os.sep, "/").replace(".py", "")
    parts = rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _build_import_graph() -> tuple[dict[str, str], dict[str, set[str]]]:
    # Collect all Python modules in ado2gh/ and services/ (the scope we audit)
    modules: dict[str, str] = {}
    for root_dir in ("ado2gh", "services"):
        for dirpath, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if not f.endswith(".py"):
                    continue
                filepath = os.path.join(dirpath, f)
                mod_name = _filepath_to_module(filepath)
                modules[mod_name] = filepath

    # Build reverse import map by scanning ado2gh/, services/, AND tests/
    # (modules imported only by tests are still alive, not orphaned)
    reverse: dict[str, set[str]] = defaultdict(set)
    for root_dir in ("ado2gh", "services", "tests"):
        for dirpath, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if not f.endswith(".py"):
                    continue
                filepath = os.path.join(dirpath, f)
                try:
                    with open(filepath, encoding="utf-8") as fh:
                        tree = ast.parse(fh.read(), filepath)
                except Exception:
                    continue
                importer_mod = _filepath_to_module(filepath)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            reverse[alias.name].add(importer_mod)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        reverse[node.module].add(importer_mod)
    return modules, reverse


# Entry points and dynamically imported modules that are allowed to have
# zero inbound imports from within ado2gh/ or services/.
_ENTRY_POINTS = {
    "ado2gh",
    "ado2gh.__main__",
    "ado2gh.cli.main",
    "services.accelerator_api.main",
    "services.agent.main",
}
_DYNAMIC_IMPORTS = {
    "ado2gh.api.llm.llm_model_store",
    "ado2gh.api.credentials.cloud_credentials_store",
    "ado2gh.api.connectivity_store",
    # Agent modules used by PEV coordinator and session orchestrator
    "ado2gh.agents.context_window",
    "ado2gh.agents.metrics",
    "ado2gh.agents.repo_lock_store",
    "ado2gh.agents.resource_mapping",
    "ado2gh.agents.rollback_tracker",
    # Reporting module used only by tests
    "ado2gh.reporting.boards_gaps",
}
# Modules with __main__ blocks that are run as standalone scripts.
_STANDALONE_EXECUTABLES = {
    "ado2gh.core.orchestration.worker",
    "services.agent.mcp_server",
}


def test_no_orphaned_modules():
    """Every Python module (excluding entry points and dynamic imports) must
    have at least one inbound import from within ado2gh/ or services/."""
    modules, reverse = _build_import_graph()

    orphans = []
    for mod_name in sorted(modules):
        if mod_name in _ENTRY_POINTS:
            continue
        if mod_name in _DYNAMIC_IMPORTS:
            continue
        if mod_name in _STANDALONE_EXECUTABLES:
            continue
        # Skip __init__ files — they are package markers, not importable units
        filepath = modules[mod_name]
        if filepath.endswith("__init__.py"):
            continue
        inbound = reverse.get(mod_name, set())
        if not inbound:
            orphans.append(mod_name)

    assert not orphans, f"Orphaned modules with zero inbound imports: {orphans}"
