"""Public API regression tests (M2.8).

Explicitly tests the existing public APIs used by M1/M2 to ensure that future
changes cannot silently break them. Verifies:

* signatures remain compatible
* expected return types remain compatible
* existing exceptions remain compatible
* no accidental API removals

This module inspects the public API surface and asserts behavioural
contracts, not implementation details.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pymupdf
import pytest

import kindle_converter
from kindle_converter import convert_pdf_to_epub
from kindle_converter.document import (
    Book,
    BookMetadata,
    Chapter,
    Paragraph,
)
from kindle_converter.epub import (
    EPUBGenerationError,
    InvalidImageError,
    NoChaptersError,
    build_epub,
)
from kindle_converter.pdf import (
    # M1.2 analyzer
    EmptyPDFError,
    NoContentError,
    PDFAnalysis,
    PDFAnalysisError,
    PDFReadError,
    PDFType,
    analyze_pdf,
    # M1.3 extractor
    PDFExtractionError,
    MixedPDFError,
    ScannedPDFError,
    extract_book,
    # M2.1 layout
    LayoutBlock,
    LayoutPage,
    PageLayout,
    TextLine,
    TextSpan,
    FontFlags,
    extract_page_layout,
    # M2.2/M2.6 reading order
    COLUMN_MAX_COUNT,
    OrderedBlock,
    OrderedLayout,
    OrderedPage,
    reconstruct_page_order,
    reconstruct_read_order,
    # M2.3 paragraphs
    ParagraphLayout,
    ParagraphPage,
    ReconstructedParagraph,
    reconstruct_paragraphs,
    # M2.4 headings
    ClassifiedParagraph,
    DetectedHeading,
    HeadingLayout,
    HeadingPage,
    classify_paragraphs,
    detect_headings,
    # M2.5 header/footer
    HeaderFooterLayout,
    HeaderFooterPage,
    HeaderFooterType,
    ClassifiedHeaderFooterParagraph,
    DetectedHeaderFooter,
    classify_header_footer_paragraphs,
    detect_headers_footers,
    # M2.7 reconstruction
    GENERIC_HEADING_LEVEL,
    ElementKind,
    ReconstructedDocument,
    ReconstructedElement,
    ReconstructedPage,
    build_reconstructed_document,
    reconstruct_layout,
)


PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def _make_text_pdf(path: Path, pages: int = 3) -> Path:
    doc = pymupdf.open()
    doc.set_metadata({"title": "API Test Book"})
    for n in range(pages):
        y = 150 + n * 60
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_textbox(
            pymupdf.Rect(72, y, 500, 700),
            f"Body text for page {n + 1} here in the document.",
            fontname="helv",
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()
    return path

# --------------------------------------------------------------------------- #
# Top-level package API
# --------------------------------------------------------------------------- #


class TestTopLevelAPI:
    def test_version_is_string(self) -> None:
        assert isinstance(kindle_converter.__version__, str)

    def test_all_exports_convert_pdf_to_epub(self) -> None:
        assert "convert_pdf_to_epub" in kindle_converter.__all__

    def test_convert_pdf_to_epub_callable(self) -> None:
        assert callable(convert_pdf_to_epub)

    def test_convert_pdf_to_epub_accepts_path_and_output(
        self, tmp_path
    ) -> None:
        pdf = _make_text_pdf(tmp_path / "api.pdf", pages=3)
        out = tmp_path / "api.epub"
        convert_pdf_to_epub(pdf, out)
        assert out.exists()


# --------------------------------------------------------------------------- #
# M1.2 Analyzer API
# --------------------------------------------------------------------------- #


class TestAnalyzerAPI:
    def test_analyze_pdf_returns_pdf_analysis(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "analyzer.pdf")
        result = analyze_pdf(pdf)
        assert isinstance(result, PDFAnalysis)

    def test_analyze_pdf_accepts_open_document(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "analyzer_doc.pdf")
        doc = pymupdf.open(pdf)
        try:
            result = analyze_pdf(doc)
            assert isinstance(result, PDFAnalysis)
            assert not doc.is_closed
        finally:
            doc.close()

    def test_pdf_type_enum_values(self) -> None:
        assert PDFType.TEXT.value == "text"
        assert PDFType.SCANNED.value == "scanned"
        assert PDFType.MIXED.value == "mixed"

    def test_empty_pdf_raises_empty_pdf_error(self, tmp_path) -> None:
        path = tmp_path / "empty.pdf"
        path.write_bytes(
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
            b"trailer<</Root 1 0 R/Size 2>>%%EOF"
        )
        with pytest.raises(EmptyPDFError):
            analyze_pdf(path)

    def test_exception_hierarchy(self) -> None:
        assert issubclass(PDFReadError, PDFAnalysisError)
        assert issubclass(EmptyPDFError, PDFAnalysisError)
        assert issubclass(NoContentError, PDFAnalysisError)


# --------------------------------------------------------------------------- #
# M1.3 Extractor API
# --------------------------------------------------------------------------- #


class TestExtractorAPI:
    def test_extract_book_returns_book(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "extractor.pdf", pages=3)
        result = extract_book(pdf)
        assert isinstance(result, Book)

    def test_extract_book_metadata_populated(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "extractor_meta.pdf", pages=3)
        book = extract_book(pdf)
        assert isinstance(book.metadata, BookMetadata)

    def test_scanned_pdf_raises_scanned_pdf_error(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300))
        page.insert_image(pymupdf.Rect(50, 100, 545, 780), pixmap=pixmap)
        path = tmp_path / "scanned.pdf"
        doc.save(str(path))
        doc.close()

        with pytest.raises(ScannedPDFError):
            extract_book(path)

    def test_exception_hierarchy(self) -> None:
        assert issubclass(ScannedPDFError, PDFExtractionError)
        assert issubclass(MixedPDFError, PDFExtractionError)


# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# M2.1 Layout API
# --------------------------------------------------------------------------- #


class TestLayoutAPI:
    def test_extract_page_layout_returns_page_layout(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "layout.pdf")
        result = extract_page_layout(pdf)
        assert isinstance(result, PageLayout)

    def test_layout_page_contains_blocks(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "layout_blocks.pdf")
        layout = extract_page_layout(pdf)
        assert len(layout.pages) >= 1
        assert isinstance(layout.pages[0], LayoutPage)
        assert isinstance(layout.pages[0].blocks[0], LayoutBlock)


# --------------------------------------------------------------------------- #
# M2.2/M2.6 Reading order API
# --------------------------------------------------------------------------- #


class TestReadingOrderAPI:
    def test_reconstruct_read_order_page(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "ro.pdf")
        layout = extract_page_layout(pdf)
        ordered = reconstruct_read_order(layout)
        assert isinstance(ordered, OrderedLayout)
        assert isinstance(ordered.pages[0], OrderedPage)
        assert isinstance(ordered.pages[0].blocks[0], OrderedBlock)

    def test_column_max_count_constant(self) -> None:
        assert COLUMN_MAX_COUNT == 4


# --------------------------------------------------------------------------- #
# M2.3 Paragraph API
# --------------------------------------------------------------------------- #


class TestParagraphAPI:
    def test_reconstruct_paragraphs_returns_layout(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "para.pdf")
        layout = extract_page_layout(pdf)
        ordered = reconstruct_read_order(layout)
        result = reconstruct_paragraphs(ordered)
        assert isinstance(result, ParagraphLayout)
        assert isinstance(result.pages[0], ParagraphPage)
        assert isinstance(result.pages[0].paragraphs[0], ReconstructedParagraph)


# --------------------------------------------------------------------------- #
# M2.4 Heading API
# --------------------------------------------------------------------------- #


class TestHeadingAPI:
    def test_classify_paragraphs_returns_layout(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "heading.pdf")
        layout = extract_page_layout(pdf)
        ordered = reconstruct_read_order(layout)
        paragraphs = reconstruct_paragraphs(ordered)
        result = classify_paragraphs(paragraphs)
        assert isinstance(result, HeadingLayout)
        assert isinstance(result.pages[0], HeadingPage)
        assert isinstance(result.pages[0].paragraphs[0], ClassifiedParagraph)

    def test_detect_headings_is_wrapper(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "detect_heading.pdf")
        layout = extract_page_layout(pdf)
        ordered = reconstruct_read_order(layout)
        paragraphs = reconstruct_paragraphs(ordered)
        result = detect_headings(paragraphs)
        assert isinstance(result, HeadingLayout)


# --------------------------------------------------------------------------- #
# M2.5 Header/footer API
# --------------------------------------------------------------------------- #


class TestHeaderFooterAPI:
    def test_detect_headers_footers_returns_layout(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "hf.pdf")
        layout = extract_page_layout(pdf)
        ordered = reconstruct_read_order(layout)
        paragraphs = reconstruct_paragraphs(ordered)
        result = detect_headers_footers(paragraphs)
        assert isinstance(result, HeaderFooterLayout)
        assert isinstance(result.pages[0], HeaderFooterPage)

    def test_classify_header_footer_paragraphs(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "hf_classify.pdf")
        layout = extract_page_layout(pdf)
        ordered = reconstruct_read_order(layout)
        paragraphs = reconstruct_paragraphs(ordered)
        result, classified = classify_header_footer_paragraphs(paragraphs)
        assert isinstance(result, HeaderFooterLayout)
        assert isinstance(classified[0], ClassifiedHeaderFooterParagraph)

    def test_header_footer_type_enum(self) -> None:
        assert HeaderFooterType.HEADER.value == "header"
        assert HeaderFooterType.FOOTER.value == "footer"


# --------------------------------------------------------------------------- #
# M2.7 Reconstruction API
# --------------------------------------------------------------------------- #


class TestReconstructionAPI:
    def test_reconstruct_layout_returns_document(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "recon.pdf")
        layout = extract_page_layout(pdf)
        document, _, _, _ = reconstruct_layout(layout)
        assert isinstance(document, ReconstructedDocument)
        assert isinstance(document.pages[0], ReconstructedPage)
        assert isinstance(document.pages[0].elements[0], ReconstructedElement)

    def test_reconstruct_layout_rejects_non_page_layout(self) -> None:
        with pytest.raises(TypeError):
            reconstruct_layout("not a layout")  # type: ignore[arg-type]

    def test_reconstruct_layout_rejects_none(self) -> None:
        with pytest.raises(TypeError):
            reconstruct_layout(None)

    def test_element_kind_enum(self) -> None:
        assert ElementKind.HEADING.value == "heading"
        assert ElementKind.PARAGRAPH.value == "paragraph"

    def test_generic_heading_level_constant(self) -> None:
        assert GENERIC_HEADING_LEVEL == 1

    def test_build_reconstructed_document_empty_input(
        self, tmp_path: Path
    ) -> None:
        """build_reconstructed_document handles empty layout
        without crashing and produces a document with no pages."""
        from kindle_converter.pdf import (
            ParagraphLayout,
            HeadingLayout,
            HeaderFooterLayout,
        )
        from kindle_converter.pdf.reconstruction import (
            build_reconstructed_document,
            reconstructed_document_to_book,
        )
        from kindle_converter.document import BookMetadata

        layout = ParagraphLayout(pages=())
        headings = HeadingLayout(pages=())
        furniture = HeaderFooterLayout(pages=(), detected=())
        document = build_reconstructed_document(layout, headings, furniture)
        assert document.pages == ()
        assert document.paragraphs is layout
        assert document.headings is headings
        assert document.header_footer is furniture

        book = reconstructed_document_to_book(
            document, BookMetadata(title="Empty")
        )
        assert book.metadata.title == "Empty"
        assert book.chapters[0].blocks is not None
        assert len(book.chapters[0].blocks) == 0

# EPUB builder API
# --------------------------------------------------------------------------- #


class TestEpubAPI:
    def test_build_epub_accepts_book_and_path(self, tmp_path) -> None:
        book = Book(
            metadata=BookMetadata(title="EPUB API Test"),
            chapters=[Chapter(blocks=[Paragraph(text="Hello")])],
        )
        out = tmp_path / "epub_api.epub"
        build_epub(book, out)
        assert out.exists()

    def test_no_chapters_raises_no_chapters_error(self, tmp_path) -> None:
        with pytest.raises(NoChaptersError):
            build_epub(Book(), tmp_path / "empty.epub")

    def test_exception_hierarchy(self) -> None:
        assert issubclass(NoChaptersError, EPUBGenerationError)
        assert issubclass(InvalidImageError, EPUBGenerationError)

