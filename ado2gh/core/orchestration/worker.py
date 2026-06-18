"""Distributed job worker — pulls from Redis queue and executes via Accelerator."""
from __future__ import annotations

import logging
import time

from ado2gh.core.gei_runtime import ensure_gei_dotnet_env

ensure_gei_dotnet_env()

from ado2gh.api.accelerator import Accelerator
from ado2gh.api.contracts import JobTypeEnum as JobType, RunWaveRequest
from ado2gh.infra.queue.redis_queue import RedisJobQueue
from ado2gh.infra.state.job_store import JobStoreFactory

log = logging.getLogger("ado2gh.worker")


def execute_job(job_store, job) -> dict:
    """Run a single job based on its type."""
    accel = Accelerator(
        config_path=job.payload.get("config_path", ""),
        db_path=job.payload.get("db_path", "migration_state.db"),
    )
    jt = job.job_type
    payload = job.payload

    if jt == JobType.DISCOVER:
        from ado2gh.api.contracts import DiscoverRequest
        return accel.discover(DiscoverRequest(**payload)).model_dump()

    if jt == JobType.INVENTORY_PROJECT:
        projects = payload.get("projects", [])
        return accel.inventory(projects=projects)

    if jt == JobType.MIGRATE_REPO:
        results = accel.run_wave(RunWaveRequest(**payload))
        return {"waves": [r.model_dump() for r in results]}

    if jt == JobType.VALIDATE_REPO:
        from ado2gh.api.contracts import ValidateRequest
        return accel.validate(ValidateRequest(**payload)).model_dump()

    if jt == JobType.TRANSFORM_PIPELINE:
        return accel.inventory(projects=[payload.get("project", "")])

    raise ValueError(f"Unsupported job type: {jt}")


def run_worker(poll_interval: float = 1.0) -> None:
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
            result = execute_job(store, job)
            store.complete(job.id, result)
            log.info("Job %s completed", job.id)
        except Exception as exc:
            store.fail(job.id, str(exc))
            log.error("Job %s failed: %s", job.id, exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_worker()
