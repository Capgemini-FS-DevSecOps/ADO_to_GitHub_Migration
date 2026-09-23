"""A queued migrate-repo job whose payload omits `dry_run` now previews.

`ado2gh/core/orchestration/worker.py` builds `RunWaveRequest(**payload)` straight
from the job's stored payload dictionary; nothing in the worker states `dry_run`
on the caller's behalf. That payload comes from `POST /v1/jobs`
(`services/accelerator_api/main.py`), a route any caller can send a body to, so a
submitted job that leaves the field out is exactly the kind of omission the
default-to-preview change protects (register cross-reference: CA-001). This
mirrors the queue-worker case named in `tests/unit/test_request_dry_run_default.py`
and the payload-construction pattern already used in
`tests/unit/test_gap_053_worker_inventory_job.py`.
"""
from __future__ import annotations

from unittest.mock import patch

from ado2gh.api.contracts import RunWaveResult
from ado2gh.core.orchestration import worker
from ado2gh.models import JobRecord, JobStatus, JobTypeEnum

CONFIG_PATH = "migration.yaml"


def test_a_migrate_repo_job_without_dry_run_previews() -> None:
    """No `dry_run` key in the stored payload reaches `RunWaveRequest` as a preview."""
    job = JobRecord(
        id="job-1",
        job_type=JobTypeEnum.MIGRATE_REPO,
        status=JobStatus.RUNNING,
        payload={"config_path": CONFIG_PATH, "wave_id": 1},
    )
    with patch.object(worker, "Accelerator", autospec=True) as accel_cls:
        accel_cls.return_value.run_wave.return_value = RunWaveResult(
            wave_id=1, status="completed", completed=0, failed=0, total=0, dry_run=True,
        )

        worker.execute_job(job)

    request = accel_cls.return_value.run_wave.call_args.args[0]
    assert request.dry_run is True
