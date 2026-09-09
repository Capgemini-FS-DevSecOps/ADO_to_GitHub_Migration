"""Global concurrency caps for git, repo, pipeline, and API rate limits."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass
class ConcurrencyConfig:
    """Worker-pool sizes and API rate ceilings for a migration run."""

    max_git_workers: int = 4
    max_repo_workers: int = 8
    max_pipeline_workers: int = 16
    max_ado_rps: float = 10.0
    max_gh_rps: float = 20.0


class ConcurrencyManager:
    """Thread-safe semaphores for nested worker pools."""

    _instance: ConcurrencyManager | None = None
    _lock = threading.Lock()

    def __init__(self, config: ConcurrencyConfig | None = None) -> None:
        """Build the semaphores sized by the given configuration.

        Args:
            config: Limits to apply. Defaults to ``ConcurrencyConfig()``.
        """
        cfg = config or ConcurrencyConfig()
        self.config = cfg
        self._git = threading.Semaphore(cfg.max_git_workers)
        self._repo = threading.Semaphore(cfg.max_repo_workers)
        self._pipeline = threading.Semaphore(cfg.max_pipeline_workers)

    @classmethod
    def get(cls, config: ConcurrencyConfig | None = None) -> ConcurrencyManager:
        """Return the process-wide manager, creating it on first call.

        Args:
            config: Limits used only when the singleton does not exist yet;
                ignored once a manager has been created.

        Returns:
            The shared manager instance.
        """
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(config)
            return cls._instance

    @classmethod
    def from_dict(cls, cfg: dict) -> ConcurrencyManager:
        """Create a manager from a raw configuration mapping.

        Args:
            cfg: Mapping of limit names to values. ``repo_parallel`` and
                ``pipeline_parallel`` are accepted as legacy aliases for
                ``max_repo_workers`` and ``max_pipeline_workers``.

        Returns:
            A new manager, not the shared singleton.
        """
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
        """Drop the shared manager so the next ``get`` rebuilds it."""
        with cls._lock:
            cls._instance = None

    @contextmanager
    def git_slot(self) -> Iterator[None]:
        """Hold one git worker slot for the duration of the block.

        Yields:
            None, once a git slot has been acquired.
        """
        self._git.acquire()
        try:
            yield
        finally:
            self._git.release()

    @contextmanager
    def pipeline_slot(self) -> Iterator[None]:
        """Hold one pipeline worker slot for the duration of the block.

        Yields:
            None, once a pipeline slot has been acquired.
        """
        self._pipeline.acquire()
        try:
            yield
        finally:
            self._pipeline.release()
