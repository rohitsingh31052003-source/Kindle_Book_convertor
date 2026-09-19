"""Regenerate ``tests/fixtures/quality_baseline.json`` (M6.3).

This is the maintenance script behind the M6.3 quality baseline. It re-runs
the real conversion pipeline over every M6.1 corpus fixture -- with the
deterministic :class:`CountingOCR` engine, never Tesseract -- and reports how
the *authored quality expectations* hold against the fresh measurement:

* ``exact`` metrics (e.g. ``page_breaks == pages - 1``) are updated to the
  fresh observed value, exactly like M6.2 updates structural pins;
* every other expectation type (``minimum`` / ``maximum`` / ``contains`` /
  ``excludes`` / ``boolean`` / ``ordered_sequence``) is human-authored and is
  carried forward verbatim; regeneration never fabricates it from a
  measurement and never weakens a failing expectation silently.

`--write` refuses to persist when an authored expectation fails the fresh
measurement: a failing expectation is a finding, not a baseline edit.

Deterministic and offline: no Tesseract, no Calibre, no network, no clocks.

Usage
-----
    python -m tests.quality.regenerate_baseline
        Preview which baseline values would change (writes nothing).

    python -m tests.quality.regenerate_baseline --write
        Regenerate the baseline file in place.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = str(_REPO_ROOT / "src")
if _SRC not in sys.path:
    # Bootstrap for `python -m tests.quality.regenerate_baseline` without an
    # installed package; pytest already puts src/ on sys.path itself.
    sys.path.insert(0, _SRC)

from tests.fixtures.corpus import iter_documents

from .baseline import (
    QUALITY_BASELINE_PATH,
    QUALITY_SCHEMA_VERSION,
    load_baseline,
    validate_baseline,
)
from .expectations import DIMENSIONS, parse_expectation
from .harness import observe_quality, observed_metrics


def _observe(fixture_id: str) -> dict[str, Any]:
    return observed_metrics(observe_quality(fixture_id))


def _recompute_document(
    fixture_id: str, previous: dict[str, Any]
) -> dict[str, Any] | None:
    """Fresh expectations for one fixture, preserving authored values."""
    old_entry = previous["documents"].get(fixture_id)
    if old_entry is None:
        print(
            f"warning: committed quality baseline has no entry for {fixture_id}; "
            "refusing to fabricate authored expectations from measurement."
        )
        return None

    observation = _observe(fixture_id)
    fresh = copy.deepcopy(old_entry)

    for dimension in DIMENSIONS:
        metrics = fresh.get(dimension)
        if not metrics:
            continue
        for metric, raw in metrics.items():
            if raw.get("type") != "exact":
                continue
            raw["expected"] = observation[metric]

    return fresh


def _failing_authored(
    fixture_id: str, entry: dict[str, Any]
) -> list[str]:
    """Authored (non-exact) expectations that fail the fresh measurement."""
    observation = _observe(fixture_id)
    failures: list[str] = []
    for dimension in DIMENSIONS:
        for metric, raw in (entry.get(dimension) or {}).items():
            if raw.get("type") == "exact":
                continue
            result = parse_expectation(dimension, metric, raw)
            from .expectations import evaluate_expectation

            verdict = evaluate_expectation(observation, result)
            if not verdict.passed:
                failures.append(
                    f"    {fixture_id} {dimension}.{metric}: "
                    f"{verdict.details}"
                )
    return failures


def build_baseline(previous: dict[str, Any]) -> dict[str, Any]:
    """Fresh quality baseline from the committed one + fresh measurement.

    ``previous`` is required: the authored expectations are the memory of the
    quality framework and are carried forward verbatim (only ``exact`` metrics
    follow the measurement). A fixture absent from ``previous`` is skipped
    with a warning rather than auto-seeded.
    """
    entries: dict[str, Any] = {}
    for document in iter_documents():
        fixture_id = document["id"]
        entry = _recompute_document(fixture_id, previous)
        if entry is not None:
            entries[fixture_id] = entry

    return {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "description": previous.get("description")
        or (
            "M6.3 conversion-quality expectations over the M6.1 corpus. "
            "Authored expectations are human claims; only exact pins are "
            "recomputed by the regeneration tool."
        ),
        "dimension_order": list(
            previous.get("dimension_order", ["classification", "structure", "reading_order", "ocr", "images", "epub"])
        ),
        "documents": entries,
    }


def _diff_lines(previous: dict[str, Any], fresh: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for fixture_id in sorted(set(previous["documents"]) | set(fresh["documents"])):
        old = json.dumps(previous["documents"].get(fixture_id, {}), sort_keys=True)
        new = json.dumps(fresh["documents"].get(fixture_id, {}), sort_keys=True)
        if old != new:
            lines.append(f"  {fixture_id}: CHANGED")
    if not lines:
        lines.append("  (no expectation value would change)")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the regenerated baseline to disk (default: preview only)",
    )
    args = parser.parse_args()

    previous = load_baseline() if QUALITY_BASELINE_PATH.is_file() else None
    if previous is None:
        print(
            f"no committed baseline at {QUALITY_BASELINE_PATH}; "
            "the quality baseline is authored and cannot be bootstrapped "
            "from measurement alone."
        )
        sys.exit(1)

    fresh = build_baseline(previous)

    print("expectation changes vs committed baseline:")
    print("\n".join(_diff_lines(previous, fresh)))

    failures: list[str] = []
    for document in iter_documents():
        fixture_id = document["id"]
        entry = fresh["documents"].get(fixture_id)
        if entry is None:
            continue
        failures.extend(_failing_authored(fixture_id, entry))
    if failures:
        print("\nauthored expectations failing the fresh measurement:")
        print("\n".join(failures))
        print(
            "\nquality drift detected: a failing authored expectation is a "
            "finding. Fix the converter, or review and edit the expectation "
            "deliberately, rather than letting --write mask it."
        )

    if not args.write:
        print("\npreview only (nothing written); re-run with --write to persist "
              f"to {QUALITY_BASELINE_PATH}")
        return

    if failures:
        print("\nrefusing to write: authored expectations fail the fresh "
              "measurement (see above).")
        sys.exit(2)

    validate_baseline(fresh)
    QUALITY_BASELINE_PATH.write_text(
        json.dumps(fresh, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {QUALITY_BASELINE_PATH}")


if __name__ == "__main__":
    main()