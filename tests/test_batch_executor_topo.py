"""Batch executor topological ordering."""
from ado2gh.models import RepoConfig, WaveConfig
from ado2gh.phase.batch_executor import BatchExecutor
from ado2gh.phase.progress_tracker import ProgressTracker
from ado2gh.state.db import StateDB


def test_plan_repo_order(tmp_path):
    db = StateDB(str(tmp_path / "topo.db"))
    db.upsert_dependency_edge("p1", "Payments/api", "Platform/lib")
    keys = ["Payments/api", "Platform/lib"]
    ordered = BatchExecutor.plan_repo_order(db, "p1", keys)
    assert ordered.index("Platform/lib") < ordered.index("Payments/api")


def test_sort_repos_topo(tmp_path):
    db = StateDB(str(tmp_path / "topo2.db"))
    db.upsert_dependency_edge("p1", "B/r2", "A/r1")
    engine = object()
    ex = BatchExecutor(engine, db, ProgressTracker(1, 1))
    repos = [
        RepoConfig(ado_project="B", ado_repo="r2", gh_org="o", gh_repo="r2"),
        RepoConfig(ado_project="A", ado_repo="r1", gh_org="o", gh_repo="r1"),
    ]
    sorted_repos = ex._sort_repos_topo(repos, "p1")
    assert sorted_repos[0].ado_repo == "r1"
