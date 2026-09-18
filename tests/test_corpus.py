"""M6.1 representative PDF corpus infrastructure tests.

Verifies the committed corpus at ``tests/fixtures/corpus``: the manifest is
valid and complete, the discovery API works, the fixture PDFs match their
manifest expectations when analyzed, key structural signals survive
reconstruction, and regeneration is byte-for-byte deterministic.
"""

from __future__ import annotations

import importlib
import json
import pathlib

import pymupdf
import pytest

from kindle_converter.pdf import (
    analyze_pdf,
    detect_chapters,
    detect_headers_footers,
    extract_page_layout,
    extract_pdf_images,
    reconstruct_layout,
    remove_page_numbers,
)

from tests.fixtures.corpus import (
    CORPUS_CATEGORIES,
    CORPUS_DIR,
    PDFS_DIR,
    CorpusError,
    document_exists,
    document_path,
    iter_documents,
    load_manifest,
)

#: Document IDs expected to contain explicit numbered chapters.
_CHAPTER_DOCUMENTS = {
    "novel_basic": 2,
    "chapters_long": 6,
}

#: Header / footer furniture expected inside the dedicated fixture.
_HEADERS_FOOTERS_TEXT = ("The Lantern Keeper", "Chapter One")

#: Fixtures whose body reading order must interleave two column markers.
_COLUMN_DOCUMENT = "twocolumn_article"

#: Minimum number of embedded image placements per fixture.
_MIN_IMAGE_PLACEMENTS = {
    "text_with_images": 3,
    "scanned_book": 5,
    "mixed_text_image": 3,
}


def _load_explicit_manifest() -> dict:
    with (CORPUS_DIR / "manifest.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def test_corpus_manifest_is_loadable() -> None:
    manifest = load_manifest()
    assert isinstance(manifest, dict)
    assert set(manifest) >= {"corpus", "documents", "external"}


def test_corpus_manifest_matches_generator_metadata() -> None:
    generator = importlib.import_module("tests.fixtures.corpus.generate_corpus")
    generated = generator.fixture_metadata()
    committed = _load_explicit_manifest()["documents"]
    assert committed == generated, (
        "manifest.json is stale; regenerate the corpus with "
        "python -m tests.fixtures.corpus.generate_corpus"
    )


def test_corpus_categories_are_complete_and_ordered() -> None:
    manifest = load_manifest()
    categories = set(manifest["corpus"]["categories"])
    assert categories == set(CORPUS_CATEGORIES)
    assert list(manifest["corpus"]["categories"]) == sorted(CORPUS_CATEGORIES)


def test_every_document_has_required_metadata() -> None:
    required_plain = {"id", "path", "category", "source", "license", "generator"}
    required_nested = {
        "classification": {"expected"},
        "expectations": {"page_count", "text_page_count", "image_page_count"},
    }
    seen_ids: set[str] = set()
    for document in iter_documents():
        assert isinstance(document, dict)
        assert required_plain <= set(document)
        classification = document["classification"]
        assert required_nested["classification"] <= set(classification)
        expectations = document["expectations"]
        assert required_nested["expectations"] <= set(expectations)
        assert document["id"] not in seen_ids, "duplicate fixture id"
        seen_ids.add(document["id"])
        assert document["source"] == "synthetic"
        assert "Proprietary" in document["license"]
    assert seen_ids, "manifest lists no documents"


def test_all_manifest_categories_are_represented() -> None:
    categories = {document["category"] for document in iter_documents()}
    assert categories == set(CORPUS_CATEGORIES)


def test_each_fixture_pdf_exists_and_opens() -> None:
    for document in iter_documents():
        document_path(document["id"])
        assert document_exists(document["id"])
        with pymupdf.open((PDFS_DIR / f"{document['id']}.pdf").as_posix()) as doc:
            assert doc.page_count == document["expectations"]["page_count"]


def test_discovery_api_rejects_unknown_document_id() -> None:
    with pytest.raises(CorpusError):
        document_path("no_such_fixture")
    with pytest.raises(CorpusError):
        document_exists("no_such_fixture")


def test_analysis_matches_manifest_classification() -> None:
    for document in iter_documents():
        analysis = analyze_pdf((PDFS_DIR / f"{document['id']}.pdf").as_posix())
        expected = document["classification"]["expected"]
        assert analysis.document_type.name == expected, document["id"]
        expectations = document["expectations"]
        assert analysis.page_count == expectations["page_count"], document["id"]
        assert analysis.text_page_count == expectations["text_page_count"], document["id"]
        assert analysis.image_page_count == expectations["image_page_count"], document["id"]


def test_chapter_heavy_documents_detect_chapters() -> None:
    for document_id, minimum in _CHAPTER_DOCUMENTS.items():
        layout = extract_page_layout((PDFS_DIR / f"{document_id}.pdf").as_posix())
        document, _, _, _ = reconstruct_layout(layout)
        detected = detect_chapters(document)
        assert len(detected.chapters) >= minimum, document_id


def test_two_column_document_interleaves_columns() -> None:
    layout = extract_page_layout(
        (PDFS_DIR / f"{_COLUMN_DOCUMENT}.pdf").as_posix()
    )
    document, _, _, _ = reconstruct_layout(layout)
    elements_by_page: dict[int, list[str]] = {}
    for element in document.elements:
        if element.text:
            elements_by_page.setdefault(element.page_number, []).append(
                element.text.split()[0]
            )
    assert set(elements_by_page) == {1, 2, 3}
    for page_number, markers in elements_by_page.items():
        left = [i for i, marker in enumerate(markers) if marker.startswith("L")]
        right = [i for i, marker in enumerate(markers) if marker.startswith("R")]
        assert len(left) == len(right) == 4, page_number
        assert all(left[i] < right[i] for i in range(len(left))), page_number


def test_headers_footers_fixture_detects_furniture() -> None:
    page_id = "headers_footers"
    layout = extract_page_layout((PDFS_DIR / f"{page_id}.pdf").as_posix())
    _, paragraphs, _, _ = reconstruct_layout(layout)
    detected = detect_headers_footers(paragraphs)
    texts = {detected_item.text for page in detected.pages for detected_item in page.detected}
    assert set(_HEADERS_FOOTERS_TEXT) <= texts
    assert any(text.strip().isdigit() for text in texts), "page numbers not detected"


def test_image_fixtures_report_minimum_placements() -> None:
    for document_id, minimum in _MIN_IMAGE_PLACEMENTS.items():
        result = extract_pdf_images((PDFS_DIR / f"{document_id}.pdf").as_posix())
        assert result.placement_count >= minimum, document_id


def test_regeneration_is_byte_identical(tmp_path: pathlib.Path) -> None:
    generator = importlib.import_module("tests.fixtures.corpus.generate_corpus")
    out_dir = tmp_path / "pdfs"
    out_dir.mkdir()
    fixtures = {fixture.id: fixture for fixture in generator.FIXTURES}
    for fixture_id in fixtures:
        path = out_dir / f"{fixture_id}.pdf"
        fixtures[fixture_id].generate(path)
        assert path.read_bytes() == (PDFS_DIR / f"{fixture_id}.pdf").read_bytes(), (
            f"{fixture_id} is not deterministic; regenerate and recommit"
        )