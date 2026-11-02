"""Small Prometheus exposition layer without requiring a client library."""

from __future__ import annotations

import threading
from collections import defaultdict


class MetricsRegistry:
    """Thread-safe counters and gauges rendered in Prometheus text format."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: defaultdict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def render(self) -> str:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)

        lines: list[str] = []
        for name, value in sorted(counters.items()):
            metric_name = _normalise_name(name)
            lines.extend(
                [
                    f"# TYPE {metric_name} counter",
                    f"{metric_name} {value}",
                ]
            )
        for name, value in sorted(gauges.items()):
            metric_name = _normalise_name(name)
            lines.extend(
                [
                    f"# TYPE {metric_name} gauge",
                    f"{metric_name} {value}",
                ]
            )
        return "\n".join(lines) + ("\n" if lines else "")


def _normalise_name(name: str) -> str:
    """Keep metric names predictable and valid for Prometheus."""
    return f"k8s_remediation_{name.strip().replace('-', '_')}"


metrics = MetricsRegistry()
