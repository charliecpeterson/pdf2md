"""Sequential stage timing and work counts for one conversion run.

The pipeline records named checkpoints because its stages do not overlap. The final
report is JSON-safe and stored with provenance rather than sent to external telemetry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
import time
from typing import Any


def _rss_bytes(value: int, platform: str) -> int:
    return value if platform == "darwin" else value * 1024


def process_peak_memory() -> dict[str, Any]:
    try:
        import resource
    except ImportError:
        return {"available": False, "reason": "resource module unavailable"}
    try:
        own = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        child = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    except OSError as exc:
        return {"available": False, "reason": str(exc)}
    return {
        "available": True,
        "scope": "process-lifetime high-water marks",
        "main_process_peak_rss_bytes": _rss_bytes(own, sys.platform),
        "largest_terminated_child_peak_rss_bytes": _rss_bytes(child, sys.platform),
    }


@dataclass
class RunMetrics:
    clock: Callable[[], float] = field(default=time.perf_counter, repr=False)
    memory_reader: Callable[[], dict[str, Any]] = field(
        default=process_peak_memory, repr=False
    )
    _checkpoint: float = field(init=False)
    _stages: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._checkpoint = self.clock()

    def finish(self, name: str, **counts: Any) -> None:
        if name in self._stages:
            raise ValueError(f"stage already recorded: {name}")
        now = self.clock()
        stage: dict[str, Any] = {"duration_s": round(now - self._checkpoint, 3)}
        if counts:
            stage["counts"] = counts
        self._stages[name] = stage
        self._checkpoint = now

    def report(self) -> dict[str, Any]:
        stages = dict(self._stages)
        return {
            "duration_s": round(sum(item["duration_s"] for item in stages.values()), 3),
            "stages": stages,
            "memory": self.memory_reader(),
        }


def load_run_metrics(version_dir: Path) -> dict[str, Any]:
    provenance = Path(version_dir) / "provenance.json"
    document = json.loads(provenance.read_text())
    metrics = (document.get("provenance") or {}).get("run_metrics")
    if not metrics or not metrics.get("stages"):
        raise ValueError(f"run metrics not found in {provenance}")
    return metrics


def failed_optional_calls(metrics: dict[str, Any] | None) -> int:
    """Model calls that failed after retries and left optional evidence incomplete."""
    if not metrics:
        return 0
    return sum(
        int(stage.get("counts", {}).get("vision_failures", 0))
        for stage in metrics.get("stages", {}).values()
    )


def _change(before: float, after: float) -> dict[str, float | None]:
    delta = round(after - before, 3)
    percent = round(delta / before * 100, 1) if before else None
    return {
        "before_s": before,
        "after_s": after,
        "delta_s": delta,
        "change_percent": percent,
    }


def _memory_change(before: int, after: int) -> dict[str, float | int | None]:
    delta = after - before
    percent = round(delta / before * 100, 1) if before else None
    return {
        "before_bytes": before,
        "after_bytes": after,
        "delta_bytes": delta,
        "change_percent": percent,
    }


def compare_run_metrics(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_stages = before["stages"]
    after_stages = after["stages"]
    names = list(dict.fromkeys([*before_stages, *after_stages]))
    before_memory = before.get("memory", {})
    after_memory = after.get("memory", {})
    memory = None
    if before_memory.get("available") and after_memory.get("available"):
        memory = {
            name: _memory_change(int(before_memory[name]), int(after_memory[name]))
            for name in (
                "main_process_peak_rss_bytes",
                "largest_terminated_child_peak_rss_bytes",
            )
        }
    return {
        "duration": _change(float(before["duration_s"]), float(after["duration_s"])),
        "memory": memory,
        "stages": [
            {
                "stage": name,
                **_change(
                    float(before_stages.get(name, {}).get("duration_s", 0)),
                    float(after_stages.get(name, {}).get("duration_s", 0)),
                ),
                "before_counts": before_stages.get(name, {}).get("counts", {}),
                "after_counts": after_stages.get(name, {}).get("counts", {}),
            }
            for name in names
        ],
    }
