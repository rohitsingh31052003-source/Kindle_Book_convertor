"""Tests for OCR-aware structural reconstruction (Milestone 3.6).

M3.6 consumes the M3.5 per-page routing results (:class:`PageProcessingResult`)
and integrates them into the M2 structural reconstruction:

* TEXT    pages -> native M2 reconstruction, byte-identical to M2
* SCANNED pages -> OCR-derived body paragraphs (no fabricated geometry)
* MIXED   pages -> native paragraphs first, then OCR paragraphs, never merged

OCR paragraphs carry ``TextSource.OCR`` and empty source-line/block
provenance; heading/header-footer detection never applies to OCR text.

These tests exercise buffer-level results directly (no PDF rendering) and
the pipeline entry point :func:`convert_pdf_to_book` with a fake OCR engine.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter import convert_pdf_to_book
from kindle_converter.document import Book, BookMetadata, PageBreak, Paragraph
from kindle_converter.pdf import (
    ElementKind,
    LayoutBlock,
    LayoutPage,
    PageLayout,
    PageProcessingResult,
    PDFType,
    ReconstructedParagraph,
    TextLine,
    TextSource,
    extract_book,
    processed_pages_to_book,
    reconstruct_layout,
    reconstruct_ocr_paragraphs,
    reconstruct_processed_pages,
)

PAGE_WIDTH = 595
PAGE_HEIGHT = 842


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _line(text: str, y0: float, order: int = 0) -> TextLine:
    """A minimal native text line (width approximating character count)."""
    return TextLine(
        text=text,
        bbox=(72.0, y0, 72.0 + len(text) * 5.0, y0 + 16.0),
        order=order,
    )


def _block(text: str, lines: tuple[TextLine, ...], order: int = 0) -> LayoutBlock:
    """A layout block wrapping ``lines``."""
    bbox = (
        min(line.x0 for line in lines),
        min(line.y0 for line in lines),
        max(line.x1 for line in lines),
        max(line.y1 for line in lines),
    )
    return LayoutBlock(text=text, bbox=bbox, order=order, lines=lines)


def _page(n: int, blocks: list[LayoutBlock]) -> LayoutPage:
    return LayoutPage(
        page_number=n,
        page_width=PAGE_WIDTH,
        page_height=PAGE_HEIGHT,
        blocks=tuple(blocks),
    )


def _native_text(blocks: list[LayoutBlock]) -> str:
    """The page text exactly as the M3.5 routing layer derives it."""
    return "\n".join(line.text for block in blocks for line in block.lines)


def _native_result(page_number: int, blocks: list[LayoutBlock]) -> PageProcessingResult:
    return PageProcessingResult(
        page_number=page_number,
        classification=PDFType.TEXT,
        native_text=_native_text(blocks),
    )


def _ocr_result(page_number: int, ocr_text: str) -> PageProcessingResult:
    return PageProcessingResult(
        page_number=page_number,
        classification=PDFType.SCANNED,
        ocr_text=ocr_text,
    )


def _element_snapshot(document) -> list[tuple[int, ElementKind, str]]:
    """(page_number, kind, text) triples in integration order."""
    return [
        (element.page_number, element.kind, element.text)
        for page in document.pages
        for element in page.elements
    ]


def _element_sources(document) -> list[tuple[int, TextSource, str]]:
    """(page_number, source, text) triples in integration order."""
    return [
        (element.paragraph.page_number, element.paragraph.source, element.text)
        for page in document.pages
        for element in page.elements
    ]


class _FakeEngine:
    """Fake OCREngine returning a predetermined text per OCR call."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.calls: list[pymupdf.Pixmap] = []

    def recognize(self, image: pymupdf.Pixmap) -> str:
        self.calls.append(image)
        index = min(len(self.calls) - 1, len(self.texts) - 1)
        return self.texts[index]


def _make_text_pdf(path, *, pages: int = 2) -> None:
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_textbox(
            pymupdf.Rect(72, 200, 500, 360),
            f"Native body text paragraph on page {n + 1} with enough words.",
            fontname="helv",
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()


def _make_scanned_pdf(path, *, pages: int = 2) -> None:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 300))
        page.insert_image(pymupdf.Rect(60, 60, 500, 760), pixmap=pixmap)
    doc.save(str(path))
    doc.close()


