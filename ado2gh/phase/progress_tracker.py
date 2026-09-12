"""Sliding-window velocity + ETA tracker."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from ado2gh.state.base import StateDBBase


class ProgressTracker:
    """Track completed repos over a five-minute window to report velocity and ETA."""

    def __init__(self, total_repos: int, total_pipelines: int) -> None:
        """Set the totals the percentage and remaining figures are measured against.

        Args:
            total_repos: Number of repos expected in this run.
            total_pipelines: Number of pipelines expected in this run.
        """
        self.total_repos = total_repos
        self.total_pipelines = total_pipelines
        self._events: list[tuple[float, str]] = []
        self._start = time.time()

    def record_repo(self) -> None:
        """Note that one repo finished now."""
        self._events.append((time.time(), "repo"))

    def snapshot(self, db: StateDBBase) -> dict:
        """Compute the current progress figures from the window and the state DB.

        Args:
            db: State store whose migration status counts give the done/failed totals.

        Returns:
            Counts (``total_repos``, ``done_repos``, ``failed_repos``,
            ``remaining_repos``), ``pct_complete``, ``repo_velocity`` and
            ``pipe_velocity`` in items per minute, ``elapsed_min``, and the ETA
            as ``eta_str`` (empty when velocity is too low) and ``eta_min``.
        """
        now = time.time()
        since = now - 300
        repo_vel = sum(1 for t, k in self._events if t >= since and k == "repo") / 5.0
        pipe_vel = sum(1 for t, k in self._events if t >= since and k == "pipeline") / 5.0
        counts = db.migration_status_counts()
        done_r = counts.get("completed", 0)
        fail_r = counts.get("failed", 0)
        remaining = max(0, self.total_repos - done_r - fail_r)
        eta_min = (remaining / repo_vel) if repo_vel > 0.01 else None
        eta_str = ((datetime.now() + timedelta(minutes=eta_min)).strftime("%Y-%m-%d %H:%M")
                   if eta_min else "")
        return {
            "total_repos": self.total_repos, "done_repos": done_r,
            "failed_repos": fail_r, "remaining_repos": remaining,
            "pct_complete": round(done_r / self.total_repos * 100, 1) if self.total_repos else 0,
            "repo_velocity": round(repo_vel, 2), "pipe_velocity": round(pipe_vel, 2),
            "elapsed_min": round((now - self._start) / 60, 1),
            "eta_str": eta_str, "eta_min": round(eta_min, 0) if eta_min else None,
        }
