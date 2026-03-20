"""Sliding-window velocity + ETA tracker."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from ado2gh.state.db import StateDB


class ProgressTracker:
    def __init__(self, total_repos: int, total_pipelines: int):
        self.total_repos = total_repos
        self.total_pipelines = total_pipelines
        self._events: list = []
        self._start = time.time()

    def record_repo(self):
        self._events.append((time.time(), "repo"))

    def record_pipeline(self):
        self._events.append((time.time(), "pipeline"))

    def snapshot(self, db: StateDB) -> dict:
        now = time.time()
        since = now - 300
        repo_vel = sum(1 for t, k in self._events if t >= since and k == "repo") / 5.0
        pipe_vel = sum(1 for t, k in self._events if t >= since and k == "pipeline") / 5.0
        all_mig = db.get_all_migrations()
        done_r = len({r["ado_repo"] for r in all_mig if r["status"] == "completed"})
        fail_r = len({r["ado_repo"] for r in all_mig if r["status"] == "failed"})
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
