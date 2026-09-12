"""Tests for the deterministic first-pass PDF analyzer (Milestone 1.2).

All PDFs are generated on the fly with PyMuPDF; nothing here requires
internet access or external fixture files.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter.pdf import (
    EmptyPDFError,
    NoContentError,
    PDFAnalysis,
    PDFReadError,
    PDFType,
    PageAnalysis,
    analyze_pdf,
    build_analysis,
    classify,
)
from kindle_converter.pdf.analyzer import (
    MEANINGFUL_TEXT_CHAR_THRESHOLD,
    SCANNED_DOCUMENT_THRESHOLD,
    TEXT_DOCUMENT_THRESHOLD,
)

# --------------------------------------------------------------------------- #
# Generation helpers
# --------------------------------------------------------------------------- #

TEXT_LINE = (
    "It was a bright cold day in April, and the clocks "
    "were striking thirteen."
)

#: Number of non-whitespace characters in :data:`TEXT_LINE`.
TEXT_LINE_CHARS = sum(1 for ch in TEXT_LINE if not ch.isspace())

HEADER_ONLY_LINE = "The Complete Works of William Shakespeare Volume One"


def _new_page(doc: pymupdf.Document) -> pymupdf.Page:
    return doc.new_page(width=595, height=842)


def _insert_text_lines(page: pymupdf.Page, lines: list[str]) -> None:
    for offset, text in enumerate(lines):
        page.insert_text((72, 100 + offset * 45), text)


def _insert_image(page: pymupdf.Page) -> None:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300))
    page.insert_image(pymupdf.Rect(50, 100, 545, 780), pixmap=pixmap)


def _save(doc: pymupdf.Document, path) -> str:
    doc.save(str(path))
    doc.close()
    return str(path)


def make_text_pdf(path, pages: int = 5, lines: list[str] | None = None) -> str:
    """Create a PDF whose pages contain several long body-text lines."""
    doc = pymupdf.open()
    body = lines or [TEXT_LINE] * 6
    for _ in range(pages):
        _insert_text_lines(_new_page(doc), body)
    return _save(doc, path)


def make_scanned_pdf(path, pages: int = 5) -> str:
    """Create a PDF whose pages are full-page images with no text."""
    doc = pymupdf.open()
    for _ in range(pages):
        _insert_image(_new_page(doc))
    return _save(doc, path)


def make_mixed_pdf(path, text_pages: int, image_pages: int) -> str:
    """Create a PDF with the given number of text and image-only pages."""
    doc = pymupdf.open()
    for _ in range(text_pages):
        _insert_text_lines(_new_page(doc), [TEXT_LINE] * 6)
    for _ in range(image_pages):
        _insert_image(_new_page(doc))
    return _save(doc, path)


ZERO_PAGE_PDF = b"""\
%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [] /Count 0 >>
endobj
trailer
<< /Root 1 0 R /Size 2 >>
%%EOF
"""


# --------------------------------------------------------------------------- #
# Classification of whole documents
# --------------------------------------------------------------------------- #


class TestDocumentClassification:
    def test_text_pdf_is_classified_text(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "text.pdf")
        analysis = analyze_pdf(path)
        assert analysis.document_type is PDFType.TEXT

    def test_scanned_pdf_is_classified_scanned(self, tmp_path) -> None:
        path = make_scanned_pdf(tmp_path / "scanned.pdf")
        analysis = analyze_pdf(path)
        assert analysis.document_type is PDFType.SCANNED

    def test_mixed_pdf_is_classified_mixed(self, tmp_path) -> None:
        path = make_mixed_pdf(tmp_path / "mixed.pdf", text_pages=3, image_pages=2)
        analysis = analyze_pdf(path)
        assert analysis.document_type is PDFType.MIXED

    def test_boundary_of_text_versus_mixed(self, tmp_path) -> None:
        # Exactly TEXT_DOCUMENT_THRESHOLD (0.8) is still TEXT.
        path = make_mixed_pdf(tmp_path / "b.pdf", text_pages=8, image_pages=2)
        assert analyze_pdf(path).document_type is PDFType.TEXT

    def test_boundary_of_scanned_versus_mixed(self, tmp_path) -> None:
        # Exactly SCANNED_DOCUMENT_THRESHOLD (0.2) is still SCANNED.
        path = make_mixed_pdf(tmp_path / "b.pdf", text_pages=2, image_pages=8)
        assert analyze_pdf(path).document_type is PDFType.SCANNED

    def test_mixed_document_with_images_is_not_text(self, tmp_path) -> None:
        path = make_mixed_pdf(tmp_path / "m.pdf", text_pages=4, image_pages=6)
        analysis = analyze_pdf(path)
        assert analysis.document_type is PDFType.MIXED
        assert analysis.text_page_count == 4
        assert analysis.image_page_count == 6


class TestOnePagePDFs:
    def test_single_text_page_is_text(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "one.pdf", pages=1)
        analysis = analyze_pdf(path)
        assert analysis.page_count == 1
        assert analysis.document_type is PDFType.TEXT

    def test_single_image_page_is_scanned(self, tmp_path) -> None:
        path = make_scanned_pdf(tmp_path / "one.pdf", pages=1)
        analysis = analyze_pdf(path)
        assert analysis.page_count == 1
        assert analysis.document_type is PDFType.SCANNED


# --------------------------------------------------------------------------- #
# Counts and density
# --------------------------------------------------------------------------- #


class TestCounts:
    def test_page_count(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "d.pdf", pages=3)
        assert analyze_pdf(path).page_count == 3

    def test_text_page_count(self, tmp_path) -> None:
        path = make_mixed_pdf(tmp_path / "d.pdf", text_pages=4, image_pages=6)
        assert analyze_pdf(path).text_page_count == 4

    def test_image_page_count(self, tmp_path) -> None:
        path = make_mixed_pdf(tmp_path / "d.pdf", text_pages=4, image_pages=6)
        assert analyze_pdf(path).image_page_count == 6

    def test_per_page_rows_are_present_and_one_based(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "d.pdf", pages=3)
        analysis = analyze_pdf(path)
        assert len(analysis.pages) == 3
        assert [p.page_number for p in analysis.pages] == [1, 2, 3]

    def test_page_with_image_and_text_counts_for_both(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        _insert_text_lines(page, [TEXT_LINE] * 4)  # real body text...
        _insert_image(page)  # ...and a small image on the same page
        path = _save(doc, tmp_path / "illustrated.pdf")

        analysis = analyze_pdf(path)
        assert analysis.text_page_count == 1  # text wins for classification
        assert analysis.image_page_count == 1  # image is still reported


class TestTextDensity:
    def test_density_matches_longest_line_for_single_page(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "d.pdf", pages=1, lines=[TEXT_LINE])
        analysis = analyze_pdf(path)
        assert analysis.text_density == pytest.approx(TEXT_LINE_CHARS)
        assert analysis.pages[0].char_count == TEXT_LINE_CHARS

    def test_density_is_an_average_across_pages(self, tmp_path) -> None:
        # Two full-text pages (longest line == TEXT_LINE_CHARS) and one
        # image-only page (char_count 0): density == 2/3 * TEXT_LINE_CHARS.
        path = make_mixed_pdf(tmp_path / "d.pdf", text_pages=2, image_pages=1)
        analysis = analyze_pdf(path)
        assert analysis.text_density == pytest.approx(2 * TEXT_LINE_CHARS / 3)

    def test_scanned_pdf_has_zero_density(self, tmp_path) -> None:
        path = make_scanned_pdf(tmp_path / "d.pdf", pages=3)
        assert analyze_pdf(path).text_density == 0.0


# --------------------------------------------------------------------------- #
# Meaningful text heuristics
# --------------------------------------------------------------------------- #


class TestMeaningfulText:
    def test_pages_with_only_page_numbers_are_not_text(self, tmp_path) -> None:
        doc = pymupdf.open()
        for numeral in ("12", "iv", "300"):
            _insert_text_lines(_new_page(doc), [numeral])
        path = _save(doc, tmp_path / "page_numbers.pdf")

        analysis = analyze_pdf(path)
        assert analysis.page_count == 3
        assert analysis.text_page_count == 0
        assert all(not p.has_meaningful_text for p in analysis.pages)
        assert analysis.document_type is PDFType.SCANNED

    def test_pages_with_only_headers_are_not_text(self, tmp_path) -> None:
        # The header line is long enough to pass the character threshold on
        # its own; only the geometric band guard keeps it from counting.
        doc = pymupdf.open()
        for _ in range(3):
            _insert_text_lines(_new_page(doc), [HEADER_ONLY_LINE])
        path = _save(doc, tmp_path / "headers.pdf")

        analysis = analyze_pdf(path)
        assert len(HEADER_ONLY_LINE) >= MEANINGFUL_TEXT_CHAR_THRESHOLD
        assert analysis.text_page_count == 0
        assert analysis.document_type is PDFType.SCANNED

    def test_page_with_short_body_text_is_not_meaningful(self, tmp_path) -> None:
        short = "A terse aside."  # well below the 24-character threshold
        doc = pymupdf.open()
        _insert_text_lines(_new_page(doc), [short, "Another short line."])
        path = _save(doc, tmp_path / "short.pdf")

        analysis = analyze_pdf(path)
        assert analysis.text_page_count == 0
        assert analysis.text_density < MEANINGFUL_TEXT_CHAR_THRESHOLD

    def test_footer_alongside_body_text_does_not_hurt(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        _insert_text_lines(page, [TEXT_LINE] * 5)
        page.insert_text((72, 820), "— 42 —")  # footer below the body
        path = _save(doc, tmp_path / "body_and_footer.pdf")

        analysis = analyze_pdf(path)
        assert analysis.text_page_count == 1
        assert analysis.document_type is PDFType.TEXT


# --------------------------------------------------------------------------- #
# Raw text retention
# --------------------------------------------------------------------------- #


class TestRawTextRetention:
    def test_first_page_keeps_raw_text(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "d.pdf", pages=3)
        analysis = analyze_pdf(path)
        assert TEXT_LINE in analysis.pages[0].text

    def test_later_pages_do_not_keep_raw_text(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "d.pdf", pages=3)
        analysis = analyze_pdf(path)
        assert analysis.pages[1].text == ""
        assert analysis.pages[2].text == ""

    def test_build_analysis_keeps_first_row_text(self) -> None:
        row = PageAnalysis(
            page_number=1, has_image=False, has_meaningful_text=True,
            char_count=10, text="hello",
        )
        result = build_analysis([row])
        assert result.pages[0].text == "hello"


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


class TestEdgeCases:
    def test_empty_pdf_raises(self, tmp_path) -> None:
        path = tmp_path / "empty.pdf"
        path.write_bytes(ZERO_PAGE_PDF)
        with pytest.raises(EmptyPDFError):
            analyze_pdf(path)

    def test_pdf_with_only_blank_pages_raises_no_content_error(
        self, tmp_path
    ) -> None:
        doc = pymupdf.open()
        for _ in range(3):
            _new_page(doc)  # page exists but is completely blank
        path = _save(doc, tmp_path / "blank.pdf")
        with pytest.raises(NoContentError):
            analyze_pdf(path)

    def test_missing_file_raises_pdf_read_error(self, tmp_path) -> None:
        with pytest.raises(PDFReadError):
            analyze_pdf(tmp_path / "does_not_exist.pdf")

    def test_corrupt_file_raises_pdf_read_error(self, tmp_path) -> None:
        path = tmp_path / "corrupt.pdf"
        path.write_bytes(b"this is not a pdf at all")
        with pytest.raises(PDFReadError):
            analyze_pdf(path)

    def test_accepts_pathlib_path(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "pathlib.pdf", pages=1)
        analysis = analyze_pdf(tmp_path / "pathlib.pdf")
        assert analysis.page_count == 1

    def test_accepts_open_document_without_closing_it(self, tmp_path) -> None:
        path = make_scanned_pdf(tmp_path / "doc.pdf", pages=2)
        doc = pymupdf.open(path)
        try:
            analysis = analyze_pdf(doc)
            assert analysis.page_count == 2
            assert not doc.is_closed
        finally:
            doc.close()


# --------------------------------------------------------------------------- #
# classify() unit tests
# --------------------------------------------------------------------------- #


class TestClassify:
    @pytest.mark.parametrize(
        "fraction,expected",
        [
            (0.0, PDFType.SCANNED),
            (SCANNED_DOCUMENT_THRESHOLD, PDFType.SCANNED),           # 0.20
            (0.5, PDFType.MIXED),
            (0.79, PDFType.MIXED),
            (TEXT_DOCUMENT_THRESHOLD, PDFType.TEXT),                 # 0.80
            (1.0, PDFType.TEXT),
        ],
    )
    def test_classify_boundaries(self, fraction: float, expected: PDFType) -> None:
        assert classify(fraction) is expected

    @pytest.mark.parametrize("fraction", [-0.01, 1.01, -1.0, 2.0])
    def test_classify_rejects_out_of_range(self, fraction: float) -> None:
        with pytest.raises(ValueError):
            classify(fraction)

    def test_every_document_type_has_readable_value(self) -> None:
        assert PDFType.TEXT.value == "text"
        assert PDFType.SCANNED.value == "scanned"
        assert PDFType.MIXED.value == "mixed"


# --------------------------------------------------------------------------- #
# Return-shape sanity
# --------------------------------------------------------------------------- #


class TestResultShape:
    def test_analysis_is_a_pdf_analysis(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "d.pdf", pages=2)
        assert isinstance(analyze_pdf(path), PDFAnalysis)

    def test_analysis_is_deterministic(self, tmp_path) -> None:
        path = make_mixed_pdf(tmp_path / "d.pdf", text_pages=3, image_pages=2)
        first = analyze_pdf(path)
        second = analyze_pdf(path)
        assert first.document_type is second.document_type
        assert first.text_density == second.text_density
        assert first.text_page_count == second.text_page_count
        assert first.image_page_count == second.image_page_count
        assert first.pages == second.pages