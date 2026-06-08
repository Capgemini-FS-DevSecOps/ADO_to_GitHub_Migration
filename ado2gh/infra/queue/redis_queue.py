"""Redis-backed job queue for distributed workers."""
from __future__ import annotations

import json
import os
from typing import Optional


class RedisJobQueue:
    """Push/pop job IDs via Redis list — workers claim from JobStore."""

    def __init__(self, redis_url: str | None = None, queue_name: str = "ado2gh:jobs"):
        self.queue_name = queue_name
        self._redis = None
        self._url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    @property
    def redis(self):
        if self._redis is None:
            import redis
            self._redis = redis.from_url(self._url, decode_responses=True)
        return self._redis

    def push(self, job_id: str) -> None:
        self.redis.lpush(self.queue_name, job_id)

    def pop(self, timeout: int = 5) -> Optional[str]:
        result = self.redis.brpop(self.queue_name, timeout=timeout)
        if result:
            return result[1]
        return None

    def length(self) -> int:
        return int(self.redis.llen(self.queue_name))

    def ping(self) -> bool:
        try:
            return self.redis.ping()
        except Exception:
            return False
