"""Tests for quality-baseline loading, validation, and schema discipline.

The baseline is authored and human-reviewed; these tests keep it that way:
full corpus coverage in both directions, only supported dimensions/metrics/
expectation types, and *no* classification duplication (the manifest is the
single source of truth for classification).
"""

from __future__ import annotations

import pytest

from tests.fixtures.corpus import iter_documents

from .baseline import QUALITY_SCHEMA_VERSION, load_baseline, validate_baseline
from .expectations import DIMENSIONS, EXPECTATION_TYPES, QualityBaselineError

pytestmark = pytest.mark.quality


def test_baseline_schema_version(quality_baseline):
    assert quality_baseline["schema_version"] == QUALITY_SCHEMA_VERSION


def test_baseline_has_every_corpus_document(quality_baseline):
    documents = quality_baseline["documents"]
    assert set(documents) >= {doc["id"] for doc in iter_documents()}
    assert len(documents) == 11


def test_baseline_has_no_documents_outside_corpus():
    baseline = load_baseline()
    corpus_ids = {document["id"] for document in iter_documents()}
    assert set(baseline["documents"]) - corpus_ids == set()


def test_every_fixture_declares_expectations(quality_baseline):
    for fixture_id, entry in quality_baseline["documents"].items():
        dimensions = {key for key in entry if key != "notes"}
        assert not dimensions - set(DIMENSIONS), fixture_id
        declared = sum(len(entry.get(dim, {})) for dim in DIMENSIONS)
        assert declared > 0, fixture_id


def test_all_dimensions_are_canonical(quality_baseline):
    assert quality_baseline["dimension_order"] == list(DIMENSIONS)


def test_classification_is_not_stored_in_baseline(quality_baseline):
    for entry in quality_baseline["documents"].values():
        assert "classification" not in entry


def test_only_supported_expectation_types(quality_baseline):
    for entry in quality_baseline["documents"].values():
        for dimension in DIMENSIONS:
            for raw in entry.get(dimension, {}).values():
                assert raw["type"] in EXPECTATION_TYPES


def test_contains_excludes_use_string_lists(quality_baseline):
    for entry in quality_baseline["documents"].values():
        for dimension in DIMENSIONS:
            for metric, raw in entry.get(dimension, {}).items():
                if metric in ("contains", "excludes"):
                    assert isinstance(raw["expected"], list)
                    assert all(isinstance(item, str) for item in raw["expected"])


def test_scalar_expectations_are_numeric(quality_baseline):
    for entry in quality_baseline["documents"].values():
        for dimension in DIMENSIONS:
            for metric, raw in entry.get(dimension, {}).items():
                if raw["type"] in ("minimum", "maximum", "exact"):
                    assert isinstance(raw["expected"], (int, float))


def test_review_notes_are_text(quality_baseline):
    for entry in quality_baseline["documents"].values():
        notes = entry.get("notes", [])
        assert isinstance(notes, list)
        assert all(isinstance(note, str) for note in notes)


def test_validate_baseline_rejects_missing_document():
    payload = load_baseline()
    documents = dict(payload["documents"])
    documents.pop(next(iter(documents)))
    payload = {**payload, "documents": documents}
    with pytest.raises(QualityBaselineError):
        validate_baseline(payload)


def test_validate_baseline_rejects_wrong_schema_version():
    payload = load_baseline()
    payload = {**payload, "schema_version": QUALITY_SCHEMA_VERSION + 1}
    with pytest.raises(QualityBaselineError):
        validate_baseline(payload)


def test_validate_baseline_rejects_unknown_dimension():
    payload = load_baseline()
    documents = dict(payload["documents"])
    documents["novel_basic"] = {**documents["novel_basic"], "spelling": {}}
    payload = {**payload, "documents": documents}
    with pytest.raises(QualityBaselineError):
        validate_baseline(payload)


def test_validate_baseline_rejects_unknown_metric():
    payload = load_baseline()
    documents = dict(payload["documents"])
    entry = dict(documents["novel_basic"])
    entry["structure"] = {**entry.get("structure", {}), "bookmarks": {"type": "minimum", "expected": 0}}
    documents["novel_basic"] = entry
    payload = {**payload, "documents": documents}
    with pytest.raises(QualityBaselineError):
        validate_baseline(payload)


def test_validate_baseline_rejects_unknown_type():
    payload = load_baseline()
    documents = dict(payload["documents"])
    entry = dict(documents["novel_basic"])
    entry["structure"] = {**entry.get("structure", {}), "paragraphs": {"type": "around", "expected": 14}}
    documents["novel_basic"] = entry
    payload = {**payload, "documents": documents}
    with pytest.raises(QualityBaselineError):
        validate_baseline(payload)