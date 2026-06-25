"""Extended StateDB agentic table coverage."""
from ado2gh.assignments.audit import AuditWriter
from ado2gh.state.db import StateDB


def test_audit_and_dependency_edges(tmp_path):
    db = StateDB(str(tmp_path / "dbag.db"))
    writer = AuditWriter(db)
    eid = writer.write("test_event", "p1", actor="op", payload={"token": "ghp_secret123"})
    assert eid.startswith("aud_")
    events = db.list_audit_events(profile_id="p1", limit=10)
    assert len(events) == 1
    db.upsert_dependency_edge("p1", "B/r2", "A/r1")
    edges = db.get_dependency_edges("p1")
    assert len(edges) == 1
    db.upsert_remediation_loop("s1", "P/r1", 0, 3, "active")
    loop = db.get_remediation_loop("s1", "P/r1")
    assert loop["max_retries"] == 3


def test_repo_in_progress(tmp_path):
    db = StateDB(str(tmp_path / "prog.db"))
    from ado2gh.models import MigrationStatus, RepoConfig
    repo = RepoConfig(ado_project="P", ado_repo="r1", gh_org="o", gh_repo="r1")
    db.upsert_migration(1, repo, "repo", MigrationStatus.IN_PROGRESS)
    assert db.has_repo_in_progress("P", "r1")
    assert not db.has_repo_in_progress("P", "other")
