"""Application-level regression smoke tests (M6.2).

Exercises the real public application boundary
(:class:`~kindle_converter.application.ConversionApplication`) over two real
M6.1 corpus fixtures -- one pure-text, one scanned -- with the deterministic
:class:`CountingOCR` engine injected, so the full PDF -> Book -> EPUB ->
validate path runs without Tesseract and without mocking any converter stage.

These are deliberately thin: the deep per-fixture matrix lives in
``test_regression_corpus.py`` and the analysis/classification assertions in
``tests/test_corpus.py``. Here we only prove the application wiring keeps
working when fed genuine corpus documents.

Marked ``regression``: ``pytest -m regression`` selects these.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kindle_converter.application import (
    ConversionApplication,
    ConversionRequest,
    InvalidRequestError,
    OutputFormat,
)
from kindle_converter.pdf import PDFType

from tests.fixtures.corpus import document_path
from tests.regression.harness import CountingOCR, epub_pages, manifest_document

pytestmark = pytest.mark.regression


def _request(pdf: Path, output_directory: Path) -> ConversionRequest:
    return ConversionRequest(
        input_pdf=pdf,
        output_directory=output_directory,
        formats=frozenset({OutputFormat.EPUB}),
        validate=True,
    )


def test_convert_pure_text_fixture_end_to_end(tmp_path: Path) -> None:
    expected = manifest_document("novel_basic")["expectations"]
    app = ConversionApplication(engine=CountingOCR())

    result = app.convert(_request(document_path("novel_basic"), tmp_path))

    assert result.epub_path == tmp_path / "novel_basic.epub"
    assert result.epub_path.is_file()
    assert result.page_count == expected["page_count"]
    assert result.document_type is PDFType.TEXT
    assert result.azw3_path is None
    assert result.validation is not None and result.validation.valid

    pages = epub_pages(result.epub_path)
    body = " ".join(line for page in pages for line in page)
    assert "Chapter 1: The Harbor Lights" in body


def test_convert_scanned_fixture_routes_ocr_through_injected_engine(
    tmp_path: Path,
) -> None:
    engine = CountingOCR()
    app = ConversionApplication(engine=engine)

    result = app.convert(_request(document_path("scanned_book"), tmp_path))

    assert result.epub_path.is_file()
    assert result.validation is not None and result.validation.valid
    assert engine.calls == 5  # one recognize() per scanned page

    pages = epub_pages(result.epub_path)
    body = " ".join(line for page in pages for line in page)
    assert "OCR-TEXT-FOR-CALL-1" in body
    assert "OCR-TEXT-FOR-CALL-5" in body


def test_convert_missing_input_is_rejected(tmp_path: Path) -> None:
    app = ConversionApplication(engine=CountingOCR())
    missing = tmp_path / "does-not-exist.pdf"

    with pytest.raises(InvalidRequestError):
        app.convert(_request(missing, tmp_path))


def test_analyze_pure_text_fixture_through_application() -> None:
    expected = manifest_document("novel_basic")["expectations"]
    app = ConversionApplication(engine=CountingOCR())

    analysis = app.analyze_pdf(document_path("novel_basic"))

    assert analysis.page_count == expected["page_count"]
    assert analysis.document_type is PDFType.TEXT