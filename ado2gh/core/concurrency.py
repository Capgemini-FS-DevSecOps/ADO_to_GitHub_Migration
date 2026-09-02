"""Global concurrency caps for git, repo, pipeline, and API rate limits."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass
class ConcurrencyConfig:
    max_git_workers: int = 4
    max_repo_workers: int = 8
    max_pipeline_workers: int = 16
    max_ado_rps: float = 10.0
    max_gh_rps: float = 20.0


class ConcurrencyManager:
    """Thread-safe semaphores for nested worker pools."""

    _instance: ConcurrencyManager | None = None
    _lock = threading.Lock()

    def __init__(self, config: ConcurrencyConfig | None = None):
        cfg = config or ConcurrencyConfig()
        self.config = cfg
        self._git = threading.Semaphore(cfg.max_git_workers)
        self._repo = threading.Semaphore(cfg.max_repo_workers)
        self._pipeline = threading.Semaphore(cfg.max_pipeline_workers)

    @classmethod
    def get(cls, config: ConcurrencyConfig | None = None) -> ConcurrencyManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(config)
            return cls._instance

    @classmethod
    def from_dict(cls, cfg: dict) -> ConcurrencyManager:
        return cls(ConcurrencyConfig(
            max_git_workers=int(cfg.get("max_git_workers", 4)),
            max_repo_workers=int(cfg.get("max_repo_workers", cfg.get("repo_parallel", 8))),
            max_pipeline_workers=int(
                cfg.get("max_pipeline_workers", cfg.get("pipeline_parallel", 16))
            ),
            max_ado_rps=float(cfg.get("max_ado_rps", 10.0)),
            max_gh_rps=float(cfg.get("max_gh_rps", 20.0)),
        ))

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    @contextmanager
    def git_slot(self) -> Iterator[None]:
        self._git.acquire()
        try:
            yield
        finally:
            self._git.release()

    @contextmanager
    def pipeline_slot(self) -> Iterator[None]:
        self._pipeline.acquire()
        try:
            yield
        finally:
            self._pipeline.release()