def _make_mixed_pdf(path) -> None:
    """Two pages: one MIXED (native text + large image), one SCANNED."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_textbox(
        pymupdf.Rect(60, 120, 500, 250),
        "Here is some reasonably lengthy native text that registers as "
        "meaningful content on this mixed page.",
        fontname="helv",
        fontsize=12,
    )
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 300))
    page.insert_image(pymupdf.Rect(30, 300, 560, 810), pixmap=pixmap)
    page2 = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page2.insert_image(pymupdf.Rect(60, 60, 500, 760), pixmap=pixmap)
    doc.save(str(path))
    doc.close()


# --------------------------------------------------------------------------- #
# Native regression: TEXT-only documents stay byte-identical to M2
# --------------------------------------------------------------------------- #


class TestNativeRegression:
    def test_text_only_equals_native_reconstruction(self) -> None:
        lines1 = [
            _line("This is the first physical line of", 100.0, 0),
            _line("the native paragraph on page one", 116.0, 1),
        ]
        lines2 = [
            _line("This is the native paragraph text", 200.0, 0),
            _line("which lives on page number two", 216.0, 1),
        ]
        layout = PageLayout(
            pages=(
                _page(1, [_block("p1", tuple(lines1), 0)]),
                _page(2, [_block("p2", tuple(lines2), 0)]),
            )
        )
        results = [
            _native_result(1, [_block("p1", tuple(lines1), 0)]),
            _native_result(2, [_block("p2", tuple(lines2), 0)]),
        ]

        native_doc, native_paragraphs, _, _ = reconstruct_layout(layout)
        combined = reconstruct_processed_pages(results, layout=layout)

        assert combined.page_count == native_doc.page_count == 2
        assert _element_snapshot(combined) == _element_snapshot(native_doc)
        assert combined.paragraphs == native_paragraphs
        assert combined.chapters is not None
        assert combined.chapters == native_doc.chapters

    def test_text_only_pages_keep_native_sources(self) -> None:
        lines = [
            _line("Native paragraph first line of text", 100.0, 0),
            _line("and its continuation line here", 116.0, 1),
        ]
        block = _block("p1", tuple(lines), 0)
        layout = PageLayout(pages=(_page(1, [block]),))
        document = reconstruct_processed_pages(
            [_native_result(1, [block])], layout=layout
        )

        assert _element_sources(document) == [
            (1, TextSource.NATIVE, "Native paragraph first line of text "
             "and its continuation line here")
        ]


# --------------------------------------------------------------------------- #
# OCR paragraph reconstruction inputs
# --------------------------------------------------------------------------- #


class TestOCRParsing:
    def test_ocr_only_page_builds_body_paragraphs(self) -> None:
        result = _ocr_result(
            1,
            "First paragraph line one\nline two of it\n"
            "\n"
            "Second paragraph here.",
        )
        document = reconstruct_processed_pages([result])
        assert document.page_count == 1
        assert _element_snapshot(document) == [
            (1, ElementKind.PARAGRAPH,
             "First paragraph line one line two of it"),
            (1, ElementKind.PARAGRAPH, "Second paragraph here."),
        ]
        first = document.pages[0].elements[0].paragraph
        assert first.source is TextSource.OCR
        assert first.source_lines == ()
        assert first.source_blocks == ()
        assert first.source_line_orders == ()
        assert first.reading_order_indices == ()
        assert first.line_count == 0
        assert first.ends_at_page_boundary is False

    def test_blank_line_runs_are_single_boundaries(self) -> None:
        text = "Alpha paragraph.\n\n\n\n\nBeta paragraph.\n\n\nGamma."
        paragraphs = reconstruct_ocr_paragraphs(text, page_number=2)
        assert [p.text for p in paragraphs] == [
            "Alpha paragraph.",
            "Beta paragraph.",
            "Gamma.",
        ]
        assert all(p.page_number == 2 for p in paragraphs)

    def test_consecutive_non_blank_lines_join_with_dehyphenation(self) -> None:
        text = "The word was per-\nhaps obvious.\n\nNormal text here too."
        paragraphs = reconstruct_ocr_paragraphs(text, page_number=1)
        assert [p.text for p in paragraphs] == [
            "The word was perhaps obvious.",
            "Normal text here too.",
        ]

    def test_ocr_paragraphs_are_never_headings(self) -> None:
        result = _ocr_result(
            1, "Chapter 1\n\nThe curious opening paragraph of the book body."
        )
        document = reconstruct_processed_pages([result])
        assert all(element.kind is ElementKind.PARAGRAPH for element in document.elements)
        assert document.chapters.chapter_count == 0
        assert all(element.paragraph.source is TextSource.OCR for element in document.elements)

    def test_empty_ocr_contributes_no_paragraphs(self) -> None:
        result = PageProcessingResult(
            page_number=1,
            classification=PDFType.SCANNED,
            ocr_text="",
        )
        document = reconstruct_processed_pages([result])
        assert document.page_count == 1
        assert document.element_count == 0
        assert document.pages[0].elements == ()


# --------------------------------------------------------------------------- #
# Mixed pages: native first, then OCR, never merged
# --------------------------------------------------------------------------- #


class TestMixedPages:
    def _mixed_setup(self):
        lines = [
            _line("Native mixed page paragraph first line", 100.0, 0),
            _line("and its native continuation second line", 116.0, 1),
        ]
        block = _block("p1", tuple(lines), 0)
        layout = PageLayout(pages=(_page(1, [block]), _page(2, [])))
        results = [
            PageProcessingResult(
                page_number=1,
                classification=PDFType.MIXED,
                native_text=_native_text([block]),
                ocr_text="Ocr paragraph one.\n\nOcr paragraph two.",
            ),
            _ocr_result(2, "Scanned page two ocr content only."),
        ]
        return block, layout, results

    def test_native_then_ocr_within_page(self) -> None:
        _, layout, results = self._mixed_setup()
        document = reconstruct_processed_pages(results, layout=layout)

        assert _element_sources(document) == [
            (1, TextSource.NATIVE,
             "Native mixed page paragraph first line and its native "
             "continuation second line"),
            (1, TextSource.OCR, "Ocr paragraph one."),
            (1, TextSource.OCR, "Ocr paragraph two."),
            (2, TextSource.OCR, "Scanned page two ocr content only."),
        ]

    def test_mixed_sources_never_merged_or_deduplicated(self) -> None:
        lines = [_line("Shared words appear on this page", 100.0, 0)]
        block = _block("p1", tuple(lines), 0)
        layout = PageLayout(pages=(_page(1, [block]),))
        result = PageProcessingResult(
            page_number=1,
            classification=PDFType.MIXED,
            native_text="Shared words appear on this page",
            ocr_text="Shared words appear on this page",
        )
        document = reconstruct_processed_pages([result], layout=layout)
        sources = _element_sources(document)
        assert sources == [
            (1, TextSource.NATIVE, "Shared words appear on this page"),
            (1, TextSource.OCR, "Shared words appear on this page"),
        ]

    def test_page_breaks_and_order_survive_to_book(self) -> None:
        _, layout, results = self._mixed_setup()
        book = processed_pages_to_book(
            results,
            BookMetadata(title="Mixed"),
            layout=layout,
        )
        blocks = book.chapters[0].blocks
        page_breaks = [b for b in blocks if isinstance(b, PageBreak)]
        assert len(page_breaks) == 1
        texts = [b.text for b in blocks if isinstance(b, Paragraph)]
        assert "Native mixed page paragraph first line" in texts[0]
        assert "Ocr paragraph one." in texts
        assert "Scanned page two ocr content only." in texts
        assert texts.index("Ocr paragraph one.") > texts.index(
            "Native mixed page paragraph first line and its native "
            "continuation second line"
        )


# --------------------------------------------------------------------------- #
# Validation / determinism
# --------------------------------------------------------------------------- #


class TestValidation:
    def test_empty_results_rejected(self) -> None:
        with pytest.raises(ValueError):
            reconstruct_processed_pages([])

    def test_missing_page_rejected(self) -> None:
        with pytest.raises(ValueError):
            reconstruct_processed_pages(
                [_ocr_result(1, "a"), _ocr_result(3, "c")]
            )

    def test_out_of_order_pages_rejected(self) -> None:
        with pytest.raises(ValueError):
            reconstruct_processed_pages(
                [_ocr_result(2, "b"), _ocr_result(1, "a")]
            )

    def test_non_processing_result_rejected(self) -> None:
        with pytest.raises(TypeError):
            reconstruct_processed_pages(["not a result"])  # type: ignore[list-item]

    def test_layout_type_checked(self) -> None:
        with pytest.raises(TypeError):
            reconstruct_processed_pages(
                [_ocr_result(1, "a")], layout="nope"  # type: ignore[arg-type]
            )

    def test_layout_page_count_must_match(self) -> None:
        layout = PageLayout(pages=(_page(1, []), _page(2, [])))
        with pytest.raises(ValueError):
            reconstruct_processed_pages([_ocr_result(1, "a")], layout=layout)


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        lines = [
            _line("Native line one of the page", 100.0, 0),
            _line("Native line two of the page", 116.0, 1),
        ]
        block = _block("p1", tuple(lines), 0)
        layout = PageLayout(pages=(_page(1, [block]), _page(2, [])))
        results = [
            PageProcessingResult(
                page_number=1,
                classification=PDFType.MIXED,
                native_text=_native_text([block]),
                ocr_text="Ocr a\n\nOcr b",
            ),
            _ocr_result(2, "Scanned text."),
        ]
        first = reconstruct_processed_pages(results, layout=layout)
        second = reconstruct_processed_pages(list(results), layout=layout)
        assert _element_sources(first) == _element_sources(second)
        assert first == second


# --------------------------------------------------------------------------- #
# Pipeline entry point (fake engine, no Tesseract)
# --------------------------------------------------------------------------- #


class TestPipeline:
    def test_text_pdf_matches_extract_book(self, tmp_path) -> None:
        pdf = tmp_path / "text.pdf"
        _make_text_pdf(pdf, pages=2)
        book = convert_pdf_to_book(pdf, engine=_FakeEngine(["unused"]))
        expected = extract_book(pdf)

        assert isinstance(book, Book)
        assert [b.text for b in book.chapters[0].blocks if isinstance(b, Paragraph)] == [
            b.text for b in expected.chapters[0].blocks if isinstance(b, Paragraph)
        ]

    def test_scanned_pdf_produces_ocr_body(self, tmp_path) -> None:
        pdf = tmp_path / "scanned.pdf"
        _make_scanned_pdf(pdf, pages=2)
        engine = _FakeEngine(
            ["First scanned paragraph.\n\nSecond scanned paragraph.", "Scanned page two only."]
        )
        book = convert_pdf_to_book(pdf, engine=engine)

        assert len(engine.calls) == 2
        texts = [b.text for b in book.chapters[0].blocks if isinstance(b, Paragraph)]
        assert texts == [
            "First scanned paragraph.",
            "Second scanned paragraph.",
            "Scanned page two only.",
        ]

    def test_mixed_pdf_keeps_native_and_ocr(self, tmp_path) -> None:
        pdf = tmp_path / "mixed.pdf"
        _make_mixed_pdf(pdf)
        engine = _FakeEngine(["Ocr of the mixed page.", "Ocr of the scanned page."])
        book = convert_pdf_to_book(pdf, engine=engine)

        assert len(engine.calls) == 2
        texts = [b.text for b in book.chapters[0].blocks if isinstance(b, Paragraph)]
        assert any("lengthy native text" in t for t in texts)
        assert "Ocr of the mixed page." in texts
        assert "Ocr of the scanned page." in texts
        native_index = next(
            i for i, t in enumerate(texts) if "lengthy native text" in t
        )
        ocr_index = texts.index("Ocr of the mixed page.")
        assert native_index < ocr_index

    def test_invalid_engine_rejected(self, tmp_path) -> None:
        pdf = tmp_path / "text.pdf"
        _make_text_pdf(pdf, pages=1)
        with pytest.raises(TypeError):
            convert_pdf_to_book(pdf, engine="not an engine")  # type: ignore[arg-type]