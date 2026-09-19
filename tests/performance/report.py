"""Machine- and human-readable M6.4 performance reports."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .harness import PerformanceMeasurement


def build_machine_report(
    measurements: Mapping[str, PerformanceMeasurement],
    comparisons: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "report": "m64-performance",
        "fixtures": {
            fixture_id: {
                "measurement": measurement.as_dict(),
                **({"comparison": comparisons[fixture_id]} if comparisons else {}),
            }
            for fixture_id, measurement in measurements.items()
        },
    }


def format_human_report(
    measurements: Mapping[str, PerformanceMeasurement],
    comparisons: Mapping[str, dict[str, Any]] | None = None,
) -> str:
    lines = [
        "M6.4 Performance Report",
        "=======================",
        f"Fixtures: {len(measurements)}",
        "Timing: perf_counter; statistics: median (min/max retained)",
        "Memory: peak Python allocations from tracemalloc (not RSS)",
        "",
    ]
    for fixture_id, measurement in measurements.items():
        lines.append(
            f"{fixture_id} [{measurement.classification}, "
            f"{measurement.page_count} pages] "
            f"{measurement.elapsed.median:.4f}s "
            f"(peak {measurement.peak_python_allocated_bytes} bytes)"
        )
        lines.append(
            "  phases: "
            + ", ".join(
                f"{name}={timing.median:.4f}s"
                for name, timing in measurement.phases.items()
            )
        )
        if comparisons:
            lines.append(f"  baseline: {comparisons[fixture_id]['status']}")
    return "\n".join(lines)


def write_json_report(path, report: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
