"""Tests for the M6.3 measurement harness over the real M6.1 corpus.

The harness converts every fixture through the real public pipeline with the
deterministic counting OCR engine and derives the quality metric dicts that
the expectation engine consumes. These tests pin the measurement surface:
full corpus coverage, manifest-derived classification, deterministic and
path-free observations.
"""

from __future__ import annotations

import pytest

from tests.fixtures.corpus import iter_documents

from .expectations import KNOWN_FIELDS
from .harness import expected_classification, observed_metrics, observe_quality

pytestmark = pytest.mark.quality


def test_observations_cover_every_corpus_document(quality_observations):
    corpus_ids = {document["id"] for document in iter_documents()}
    assert set(quality_observations) == corpus_ids
    assert len(quality_observations) == 11


def test_observation_metric_namespace_matches_known_fields(quality_observations):
    for metrics in quality_observations.values():
        assert set(metrics) >= KNOWN_FIELDS
        assert "contains" not in metrics
        assert "excludes" not in metrics


def test_classification_is_manifest_driven(quality_observations):
    for document in iter_documents():
        fixture_id = document["id"]
        assert (
            quality_observations[fixture_id]["classification"]
            == document["classification"]["expected"]
        )


def test_expected_classification_matches_manifest(quality_observations):
    for document in iter_documents():
        fixture_id = document["id"]
        assert expected_classification(fixture_id) == document["classification"]["expected"]


@pytest.mark.parametrize("fixture_id", [document["id"] for document in iter_documents()])
def test_epub_valid_for_every_fixture(quality_observations, fixture_id):
    assert quality_observations[fixture_id]["epub_valid"] is True


@pytest.mark.parametrize("fixture_id", [document["id"] for document in iter_documents()])
def test_no_empty_chapters_in_epub(quality_observations, fixture_id):
    assert quality_observations[fixture_id]["epub_empty_chapters"] == 0


@pytest.mark.parametrize("fixture_id", [document["id"] for document in iter_documents()])
def test_single_chapter_file_per_fixture(quality_observations, fixture_id):
    # M2.x EPUB generation renders the Book as one chapter; the fixtures never
    # produce split EPUB chapters today. Quality records the honest number.
    assert quality_observations[fixture_id]["epub_chapter_files"] == 1


@pytest.mark.parametrize("fixture_id", [document["id"] for document in iter_documents()])
def test_ocr_engine_runs_only_when_expected(quality_observations, fixture_id):
    manifest = {
        document["id"]: document
        for document in iter_documents()
    }[fixture_id]
    expected = manifest["classification"]["expected"]
    calls = quality_observations[fixture_id]["ocr_calls"]
    if expected == "SCANNED":
        assert calls == 5
    elif expected == "MIXED":
        assert calls >= 1
    else:
        assert calls == 0


def test_scanned_fixture_keeps_ocr_call_tokens():
    observation = observe_quality("scanned_book")
    assert observation.ocr_calls == 5
    assert observation.ocr_text_present is True
    assert "OCR-TEXT-FOR-CALL-1" in observation.body_text
    assert "OCR-TEXT-FOR-CALL-5" in observation.body_text
    assert "Tesseract" not in observation.body_text


def test_twocolumn_reading_order_tokens_present():
    observation = observe_quality("twocolumn_article")
    assert observation.column_tokens[:4] == ("L1", "R1", "L2", "R2")
    assert len(observation.column_tokens) == 24


def test_chapter_detection_surface():
    observation = observe_quality("chapters_long")
    assert observation.chapters == 8
    assert observation.toc_entries == 8
    assert observation.headings == 8


def test_novel_chapter_detection_surface():
    observation = observe_quality("novel_basic")
    assert observation.chapters == 3
    assert observation.toc_entries == 3
    assert observation.headings == 3


def test_heading_integrity_for_textbook():
    observation = observe_quality("textbook_dense")
    assert observation.headings >= 13
    assert "Introduction to Coastal Hydrography" in observation.epub_text


def test_image_surfaces():
    scanned = observe_quality("scanned_book")
    assert scanned.image_blocks == 5
    assert scanned.epub_image_resources == 5

    figures = observe_quality("text_with_images")
    assert figures.image_blocks == 4
    assert figures.epub_image_resources == 3


def test_page_breaks_are_a_book_artifact(quality_observations):
    for fixture_id in quality_observations:
        manifest = {
            document["id"]: document
            for document in iter_documents()
        }[fixture_id]
        pages = manifest["expectations"]["page_count"]
        expected_breaks = pages - 1
        assert (
            quality_observations[fixture_id]["page_breaks"]
            == expected_breaks
        )


def test_observation_is_deterministic():
    first = observed_metrics(observe_quality("edge_short_report"))
    second = observed_metrics(observe_quality("edge_short_report"))
    assert first == second


def test_observation_contains_no_paths():
    observation = observe_quality("novel_basic")
    rendered = str(observed_metrics(observation))
    assert "C:\\" not in rendered
    assert "Kindle Book Convertor" not in rendered