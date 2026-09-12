"""Regression tests for GAP-053 — the queue worker's calls into the Accelerator.

Every test patches ``worker.Accelerator`` with an ``autospec`` mock, so a call that
does not match the real signature raises here exactly as it would in the worker
container, instead of being silently absorbed by a permissive stub.

No credential value appears in this file: the payloads carry only config and
database paths, so nothing to mask reaches a message or a job record (CA-003).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from ado2gh.api.contracts import RunWaveResult
from ado2gh.models import JobRecord, JobStatus, JobTypeEnum
from ado2gh.core.orchestration import worker

CONFIG_PATH = "migration.yaml"
DB_PATH = "migration_state.db"


def _job(job_type: JobTypeEnum, **payload: Any) -> JobRecord:
    """Build a claimed job record of the given type carrying `payload`."""
    return JobRecord(
        id="job-1", job_type=job_type, status=JobStatus.RUNNING, payload=payload,
    )


@pytest.fixture
def accel_cls():
    """Patch the worker's Accelerator with a signature-checked mock class."""
    with patch.object(worker, "Accelerator", autospec=True) as cls:
        yield cls


def test_inventory_job_passes_config_path(accel_cls) -> None:
    """An inventory job supplies the config path Accelerator.inventory requires."""
    job = _job(
        JobTypeEnum.INVENTORY_PROJECT,
        config_path=CONFIG_PATH,
        db_path=DB_PATH,
        projects=["Payments"],
    )

    worker.execute_job(job)

    call = accel_cls.return_value.inventory.call_args
    assert call.args and call.args[0] == CONFIG_PATH, f"no config_path: {call}"
    assert call.kwargs["projects"] == ["Payments"]


def test_transform_pipeline_job_passes_config_path(accel_cls) -> None:
    """A transform-pipeline job scans its one project with the config path."""
    job = _job(
        JobTypeEnum.TRANSFORM_PIPELINE,
        config_path=CONFIG_PATH,
        db_path=DB_PATH,
        project="Payments",
    )

    worker.execute_job(job)

    call = accel_cls.return_value.inventory.call_args
    assert call.args and call.args[0] == CONFIG_PATH, f"no config_path: {call}"
    assert call.kwargs["projects"] == ["Payments"]


@pytest.mark.parametrize(
    "job_type", [JobTypeEnum.INVENTORY_PROJECT, JobTypeEnum.TRANSFORM_PIPELINE],
)
def test_inventory_job_without_config_path_fails_loudly(accel_cls, job_type) -> None:
    """A payload with no config path is refused instead of guessing a file."""
    job = _job(job_type, db_path=DB_PATH, projects=["Payments"], project="Payments")

    with pytest.raises(ValueError, match="config_path"):
        worker.execute_job(job)

    accel_cls.return_value.inventory.assert_not_called()


def test_accelerator_is_constructed_with_db_path_only(accel_cls) -> None:
    """The facade takes only db_path; config_path belongs to the per-job call."""
    job = _job(
        JobTypeEnum.VALIDATE_REPO, config_path=CONFIG_PATH, db_path="custom.db",
    )

    worker.execute_job(job)

    assert accel_cls.call_args.kwargs == {"db_path": "custom.db"}
    assert accel_cls.call_args.args == ()


def test_discover_job_reaches_the_accelerator(accel_cls) -> None:
    """The discover branch survives construction and forwards its request."""
    job = _job(
        JobTypeEnum.DISCOVER,
        config_path=CONFIG_PATH,
        db_path=DB_PATH,
        output_dir="output/discovery",
    )

    worker.execute_job(job)

    request = accel_cls.return_value.discover.call_args.args[0]
    assert request.config_path == CONFIG_PATH
    assert request.output_dir == "output/discovery"


def test_migrate_repo_job_serialises_the_single_wave_result(accel_cls) -> None:
    """run_wave returns one RunWaveResult, not a list, and it is dumped as one."""
    accel_cls.return_value.run_wave.return_value = RunWaveResult(
        wave_id=2, status="completed", completed=3, failed=0, total=3, dry_run=True,
    )
    job = _job(
        JobTypeEnum.MIGRATE_REPO,
        config_path=CONFIG_PATH,
        db_path=DB_PATH,
        wave_id=2,
        dry_run=True,
    )

    result = worker.execute_job(job)

    assert result == {
        "waves": [
            {
                "wave_id": 2,
                "status": "completed",
                "completed": 3,
                "failed": 0,
                "total": 3,
                "dry_run": True,
            },
        ],
    }


def test_unknown_job_type_still_raises(accel_cls) -> None:
    """The unsupported-type guard is unchanged by the signature fixes."""
    job = _job(JobTypeEnum.PUSH_WORKFLOWS, config_path=CONFIG_PATH, db_path=DB_PATH)

    with pytest.raises(ValueError, match="Unsupported job type"):
        worker.execute_job(job)
