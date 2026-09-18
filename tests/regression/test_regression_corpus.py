"""Regression tests for the conversion pipeline over the M6.1 corpus.

Every expectation lives either in the corpus manifest (analysis results,
already covered by ``tests/test_corpus.py`` -- not duplicated here) or in
``tests/fixtures/regression_baseline.json`` (pipeline *behavior*: block
composition, OCR routing, headings, chapter detection, EPUB artifact
structure, and human-authored content anchors).

All conversions run with the deterministic :class:`CountingOCR` engine, so
the suite needs no Tesseract and no network, and produces structurally
stable EPUBs regardless of platform or optional dependencies.

Marked ``regression``: ``pytest -m regression`` selects these.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from tests.fixtures.corpus import iter_documents
from tests.regression.harness import (
    epub_pages,
    load_baseline,
    manifest_document,
    observe_conversion,
)

pytestmark = pytest.mark.regression

#: The deterministic marker the injected OCR engine emits (one per OCR call).
_OCR_MARKER = "OCR-TEXT-FOR-CALL"

#: Manifest ``expectations`` keys describing repeated header/footer furniture.
_FURNITURE_KEYS = ("header_text", "footer_text")

_ANCHOR_KINDS = ("contains", "excludes")
_EPUB_NUMERIC_FACTS = (
    "chapter_files",
    "image_resources",
    "page_breaks",
    "heading_tags",
    "nav_entries",
)
_ANALYSIS_FACTS = (
    "document_type",
    "page_count",
    "text_page_count",
    "image_page_count",
)


@pytest.fixture(scope="session")
def workdir():
    """A session-lifetime directory keeping generated EPUBs around."""
    with tempfile.TemporaryDirectory(prefix="kbc-regression-") as tmp:
        yield Path(tmp)


@pytest.fixture(scope="session")
def converted_documents(workdir) -> dict[str, dict]:
    """Convert every fixture once and cache the observations.

    Session-scoped so the parametrized tests below share a single
    conversion run instead of converting eleven fixtures per test.
    """
    baseline = load_baseline()
    return {
        document["id"]: {
            "observation": observe_conversion(
                document["id"], keep_text=True, workdir=workdir
            ),
            "expected": baseline["documents"][document["id"]],
        }
        for document in iter_documents()
    }


@pytest.fixture(params=[document["id"] for document in iter_documents()])
def fixture_id(request) -> str:
    return request.param


@pytest.fixture()
def converted(converted_documents: dict[str, dict], fixture_id: str) -> dict:
    return converted_documents[fixture_id]


def test_blocks_match_baseline(converted: dict) -> None:
    assert converted["observation"]["blocks"] == converted["expected"]["blocks"]


def test_ocr_routing_matches_baseline(converted: dict) -> None:
    assert converted["observation"]["ocr_calls"] == converted["expected"]["ocr_calls"]


def test_heading_texts_match_baseline(converted: dict) -> None:
    assert (
        converted["observation"]["heading_texts"]
        == converted["expected"]["heading_texts"]
    )


def test_chapter_count_matches_baseline(converted: dict) -> None:
    assert (
        converted["observation"]["chapter_count"]
        == converted["expected"]["chapter_count"]
    )


@pytest.mark.parametrize("kind", _ANCHOR_KINDS)
def test_body_content_anchors(kind: str, converted: dict, fixture_id: str) -> None:
    body_text = converted["observation"]["epub"]["text"]
    for anchor in converted["expected"].get(kind, []):
        if kind == "contains":
            assert anchor in body_text, f"{fixture_id}: missing {anchor!r} in body"
        else:
            assert anchor not in body_text, f"{fixture_id}: {anchor!r} leaked into body"


@pytest.mark.parametrize("fact", _EPUB_NUMERIC_FACTS)
def test_epub_structure_matches_baseline(fact: str, converted: dict) -> None:
    observed = converted["observation"]["epub"]
    assert observed[fact] == converted["expected"]["epub"][fact]


def test_epub_validates(converted: dict) -> None:
    epub = converted["observation"]["epub"]
    assert epub["valid"] is True
    assert epub["errors"] == 0


def test_ocr_marker_uses_deterministic_sequence(
    converted: dict, fixture_id: str
) -> None:
    """OCR-produced text follows the injected engine's predictable sequence."""
    body_text = converted["observation"]["epub"]["text"]
    for call_number in range(1, converted["expected"]["ocr_calls"] + 1):
        assert f"{_OCR_MARKER}-{call_number}" in body_text, fixture_id


