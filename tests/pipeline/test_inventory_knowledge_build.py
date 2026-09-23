"""What the pipeline inventory does about the knowledge base when it finishes.

A preview writes no inventory rows, so it must record no facts about them
either. A real scan records them. And because the rows are the scan's whole
product, a knowledge base that fails to build may never cost the caller the
scan: the failure comes back on the scan's own result in plain words.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from ado2gh.knowledge.models import KnowledgeScan
from ado2gh.models import ExecutionMode
from ado2gh.pipelines.inventory import (
    KNOWLEDGE_SUMMARY_KEY,
    PipelineInventoryBuilder,
    summarize_project_inventory,
)

FAKE_PROFILE = SimpleNamespace(id="profile-fake-0001", ado_org_url="https://dev.azure.com/fake-org/")
FAKE_SCAN_ID = "scan-fake-0001"
FAKE_COVERAGE = {"rows_read": 3, "nodes_recorded": 4, "edges_recorded": 5}


@pytest.fixture
def builds(monkeypatch) -> list[tuple[str, str]]:
    """Record every knowledge build the inventory asks for, and answer with a fake scan."""
    calls: list[tuple[str, str]] = []

    def _fake_build(store: Any, db: Any, *, profile_id: str, source_scope: str) -> KnowledgeScan:
        calls.append((profile_id, source_scope))
        return KnowledgeScan(
            scan_id=FAKE_SCAN_ID,
            profile_id=profile_id,
            source_scope=source_scope,
            extractor_version="fake-extractor-1",
            started_at="2026-09-23T00:00:00+00:00",
            completed_at="2026-09-23T00:00:01+00:00",
            status="completed",
            coverage=dict(FAKE_COVERAGE),
        )

    monkeypatch.setattr("ado2gh.knowledge.builder.build_knowledge_base", _fake_build)
    monkeypatch.setattr(
        "ado2gh.api.settings_store.SettingsStore",
        lambda *_a, **_k: SimpleNamespace(get_active_profile=lambda: FAKE_PROFILE),
    )
    return calls


def _scan(mode: ExecutionMode) -> dict[str, dict]:
    """Run one inventory scan over an Azure DevOps double that holds no pipelines."""
    ado = MagicMock()
    ado.org_url = "https://dev.azure.com/fake-org"
    return PipelineInventoryBuilder(
        ado, MagicMock(), parallel=1, mode=mode,
    ).build_for_projects(["FakeProject"])


def test_a_preview_run_records_no_knowledge(builds):
    """A preview changes nothing, here as everywhere else on this platform."""
    summary = _scan(ExecutionMode.DRY_RUN)

    assert builds == []
    assert KNOWLEDGE_SUMMARY_KEY not in summary


def test_a_real_run_records_the_knowledge_for_the_active_profile(builds):
    """The rows just written are read back for the profile the scan ran under."""
    summary = _scan(ExecutionMode.LIVE)

    assert builds == [(FAKE_PROFILE.id, "https://dev.azure.com/fake-org")]
    recorded = summary[KNOWLEDGE_SUMMARY_KEY]
    assert recorded["status"] == "completed"
    assert recorded["scan_id"] == FAKE_SCAN_ID
    assert recorded["coverage"] == FAKE_COVERAGE


def test_a_failed_knowledge_build_never_costs_the_caller_the_scan(monkeypatch, builds):
    """The scan still answers with its counts, and says in plain words what went wrong."""
    def _explode(*_a: Any, **_k: Any) -> KnowledgeScan:
        raise RuntimeError("the state store would not open")

    monkeypatch.setattr("ado2gh.knowledge.builder.build_knowledge_base", _explode)

    summary = _scan(ExecutionMode.LIVE)

    assert summary["FakeProject"]["total"] == 0
    recorded = summary[KNOWLEDGE_SUMMARY_KEY]
    assert recorded["status"] == "failed"
    assert "the state store would not open" in recorded["detail"]


def test_the_knowledge_entry_is_not_counted_as_a_project(builds):
    """The organisation totals count projects, and the knowledge entry is not one."""
    totals = summarize_project_inventory(_scan(ExecutionMode.LIVE))

    assert totals["projects"] == 1
