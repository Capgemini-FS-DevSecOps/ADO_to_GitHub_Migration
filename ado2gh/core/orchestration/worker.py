"""Distributed job worker — pulls from Redis queue and executes via Accelerator."""
# ruff: noqa: E402  -- imports below intentionally follow ensure_gei_dotnet_env()
from __future__ import annotations

import logging
import time

from ado2gh.core.gei_runtime import ensure_gei_dotnet_env

ensure_gei_dotnet_env()

from ado2gh.api.accelerator import Accelerator
from ado2gh.api.contracts import RunWaveRequest
from ado2gh.core.redis_queue import RedisJobQueue
from ado2gh.models import JobRecord
from ado2gh.models import JobTypeEnum as JobType
from ado2gh.state.job_store import JobStoreFactory

log = logging.getLogger("ado2gh.worker")


def _require_config_path(payload: dict) -> str:
    """Read the migration configuration path a job needs, or refuse the job.

    Args:
        payload: The job payload exactly as it was enqueued through
            ``POST /v1/jobs``.

    Returns:
        The ``config_path`` the Accelerator resolves its Azure DevOps settings
        from.

    Raises:
        ValueError: The payload carries no ``config_path``. No config file is
            guessed on the operator's behalf, because the wrong one would scan
            the wrong organisation.
    """
    config_path = payload.get("config_path", "")
    if not config_path:
        raise ValueError("Job payload is missing 'config_path'")
    return config_path


def execute_job(job: JobRecord) -> dict:
    """Run a single job based on its type.

    Args:
        job: The claimed job. Its payload supplies the state database path and
            the request fields for the job type, including the ``config_path``
            that the inventory job types read directly.

    Returns:
        The accelerator response for that job type, as a plain dictionary
        ready to be stored as the job result.

    Raises:
        ValueError: The job type has no handler, or an inventory job's payload
            omits ``config_path``.
    """
    accel = Accelerator(db_path=job.payload.get("db_path", "migration_state.db"))
    jt = job.job_type
    payload = job.payload

    if jt == JobType.DISCOVER:
        from ado2gh.api.contracts import DiscoverRequest
        return accel.discover(DiscoverRequest(**payload)).model_dump()

    if jt == JobType.INVENTORY_PROJECT:
        projects = payload.get("projects", [])
        return accel.inventory(_require_config_path(payload), projects=projects)

    if jt == JobType.MIGRATE_REPO:
        result = accel.run_wave(RunWaveRequest(**payload))
        return {"waves": [result.model_dump()]}

    if jt == JobType.VALIDATE_REPO:
        from ado2gh.api.contracts import ValidateRequest
        return accel.validate(ValidateRequest(**payload)).model_dump()

    if jt == JobType.TRANSFORM_PIPELINE:
        return accel.inventory(
            _require_config_path(payload), projects=[payload.get("project", "")],
        )

    raise ValueError(f"Unsupported job type: {jt}")


def run_worker(poll_interval: float = 1.0) -> None:
    """Consume jobs from the queue forever, executing each one.

    Args:
        poll_interval: Seconds to wait for a queued job before falling back
            to claiming directly from the job store.
    """
    store = JobStoreFactory.from_env()
    queue = RedisJobQueue()
    log.info("Worker started — queue=%s", queue.queue_name)

    while True:
        job_id = queue.pop(timeout=int(poll_interval))
        if not job_id:
            job = store.claim_next()
            if job:
                job_id = job.id
            else:
                time.sleep(poll_interval)
                continue

        job = store.get(job_id)
        if not job:
            continue

        if job.status.value == "pending":
            job = store.claim_next() or job

        try:
            result = execute_job(job)
            store.complete(job.id, result)
            log.info("Job %s completed", job.id)
        except Exception as exc:
            store.fail(job.id, str(exc))
            log.error("Job %s failed: %s", job.id, exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_worker()