def test_twocolumn_reading_order_survives_conversion(
    converted_documents: dict[str, dict],
) -> None:
    """Multi-column pages keep left-before-right interleaved reading order."""
    fixture = converted_documents["twocolumn_article"]
    expected_tokens = fixture["expected"]["column_tokens"]
    pages = epub_pages(fixture["observation"]["epub"]["path"])
    parsed = [_tokens(" ".join(page_text)) for page_text in pages]
    parsed = [tokens for tokens in parsed if tokens]
    assert parsed and all(tokens == expected_tokens for tokens in parsed)


def _tokens(text: str) -> list[str]:
    return re.findall(r"\b[LR][1-4]\b", text)


# --------------------------------------------------------------------------- #
# Manifest-driven regression (corpus metadata is the declared source of truth)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fact", _ANALYSIS_FACTS)
def test_analysis_matches_manifest(fact: str, converted: dict, fixture_id: str) -> None:
    """PDF classification and page counts still match the corpus manifest.

    The manifest (M6.1) declares what each fixture *is*; this asserts the
    converter still observes exactly that, which is the classification half of
    the regression contract.
    """
    expectations = manifest_document(fixture_id)["expectations"]
    observed = converted["observation"]["analysis"]
    if fact == "document_type":
        expected = manifest_document(fixture_id)["classification"]["expected"]
    else:
        expected = expectations[fact]
    assert observed[fact] == expected, fixture_id


@pytest.mark.parametrize("fact", _ANALYSIS_FACTS)
def test_analysis_matches_baseline(fact: str, converted: dict, fixture_id: str) -> None:
    """The frozen baseline repeats the analysis facts, so corpus drift shows up."""
    assert (
        converted["observation"]["analysis"][fact]
        == converted["expected"]["analysis"][fact]
    ), fixture_id


def test_ocr_routing_follows_classification(converted: dict, fixture_id: str) -> None:
    """TEXT fixtures stay native; SCANNED/MIXED fixtures route pages to OCR."""
    classification = manifest_document(fixture_id)["classification"]["expected"]
    ocr_calls = converted["observation"]["ocr_calls"]
    if classification == "TEXT":
        assert ocr_calls == 0, f"{fixture_id}: native text reached OCR"
    else:
        assert ocr_calls > 0, f"{fixture_id}: {classification} never reached OCR"


#: Fixture ids derived from the manifest for the focused expectations below.
_CHAPTER_FIXTURE_IDS = [
    document["id"] for document in iter_documents() if "min_chapters" in document["expectations"]
]
_IMAGE_FIXTURE_IDS = [
    document["id"]
    for document in iter_documents()
    if document["expectations"]["image_page_count"] > 0
]


@pytest.mark.parametrize("fixture_id", _CHAPTER_FIXTURE_IDS)
def test_chapter_minimum_from_manifest(
    converted_documents: dict[str, dict], fixture_id: str
) -> None:
    """Fixtures that declare ``min_chapters`` still detect at least that many."""
    minimum = manifest_document(fixture_id)["expectations"]["min_chapters"]
    assert converted_documents[fixture_id]["observation"]["chapter_count"] >= minimum


@pytest.mark.parametrize("fixture_id", _IMAGE_FIXTURE_IDS)
def test_image_fixtures_keep_images(
    converted_documents: dict[str, dict], fixture_id: str
) -> None:
    """Documents with image pages keep images in the Book and the EPUB.

    Guards "image placement does not disappear unexpectedly": a fixture the
    manifest declares as carrying images must still produce image blocks and
    at least one packaged image resource.
    """
    observation = converted_documents[fixture_id]["observation"]
    assert observation["blocks"]["images"] >= 1, fixture_id
    assert observation["epub"]["image_resources"] >= 1, fixture_id


def test_declared_column_markers_survive_conversion(
    converted_documents: dict[str, dict],
) -> None:
    """The manifest's left/right column markers both reach the EPUB body."""
    document = manifest_document("twocolumn_article")
    expectations = document["expectations"]
    body = converted_documents["twocolumn_article"]["observation"]["epub"]["text"]
    for key in ("left_column_marker", "right_column_marker"):
        marker = expectations[key]
        assert marker in body, f"twocolumn_article: {key} {marker!r} missing"


def test_header_footer_furniture_stays_out_of_body(
    converted_documents: dict[str, dict],
) -> None:
    """Repeated header/footer furniture is never emitted as normal body content."""
    declaring = [
        document
        for document in iter_documents()
        if set(_FURNITURE_KEYS) & set(document["expectations"])
    ]
    assert declaring, "corpus no longer declares header/footer furniture"
    for document in declaring:
        body = converted_documents[document["id"]]["observation"]["epub"]["text"]
        for key in _FURNITURE_KEYS:
            furniture = document["expectations"].get(key)
            if furniture is None:
                continue
            assert furniture not in body, (
                f"{document['id']}: {key} {furniture!r} leaked into body text"
            )