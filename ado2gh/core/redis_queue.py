"""Redis-backed job queue for distributed workers."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import redis


class RedisJobQueue:
    """Push/pop job IDs via Redis list — workers claim from JobStore."""

    def __init__(self, redis_url: str | None = None, queue_name: str = "ado2gh:jobs") -> None:
        """Configure the queue without connecting to Redis.

        Args:
            redis_url: Redis connection URL. Falls back to the ``REDIS_URL``
                environment variable, then to ``redis://localhost:6379/0``.
            queue_name: Key of the Redis list holding queued job IDs.
        """
        self.queue_name = queue_name
        self._redis = None
        self._url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    @property
    def redis(self) -> "redis.Redis":
        """Return the Redis client, creating it on first access.

        Returns:
            A Redis client bound to the configured URL, decoding responses to
            strings.
        """
        if self._redis is None:
            import redis
            self._redis = redis.from_url(self._url, decode_responses=True)
        return self._redis

    def push(self, job_id: str) -> None:
        """Append a job ID to the head of the queue.

        Args:
            job_id: Identifier of the job to enqueue.
        """
        self.redis.lpush(self.queue_name, job_id)

    def pop(self, timeout: int = 5) -> Optional[str]:
        """Block until a job ID is available or the timeout elapses.

        Args:
            timeout: Seconds to wait for a job before giving up.

        Returns:
            The dequeued job ID, or None if the timeout elapsed first.
        """
        result = self.redis.brpop(self.queue_name, timeout=timeout)
        if result:
            return result[1]
        return None

    def length(self) -> int:
        """Report how many jobs are waiting to be claimed.

        Returns:
            The number of job IDs currently held in the Redis list.
        """
        return int(self.redis.llen(self.queue_name))

    def ping(self) -> bool:
        """Report whether the Redis server is reachable.

        Returns:
            True if the server answered the ping, False on any failure.
        """
        try:
            return self.redis.ping()
        except Exception:
            return False
