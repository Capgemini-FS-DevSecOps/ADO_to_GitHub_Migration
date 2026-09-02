"""Prometheus-compatible metrics collector.

In-memory counters and histograms for agent sessions, PEV cycles,
LLM calls, guardrails, and tool calls.
"""
from __future__ import annotations

import threading


class MetricsCollector:
    """Thread-safe in-memory metrics collector with Prometheus text export."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._gauges: dict[str, float] = {}
        self._session_status_counts: dict[str, int] = {}

    def inc_counter(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + value

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def observe_histogram(self, name: str, value: float) -> None:
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = []
            self._histograms[name].append(value)

    def set_session_status(self, status: str, count: int) -> None:
        with self._lock:
            self._session_status_counts[status] = count

    def record_llm_call(self, duration_seconds: float) -> None:
        self.inc_counter("llm_calls_total")
        self.observe_histogram("llm_call_duration_seconds", duration_seconds)

    def record_pev_cycle(self) -> None:
        self.inc_counter("pev_cycles_total")

    def record_guardrail_block(self) -> None:
        self.inc_counter("guardrail_blocks_total")

    def record_tool_call(self) -> None:
        self.inc_counter("tool_calls_total")


    def to_prometheus_text(self) -> str:
        """Export metrics in Prometheus text exposition format."""
        lines: list[str] = []

        with self._lock:
            for name, value in sorted(self._counters.items()):
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name} {value}")

            for name, value in sorted(self._gauges.items()):
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name} {value}")

            for name, values in sorted(self._histograms.items()):
                lines.append(f"# TYPE {name} histogram")
                count = len(values)
                total = sum(values)
                lines.append(f"{name}_count {count}")
                lines.append(f"{name}_sum {total}")
                if values:
                    sorted_vals = sorted(values)
                    for p in [0.5, 0.9, 0.99]:
                        idx = int(len(sorted_vals) * p)
                        idx = min(idx, len(sorted_vals) - 1)
                        lines.append(f'{name}{{quantile="{p}"}} {sorted_vals[idx]}')

            if self._session_status_counts:
                lines.append("# TYPE session_status_counts gauge")
                for status, count in sorted(self._session_status_counts.items()):
                    lines.append(f'session_status_counts{{status="{status}"}} {count}')

        return "\n".join(lines) + "\n"


_metrics: MetricsCollector | None = None


def get_metrics_collector() -> MetricsCollector:
    """Get the singleton metrics collector instance."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics
