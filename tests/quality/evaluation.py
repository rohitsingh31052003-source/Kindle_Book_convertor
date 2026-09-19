"""Evaluation of measured quality against authored expectations.

``evaluate_fixture`` compares a measured metric dict with the authored
expectations for one fixture (plus the manifest-derived classification check)
and produces a flat, ordered list of :class:`ExpectationResult`. ``passed``
is true only when every declared expectation is satisfied.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .expectations import (
    QualityBaselineError,
    DIMENSIONS,
    ExpectationResult,
    parse_expectation,
    evaluate_expectation,
)


@dataclass(frozen=True, slots=True)
class FixtureQualityResult:
    """The full quality verdict for one fixture."""

    fixture: str
    expected_classification: str
    results: tuple[ExpectationResult, ...]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    def results_by_dimension(self) -> dict[str, tuple[ExpectationResult, ...]]:
        grouped: dict[str, list[ExpectationResult]] = {}
        for result in self.results:
            grouped.setdefault(result.dimension, []).append(result)
        return {
            dim: tuple(grouped[dim]) for dim in DIMENSIONS if dim in grouped
        }


def _classification_result(expected: str, observed: str) -> ExpectationResult:
    passed = expected == observed
    return ExpectationResult(
        dimension="classification",
        metric="classification",
        expectation_type="exact",
        expected=expected,
        observed=observed,
        passed=passed,
        details=(
            "manifest classification.expected matches observed PDF type"
            if passed
            else f"manifest expects {expected!r}, converter observed {observed!r}"
        ),
    )


def evaluate_fixture(
    fixture_id: str,
    metrics: Mapping[str, Any],
    fixture_expectations: Mapping[str, Mapping[str, Any]],
    expected_classification: str,
) -> FixtureQualityResult:
    """Evaluate one fixture's measurement against its authored expectations.

    ``metrics`` is the flat dict from ``observed_metrics``. ``fixture_expectations``
    is the per-document block of the quality baseline, grouped by dimension
    (classification expectations are *never* stored there -- they are derived
    from the corpus manifest via ``expected_classification``).
    """
    if "classification" not in metrics:
        raise QualityBaselineError(
            f"observation for {fixture_id!r} has no classification metric"
        )

    results: list[ExpectationResult] = [
        _classification_result(expected_classification, metrics["classification"])
    ]

    declared = 0
    for dimension in DIMENSIONS:
        expectations = fixture_expectations.get(dimension)
        if not expectations:
            continue
        for metric, raw in expectations.items():
            expectation = parse_expectation(dimension, metric, raw)
            results.append(evaluate_expectation(metrics, expectation))
            declared += 1

    if declared == 0:
        raise QualityBaselineError(
            f"fixture {fixture_id!r} declares no quality expectations"
        )

    return FixtureQualityResult(
        fixture=fixture_id,
        expected_classification=expected_classification,
        results=tuple(results),
    )


def evaluate_corpus(
    observations: Mapping[str, Mapping[str, Any]],
    baseline: Mapping[str, Any],
    expected_classifications: Mapping[str, str],
) -> dict[str, FixtureQualityResult]:
    """Evaluate every corpus document (from a session observation cache).

    Coverage is enforced both ways: every corpus document must have an
    observation and a baseline block, and the baseline may not declare
    documents that are not part of the corpus.
    """
    from tests.fixtures.corpus import iter_documents

    corpus_ids = sorted(doc["id"] for doc in iter_documents())
    documents = baseline.get("documents", {})

    missing_baseline = [fid for fid in corpus_ids if fid not in documents]
    if missing_baseline:
        raise QualityBaselineError(
            "baseline has no expectations for corpus documents: "
            + ", ".join(missing_baseline)
        )
    extra_baseline = [fid for fid in documents if fid not in corpus_ids]
    if extra_baseline:
        raise QualityBaselineError(
            "baseline declares documents outside the corpus: "
            + ", ".join(extra_baseline)
        )
    missing_obs = [fid for fid in corpus_ids if fid not in observations]
    if missing_obs:
        raise QualityBaselineError(
            "missing observations for corpus documents: "
            + ", ".join(missing_obs)
        )

    results: dict[str, FixtureQualityResult] = {}
    for fid in corpus_ids:
        entry = documents[fid]
        fixture_expectations = {
            key: value for key, value in entry.items() if key != "notes"
        }
        results[fid] = evaluate_fixture(
            fixture_id=fid,
            metrics=observations[fid],
            fixture_expectations=fixture_expectations,
            expected_classification=expected_classifications[fid],
        )
    return results