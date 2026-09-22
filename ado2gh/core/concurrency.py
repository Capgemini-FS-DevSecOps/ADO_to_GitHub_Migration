"""Global concurrency caps for git, repository, pipeline, and API rate limits."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


def _defaults() -> "ConcurrencySettings":  # noqa: F821 - forward ref, imported lazily below
    """Return the one source of these numbers, imported lazily.

    ``ado2gh.api`` imports ``ado2gh.core.migration_engine``, which imports
    this module, so importing ``ConcurrencySettings`` at module load time
    would be a circular import. Deferring the import to first use — after
    both modules have finished loading — avoids that without duplicating
    the defaults here.
    """
    from ado2gh.api.settings_models import ConcurrencySettings

    return ConcurrencySettings()


@dataclass
class ConcurrencyConfig:
    """Worker-pool sizes and API rate ceilings for a migration run."""

    max_git_workers: int = field(default_factory=lambda: _defaults().max_git_workers)
    max_repo_workers: int = field(default_factory=lambda: _defaults().max_repo_workers)
    max_pipeline_workers: int = field(default_factory=lambda: _defaults().max_pipeline_workers)
    max_ado_rps: float = field(default_factory=lambda: _defaults().max_ado_rps)
    max_gh_rps: float = field(default_factory=lambda: _defaults().max_gh_rps)


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
        defaults = _defaults()
        return cls(ConcurrencyConfig(
            max_git_workers=int(cfg.get("max_git_workers", defaults.max_git_workers)),
            max_repo_workers=int(
                cfg.get("max_repo_workers", cfg.get("repo_parallel", defaults.max_repo_workers))
            ),
            max_pipeline_workers=int(
                cfg.get("max_pipeline_workers", cfg.get("pipeline_parallel", defaults.max_pipeline_workers))
            ),
            max_ado_rps=float(cfg.get("max_ado_rps", defaults.max_ado_rps)),
            max_gh_rps=float(cfg.get("max_gh_rps", defaults.max_gh_rps)),
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
