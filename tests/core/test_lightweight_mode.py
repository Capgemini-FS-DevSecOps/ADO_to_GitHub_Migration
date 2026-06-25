"""Lightweight mode inline job completion."""

from ado2gh.api.contracts import JobTypeEnum
from ado2gh.state.job_store import JobStoreFactory


def test_lightweight_inline_job_complete(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    store = JobStoreFactory.from_env()
    job = store.enqueue(JobTypeEnum.MIGRATE_REPO, {"dry_run": True})
    store.complete(job.id, {"inline": True, "dry_run": True})
    done = store.get(job.id)
    assert done is not None
    assert done.status.value == "completed"
