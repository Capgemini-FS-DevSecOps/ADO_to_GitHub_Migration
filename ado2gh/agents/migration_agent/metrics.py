"""In-process metrics counters for Prometheus endpoint (FR-102).

Simple thread-safe counters and histograms without external dependencies.
"""
from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_counters: dict[str, float] = {}
_histograms: dict[str, dict[str, float]] = {}


def increment_metric(name: str, amount: float = 1.0) -> None:
    """Increment a counter metric."""
    with _lock:
        _counters[name] = _counters.get(name, 0.0) + amount


def observe_llm_duration(seconds: float) -> None:
    """Record an LLM call duration observation."""
    with _lock:
        hist = _histograms.setdefault("llm_call_duration_seconds", {"sum": 0.0, "count": 0})
        hist["sum"] += seconds
        hist["count"] += 1


def get_metric(name: str) -> float:
    """Read a metric value by name."""
    with _lock:
        if name in _counters:
            return _counters[name]
        if name in _histograms:
            return _histograms[name].get(name.split("_", 1)[-1], 0.0)
        # Check histogram sub-keys
        for hist_name, hist in _histograms.items():
            if name == f"{hist_name}_sum":
                return hist.get("sum", 0.0)
            if name == f"{hist_name}_count":
                return hist.get("count", 0.0)
        return 0.0


def record_tool_call(tool_name: str, *, blocked: bool = False) -> None:
    """Record a tool call and optionally a guardrail block."""
    increment_metric("tool_calls_total")
    if blocked:
        increment_metric("guardrail_blocks_total")


def record_pev_cycle() -> None:
    """Record that a PEV cycle was started."""
    increment_metric("pev_cycles_total")


class LLMCallTimer:
    """Context manager for timing LLM calls."""

    def __init__(self) -> None:
        self._start = 0.0

    def __enter__(self) -> "LLMCallTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *args: Any) -> None:
        elapsed = time.monotonic() - self._start
        observe_llm_duration(elapsed)
