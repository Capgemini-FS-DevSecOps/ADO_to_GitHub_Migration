"""Lightweight mode inline job completion."""
import os

from ado2gh.api.contracts import JobTypeEnum
from ado2gh.infra.state.job_store import JobStoreFactory


def test_lightweight_inline_job_complete(tmp_path):
    db_path = tmp_path / "jobs.db"
    os.environ["ADO2GH_STORAGE_BACKEND"] = "sqlite"
    os.environ["ADO2GH_SQLITE_PATH"] = str(db_path)
    store = JobStoreFactory.from_env()
    job = store.enqueue(JobTypeEnum.MIGRATE_REPO, {"dry_run": True})
    store.complete(job.id, {"inline": True, "dry_run": True})
    done = store.get(job.id)
    assert done is not None
    assert done.status.value == "completed"
