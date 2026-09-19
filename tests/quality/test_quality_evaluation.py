"""Tests for per-fixture and corpus-level quality evaluation.

Evaluation is a pure function: measurements in, verdicts out. These tests pin
the all-green status of the committed baseline over the current converter, the
manifest-derived classification check, and -- critically -- that a failing
expectation surfaces as a per-metric FAIL with actionable details instead of
being averaged away or auto-tolerated.
"""

from __future__ import annotations

import pytest

from tests.fixtures.corpus import document_path, iter_documents

from .evaluation import evaluate_corpus, evaluate_fixture
from .expectations import QualityBaselineError

pytestmark = pytest.mark.quality


def test_every_fixture_passes_committed_baseline(quality_results):
    for fixture_id, result in quality_results.items():
        assert result.passed, fixture_id


def test_results_cover_whole_corpus(quality_results):
    assert set(quality_results) == {doc["id"] for doc in iter_documents()}


@pytest.mark.parametrize("fixture_id", [doc["id"] for doc in iter_documents()])
def test_classification_result_derived_from_manifest(quality_results, fixture_id):
    manifest = {
        doc["id"]: doc for doc in iter_documents()
    }[fixture_id]["classification"]["expected"]
    result = quality_results[fixture_id]
    classification = [
        entry for entry in result.results if entry.dimension == "classification"
    ]
    assert len(classification) == 1
    entry = classification[0]
    assert entry.metric == "classification"
    assert entry.expected == manifest
    assert entry.observed == result.expected_classification
    assert entry.passed is True


def test_evaluation_reports_every_metric(quality_results):
    for result in quality_results.values():
        assert len(result.results) >= 12


def test_reading_order_dimension_present_for_twocolumn(quality_results):
    result = quality_results["twocolumn_article"]
    reading_order = [
        entry for entry in result.results if entry.dimension == "reading_order"
    ]
    assert reading_order
    assert all(entry.passed for entry in reading_order)


def test_failing_expectation_marks_fixture_failed():
    metrics = {
        "classification": "TEXT",
        "paragraphs": 14,
        "headings": 3,
        "body_text": "The Lantern Keeper",
        "classification": "TEXT",
    }
    expectations = {
        "structure": {
            "paragraphs": {"type": "minimum", "expected": 9999}
        }
    }
    result = evaluate_fixture(
        "novel_basic", metrics, expectations, expected_classification="TEXT"
    )
    assert result.passed is False
    paragraph = [
        entry for entry in result.results if entry.metric == "paragraphs"
    ][0]
    assert paragraph.passed is False
    assert "9999" in paragraph.details
    assert paragraph.expected == 9999
    assert paragraph.observed == 14


def test_classification_mismatch_is_reported():
    metrics = {
        "classification": "SCANNED",
        "paragraphs": 1,
        "body_text": "",
    }
    expectations = {"structure": {"paragraphs": {"type": "minimum", "expected": 1}}}
    result = evaluate_fixture(
        "novel_basic", metrics, expectations, expected_classification="TEXT"
    )
    assert result.passed is False
    classification = [
        entry for entry in result.results if entry.dimension == "classification"
    ][0]
    assert classification.passed is False
    assert "SCANNED" in classification.details


def test_observation_without_classification_is_rejected():
    metrics = {"paragraphs": 14}
    expectations = {"structure": {}}
    # structure is empty -> no expectations declared; classification check first.
    with pytest.raises(QualityBaselineError):
        evaluate_fixture(
            "novel_basic", metrics, expectations, expected_classification="TEXT"
        )


def test_fixture_without_declared_expectations_is_rejected():
    metrics = {"classification": "TEXT"}
    with pytest.raises(QualityBaselineError):
        evaluate_fixture(
            "novel_basic", metrics, {}, expected_classification="TEXT"
        )


def test_evaluate_corpus_requires_full_coverage(quality_observations, quality_baseline):
    missing = quality_baseline["documents"].copy()
    missing.pop("novel_basic")
    payload = {**quality_baseline, "documents": missing}
    with pytest.raises(QualityBaselineError):
        evaluate_corpus(
            quality_observations,
            payload,
            {"novel_basic": "TEXT"},
        )


def test_evaluate_corpus_rejects_missing_observation(quality_baseline):
    observations = {"novel_basic": {"classification": "TEXT"}}
    with pytest.raises(QualityBaselineError):
        evaluate_corpus(observations, quality_baseline, {})


def test_fixture_passed_property_is_all_metrics():
    from .expectations import ExpectationResult

    result = evaluate_fixture(
        "novel_basic",
        {"classification": "TEXT", "paragraphs": 14, "body_text": "x"},
        {
            "structure": {
                "paragraphs": {"type": "minimum", "expected": 14},
                "contains": {"type": "contains", "expected": ["x"]},
            }
        },
        expected_classification="TEXT",
    )
    assert result.passed is True
    assert all(isinstance(entry, ExpectationResult) for entry in result.results)