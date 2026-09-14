"""Explicit determinism regression tests for the M2 pipeline (M2.8).

Verifies that every stage of the pipeline is deterministic: running the same
input multiple times produces identical output. This guards against accidental
introduction of non-determinism (e.g. relying on dict ordering, hash
randomization, or system time).

Each test runs the relevant function multiple times and asserts the results
are identical.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter.pdf import (
    LayoutBlock,
    LayoutPage,
    PageLayout,
    ParagraphLayout,
    ParagraphPage,
    ReconstructedParagraph,
    TextLine,
    TextSpan,
    FontFlags,
    analyze_pdf,
    classify_paragraphs,
    detect_headers_footers,
    extract_page_layout,
    reconstruct_layout,
    reconstruct_paragraphs,
    reconstruct_read_order,
)

PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def _make_span(text, font_size=12.0, font_name="Helvetica", flags=FontFlags(0)):
    return TextSpan(
        text=text,
        bbox=(0.0, 0.0, len(text) * font_size * 0.5, font_size),
        font_name=font_name,
        font_size=font_size,
        font_flags=flags,
    )


def _make_line(text, bbox, order=0):
    return TextLine(
        text=text, bbox=bbox, order=order, spans=(_make_span(text),)
    )


def _make_paragraph(text, page_number=1, y0=200.0):
    line = _make_line(text, (72, y0, 72 + len(text) * 6.0, y0 + 14), 0)
    block = LayoutBlock(
        text=text, bbox=line.bbox, order=0, lines=(line,)
    )
    return ReconstructedParagraph(
        text=text,
        page_number=page_number,
        source_lines=(line,),
        source_blocks=(block,),
        source_line_orders=((0, 0),),
        reading_order_indices=(0,),
    )


def _make_block(text, bbox, order):
    return LayoutBlock(text=text, bbox=bbox, order=order, number=order)


def _make_page(blocks, page_number=1):
    return LayoutPage(
        page_number=page_number,
        page_width=PAGE_WIDTH,
        page_height=PAGE_HEIGHT,
        blocks=tuple(blocks),
    )


def _make_paragraph_page(paragraphs, page_number=1):
    return ParagraphPage(
        page=LayoutPage(
            page_number=page_number,
            page_width=PAGE_WIDTH,
            page_height=PAGE_HEIGHT,
            blocks=tuple(
                LayoutBlock(
                    text=p.text,
                    bbox=(72, 100, 500, 300),
                    order=0,
                    lines=p.source_lines,
                )
                for p in paragraphs
            ),
        ),
        paragraphs=paragraphs,
    )


def _make_text_pdf(path, pages=3):
    doc = pymupdf.open()
    doc.set_metadata({"title": "Determinism Test"})
    for n in range(pages):
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_textbox(
            pymupdf.Rect(72, 120, 500, 760),
            f"Body text for page number {n + 1} here in the document.",
            fontname="helv",
            fontsize=12,
        )
        page.insert_text(
            (72, 810), "Determinism Test", fontname="helv", fontsize=9
        )
    doc.save(str(path))
    doc.close()
    return str(path)


class TestLayoutDeterminism:
    def test_layout_extraction_is_deterministic(self, tmp_path) -> None:
        path = _make_text_pdf(tmp_path / "layout_det.pdf")
        first = extract_page_layout(path)
        for _ in range(5):
            again = extract_page_layout(path)
            assert first == again


# --------------------------------------------------------------------------- #
# Reading order determinism
# --------------------------------------------------------------------------- #


class TestReadingOrderDeterminism:
    def test_reading_order_is_deterministic(self) -> None:
        page = _make_page(
            [
                _make_block("C", (72, 220, 400, 240), 0),
                _make_block("A", (72, 100, 400, 120), 1),
                _make_block("B", (72, 160, 400, 180), 2),
            ]
        )
        first = reconstruct_read_order(page)
        for _ in range(5):
            again = reconstruct_read_order(page)
            assert [
                b.text for b in first.blocks
            ] == [b.text for b in again.blocks]


# --------------------------------------------------------------------------- #
# Paragraph reconstruction determinism
# --------------------------------------------------------------------------- #


class TestParagraphDeterminism:
    def test_paragraph_reconstruction_is_deterministic(self) -> None:
        page = _make_page(
            [
                _make_block("P1 line one", (72, 100, 400, 116), 0),
                _make_block("P1 line two", (72, 116, 400, 132), 1),
            ]
        )
        ordered = reconstruct_read_order(page)
        first = reconstruct_paragraphs(ordered)
        for _ in range(5):
            again = reconstruct_paragraphs(ordered)
            assert [p.text for p in first.paragraphs] == [
                p.text for p in again.paragraphs
            ]


# --------------------------------------------------------------------------- #
# Heading classification determinism
# --------------------------------------------------------------------------- #


class TestHeadingDeterminism:
    def test_heading_classification_is_deterministic(self) -> None:
        para = _make_paragraph("Heading text", page_number=1, y0=100)
        page = _make_paragraph_page((para,), page_number=1)
        layout = ParagraphLayout(pages=(page,))
        first = classify_paragraphs(layout)
        for _ in range(5):
            again = classify_paragraphs(layout)
            assert [p.is_heading for p in first.pages[0].paragraphs] == [
                p.is_heading for p in again.pages[0].paragraphs
            ]


# --------------------------------------------------------------------------- #
# Header/footer classification determinism
# --------------------------------------------------------------------------- #


class TestHeaderFooterDeterminism:
    def test_header_footer_classification_is_deterministic(self) -> None:
        pages = tuple(
            _make_paragraph_page(
                (_make_paragraph("Repeated Header", page_number=n, y0=80),),
                page_number=n,
            )
            for n in range(1, 4)
        )
        layout = ParagraphLayout(pages=pages)
        first = detect_headers_footers(layout)
        for _ in range(5):
            again = detect_headers_footers(layout)
            assert [item.text for item in first.detected] == [
                item.text for item in again.detected
            ]


# --------------------------------------------------------------------------- #
# Full pipeline determinism
# --------------------------------------------------------------------------- #


class TestFullPipelineDeterminism:
    def test_full_pipeline_is_deterministic(self, tmp_path) -> None:
        path = _make_text_pdf(tmp_path / "pipeline_det.pdf")
        first = reconstruct_layout(extract_page_layout(path))[0]
        first_texts = [
            el.text for page in first.pages for el in page.elements
        ]
        for _ in range(5):
            again = reconstruct_layout(extract_page_layout(path))[0]
            again_texts = [
                el.text for page in again.pages for el in page.elements
            ]
            assert first_texts == again_texts


# --------------------------------------------------------------------------- #
# Analyzer determinism
# --------------------------------------------------------------------------- #


class TestAnalyzerDeterminism:
    def test_analyzer_is_deterministic(self, tmp_path) -> None:
        path = _make_text_pdf(tmp_path / "analyzer_det.pdf")
        first = analyze_pdf(path)
        for _ in range(5):
            again = analyze_pdf(path)
            assert first.document_type == again.document_type
            assert first.text_density == again.text_density
            assert first.page_count == again.page_count
