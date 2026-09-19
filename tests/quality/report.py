"""Deterministic quality reports for the M6.3 measurement framework.

Both report formats are pure functions of the evaluation results: no
timestamps, no absolute paths, no randomness. The machine-readable report is a
JSON-serializable dict with stable ordering (corpus order, canonical dimension
order); the human-readable report is generated from the same data.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .evaluation import FixtureQualityResult
from .expectations import DIMENSIONS

_REPORT_SCHEMA_VERSION = 1


def build_machine_report(
    results: Mapping[str, FixtureQualityResult],
) -> dict[str, Any]:
    """A JSON-serializable quality report over a whole corpus evaluation.

    Every metric keeps its expected value, observed value, status, and detail
    so individual measurements are never hidden behind an aggregate number.
    """
    fixtures: dict[str, Any] = {}
    for fixture in sorted(results):
        result = results[fixture]
        dimensions: dict[str, Any] = {}
        for dimension in DIMENSIONS:
            entries = result.results_by_dimension().get(dimension)
            if not entries:
                continue
            status = "PASS" if all(entry.passed for entry in entries) else "FAIL"
            dimensions[dimension] = {
                "status": status,
                "metrics": [entry.as_dict() for entry in entries],
            }
        fixtures[fixture] = {
            "status": "PASS" if result.passed else "FAIL",
            "dimensions": dimensions,
        }

    statuses = [result.passed for result in results.values()]
    return {
        "schema_version": _REPORT_SCHEMA_VERSION,
        "report": "m63-quality",
        "corpus_size": len(results),
        "fixtures_passed": sum(statuses),
        "fixtures_failed": len(statuses) - sum(statuses),
        "fixtures": fixtures,
    }


def format_human_report(
    results: Mapping[str, FixtureQualityResult],
    review_notes: Mapping[str, list[str]] | None = None,
) -> str:
    """A human-focused, deterministic summary + per-fixture breakdown."""
    review_notes = review_notes or {}
    ordered = [results[fixture] for fixture in sorted(results)]

    lines: list[str] = []
    lines.append("M6.3 Conversion Quality Report")
    lines.append("-------------------------------")
    lines.append(f"Corpus documents: {len(ordered)}")
    passed = sum(1 for r in ordered if r.passed)
    lines.append(f"Fixture status: {passed}/{len(ordered)} PASS")
    lines.append("")

    lines.append("Per-dimension status")
    for dimension in DIMENSIONS:
        dim_results = [
            entry
            for result in ordered
            for entry in result.results_by_dimension().get(dimension, ())
        ]
        if not dim_results:
            continue
        dim_pass = sum(1 for entry in dim_results if entry.passed)
        lines.append(
            f"  {dimension:<14} {dim_pass}/{len(dim_results)} PASS"
        )
    lines.append("")

    lines.append("Per-fixture results")
    lines.append("-------------------")
    for result in ordered:
        lines.append("")
        lines.append(f"{result.fixture}: {'PASS' if result.passed else 'FAIL'}")
        for dimension, entries in result.results_by_dimension().items():
            lines.append(f"  {dimension}")
            for entry in entries:
                status = "PASS" if entry.passed else "FAIL"
                expected = _format_value(entry.expected)
                observed = _format_value(entry.observed)
                lines.append(
                    f"    {entry.metric:<28} {status:<5} "
                    f"expected {expected}  |  observed {observed}  {entry.details}"
                )
        notes = review_notes.get(result.fixture)
        if notes:
            lines.append("  notes:")
            for note in notes:
                lines.append(f"    - {note}")

    lines.append("")
    lines.append("Failures are reported, never auto-tolerated; fix the")
    lines.append("converter, never the expectation, unless reviewed with the")
    lines.append("regeneration tool: python -m tests.quality.regenerate_baseline")
    return "\n".join(lines)


def _format_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
        if len(items) > 6:
            preview = ", ".join(items[:6])
            return f"[{preview}, ... ({len(items)} items)]"
        return f"[{', '.join(items)}]"
    text = str(value)
    if len(text) > 64:
        return text[:61] + "..."
    return text