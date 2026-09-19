"""Unit tests for the expectation schema, parsing, and evaluation.

These tests exercise the expectation engine in isolation -- no conversion,
no corpus: they pin the vocabulary of the M6.3 baseline (types, aliases,
validation) and the evaluator for every supported expectation type.
"""

from __future__ import annotations

import pytest

from .expectations import (
    QualityBaselineError,
    parse_expectation,
    evaluate_expectation,
    resolve_observed_field,
)

pytestmark = pytest.mark.quality

#: A minimal, real-shaped observation dict used by the evaluator tests.
MINIOBS = {
    "classification": "TEXT",
    "paragraphs": 14,
    "headings": 3,
    "page_breaks": 5,
    "empty_paragraphs": 0,
    "near_empty_paragraphs": 0,
    "body_text": "The Lantern Keeper\nby Ada Grant\nChapter 1: The Harbor Lights",
    "epub_valid": True,
    "epub_text": "The Lantern Keeper\nChapter 1: The Harbor Lights",
    "column_tokens": ["L1", "R1", "L2", "R2"],
    "ocr_calls": 0,
    "ocr_text_present": False,
}


@pytest.mark.parametrize(
    ("metric", "raw", "field"),
    [
        ("paragraphs", {"type": "minimum", "expected": 12}, "paragraphs"),
        ("headings", {"type": "minimum", "expected": 3}, "headings"),
        ("page_breaks", {"type": "exact", "expected": 5}, "page_breaks"),
        ("empty_paragraphs", {"type": "maximum", "expected": 0}, "empty_paragraphs"),
        ("ocr_text_present", {"type": "boolean", "expected": False}, "ocr_text_present"),
        ("column_tokens", {"type": "ordered_sequence", "expected": ["L1", "R1"]}, "column_tokens"),
        ("contains", {"type": "contains", "expected": ["x"]}, "body_text"),
        ("excludes", {"type": "excludes", "expected": ["x"]}, "body_text"),
    ],
)
def test_parse_expectation_resolves_field(metric, raw, field):
    expectation = parse_expectation("structure", metric, raw)
    assert expectation.dimension == "structure"
    assert expectation.metric == metric
    assert expectation.observed_field == field


def test_epub_dimension_text_surface():
    exp = parse_expectation("epub", "contains", {"type": "contains", "expected": ["x"]})
    assert exp.observed_field == "epub_text"
    exp = parse_expectation("ocr", "excludes", {"type": "excludes", "expected": ["x"]})
    assert exp.observed_field == "body_text"


def test_epub_valid_alias_maps_to_epub_valid_field():
    exp = parse_expectation("epub", "valid", {"type": "boolean", "expected": True})
    assert exp.observed_field == "epub_valid"


@pytest.mark.parametrize(
    "raw",
    [
        {"expected": 3},
        {"type": "minimum"},
        {"type": "unsupported", "expected": 3},
        {"type": "minimum", "expected": "many"},
        {"type": "boolean", "expected": "yes"},
        {"type": "contains", "expected": ["x", 3]},
        {"type": "ordered_sequence", "expected": ["L1", None]},
    ],
)
def test_parse_rejects_malformed_expectations(raw):
    with pytest.raises(QualityBaselineError):
        parse_expectation("structure", "paragraphs", raw)


def test_parse_rejects_unknown_metric():
    with pytest.raises(QualityBaselineError):
        parse_expectation("structure", "bookmarks", {"type": "minimum", "expected": 0})


def test_resolve_observed_field_unknown_dimension_free_text_raises():
    with pytest.raises(KeyError):
        resolve_observed_field("nonsense", "contains")


@pytest.mark.parametrize(
    ("metric", "raw", "passed"),
    [
        ("paragraphs", {"type": "minimum", "expected": 12}, True),
        ("paragraphs", {"type": "minimum", "expected": 15}, False),
        ("page_breaks", {"type": "exact", "expected": 5}, True),
        ("page_breaks", {"type": "exact", "expected": 4}, False),
        ("empty_paragraphs", {"type": "maximum", "expected": 0}, True),
        ("empty_paragraphs", {"type": "maximum", "expected": -1}, False),
    ],
)
def test_numeric_evaluators(metric, raw, passed):
    expectation = parse_expectation("structure", metric, raw)
    assert evaluate_expectation(MINIOBS, expectation).passed is passed


def test_boolean_evaluator():
    expectation = parse_expectation(
        "epub", "valid", {"type": "boolean", "expected": True}
    )
    assert evaluate_expectation(MINIOBS, expectation).passed is True
    expectation = parse_expectation(
        "epub", "valid", {"type": "boolean", "expected": False}
    )
    assert evaluate_expectation(MINIOBS, expectation).passed is False


def test_contains_reports_missing_items():
    expectation = parse_expectation(
        "structure",
        "contains",
        {"type": "contains", "expected": ["by Ada Grant", "ORPHANED STRING"]},
    )
    result = evaluate_expectation(MINIOBS, expectation)
    assert result.passed is False
    assert "ORPHANED STRING" in result.details


def test_excludes_reports_leaked_items():
    expectation = parse_expectation(
        "ocr",
        "excludes",
        {"type": "excludes", "expected": ["OCR-TEXT-FOR-CALL", "Tesseract"]},
    )
    result = evaluate_expectation(MINIOBS, expectation)
    assert result.passed is True
    assert "no excluded content present" in result.details

    leaked = parse_expectation(
        "structure",
        "excludes",
        {"type": "excludes", "expected": ["by Ada Grant"]},
    )
    leaked_result = evaluate_expectation(MINIOBS, leaked)
    assert leaked_result.passed is False
    assert "by Ada Grant" in leaked_result.details


def test_ordered_sequence_evaluator():
    expectation = parse_expectation(
        "reading_order",
        "column_tokens",
        {"type": "ordered_sequence", "expected": ["L1", "R1", "L2", "R2"]},
    )
    result = evaluate_expectation(MINIOBS, expectation)
    assert result.passed is True
    assert "24" not in result.details

    wrong = parse_expectation(
        "reading_order",
        "column_tokens",
        {"type": "ordered_sequence", "expected": ["R1", "L1", "L2", "R2"]},
    )
    wrong_result = evaluate_expectation(MINIOBS, wrong)
    assert wrong_result.passed is False
    assert "sequence mismatch" in wrong_result.details


def test_evaluator_rejects_missing_observation_field():
    expectation = parse_expectation(
        "epub", "contains", {"type": "contains", "expected": ["anything"]}
    )
    partial = {key: value for key, value in MINIOBS.items() if key != "epub_text"}
    with pytest.raises(QualityBaselineError):
        evaluate_expectation(partial, expectation)


def test_result_as_dict_shape():
    expectation = parse_expectation(
        "structure", "paragraphs", {"type": "minimum", "expected": 12}
    )
    result = evaluate_expectation(MINIOBS, expectation)
    rendered = result.as_dict()
    assert rendered["metric"] == "paragraphs"
    assert rendered["status"] == "PASS"
    assert rendered["type"] == "minimum"
    assert rendered["expected"] == 12
    assert rendered["observed"] == 14
    assert "details" in rendered