"""Tests for the layout-aware PDF text representation (Milestone 2.1).

The representation preserves per-block/page geometry, font metadata, and
extraction ordering for the later reconstruction stages (M2.2 onward)
without performing any reconstruction itself.

Coordinate convention under test: top-left origin, x grows right, y grows
down, units are PDF points, bounding boxes are inclusive (``width ==
x1 - x0``, ``height == y1 - y0``). Page numbers are 1-based physical PDF
page indices.

All PDFs are generated on the fly with PyMuPDF; nothing here requires
internet access or external fixture files.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter.pdf import (
    EmptyPDFError,
    LayoutBlock,
    LayoutPage,
    PageLayout,
    PDFReadError,
    TextLine,
    TextSpan,
    extract_page_layout,
)
from kindle_converter.pdf.layout import FontFlags, decode_font_flags, valid_bbox

# --------------------------------------------------------------------------- #
# Generation helpers
# --------------------------------------------------------------------------- #

PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def _new_page(doc: pymupdf.Document) -> pymupdf.Page:
    return doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)


def make_simple_pdf(path, *, pages: int = 1, base_y: float = 100.0) -> str:
    """A text PDF whose pages each carry body text at a fixed position."""
    doc = pymupdf.open()
    for index in range(pages):
        page = _new_page(doc)
        page.insert_text(
            (72.0, base_y),
            f"Unique body line number {index + 1} of the document",
            fontname="helv",
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()
    return str(path)


def make_block_pdf(path) -> str:
    """A single-line PDF with a bold title, a body paragraph, and a footer.

    Produces three visually distinct text blocks on one page: a Helvetica
    title, a bold body line, and a 9 pt footer.
    """
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(
        (72.0, 120.0),
        "A Bold Short Title",
        fontname="hebo",
        fontsize=14,
    )
    page.insert_text(
        (72.0, 260.0),
        "The quick brown fox jumps over the lazy dog near the riverbank.",
        fontname="helv",
        fontsize=12,
    )
    page.insert_text(
        (72.0, 820.0),
        "Page footer text",
        fontname="helv",
        fontsize=9,
    )
    doc.save(str(path))
    doc.close()
    return str(path)


def make_multi_span_pdf(path) -> str:
    """A single line split into two adjacent spans at the same baseline.

    The two inserts sit flush against each other (the second starts exactly
    where the first ends), which makes PyMuPDF group them into one line
    with two spans carrying different fonts.
    """
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text((72.0, 200.0), "Left part of the line", fontname="helv", fontsize=12)
    blocks = page.get_text("dict")["blocks"]
    left_edge = next(
        span["bbox"][2]
        for block in blocks
        if block.get("type") == 0
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if span.get("text", "").startswith("Left")
    )
    page.insert_text((left_edge, 200.0), "Right part of the line", fontname="hebo", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


def make_image_pdf(path) -> str:
    """A page with text above and below a small image."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text((72.0, 200.0), "Text above the image", fontname="helv", fontsize=12)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100))
    page.insert_image(pymupdf.Rect(70.0, 300.0, 400.0, 500.0), pixmap=pixmap)
    page.insert_text((72.0, 600.0), "Text below the image", fontname="helv", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


def make_scanned_pdf(path, *, pages: int = 2) -> str:
    """A PDF whose pages are full-page images with no text."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = _new_page(doc)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300))
        page.insert_image(pymupdf.Rect(50, 100, 545, 780), pixmap=pixmap)
    doc.save(str(path))
    doc.close()
    return str(path)


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
# Basic representation and geometry
# --------------------------------------------------------------------------- #


class TestBasicRepresentation:
    def test_simple_pdf_produces_layout_blocks(self, tmp_path) -> None:
        path = make_simple_pdf(tmp_path / "simple.pdf")
        layout = extract_page_layout(path)
        assert isinstance(layout, PageLayout)
        assert len(layout.pages) == 1
        page = layout.pages[0]
        assert isinstance(page, LayoutPage)
        assert len(page.blocks) >= 1
        assert all(isinstance(b, LayoutBlock) for b in page.blocks)
        for block in page.blocks:
            for line in block.lines:
                assert isinstance(line, TextLine)
                for span in line.spans:
                    assert isinstance(span, TextSpan)

    def test_block_has_typed_text_and_geometry(self, tmp_path) -> None:
        path = make_simple_pdf(tmp_path / "simple.pdf")
        block = extract_page_layout(path).pages[0].blocks[0]
        assert isinstance(block.text, str)
        assert all(isinstance(v, float) for v in block.bbox)
        assert valid_bbox(block.bbox)
        assert block.x0 == block.bbox[0]
        assert block.y0 == block.bbox[1]
        assert block.x1 == block.bbox[2]
        assert block.y1 == block.bbox[3]

    def test_page_dimensions_match_the_document(self, tmp_path) -> None:
        path = make_simple_pdf(tmp_path / "dims.pdf")
        page = extract_page_layout(path).pages[0]
        assert page.page_width == PAGE_WIDTH
        assert page.page_height == PAGE_HEIGHT


class TestGeometryConsistency:
    def test_bbox_width_height_follow_x1_minus_x0_y1_minus_y0(
        self, tmp_path
    ) -> None:
        path = make_block_pdf(tmp_path / "geo.pdf")
        layout = extract_page_layout(path)
        for page in layout.pages:
            for block in page.blocks:
                assert valid_bbox(block.bbox)
                assert block.width == pytest.approx(block.x1 - block.x0)
                assert block.height == pytest.approx(block.y1 - block.y0)
                for line in block.lines:
                    assert valid_bbox(line.bbox)
                    assert line.width == pytest.approx(line.x1 - line.x0)
                    assert line.height == pytest.approx(line.y1 - line.y0)
                    for span in line.spans:
                        assert valid_bbox(span.bbox)
                        assert span.width == pytest.approx(span.x1 - span.x0)
                        assert span.height == pytest.approx(span.y1 - span.y0)

    def test_y_axis_grows_downwards(self, tmp_path) -> None:
        # The lower block on the page must have a larger y0 (top edge).
        path = make_block_pdf(tmp_path / "yaxis.pdf")
        page = extract_page_layout(path).pages[0]
        blocks = sorted(page.blocks, key=lambda b: b.y0)
        assert len(blocks) >= 3
        # Title is highest (smallest y0), footer lowest (largest y0).
        assert blocks[0].y0 < blocks[1].y0 < blocks[2].y0

    def test_geometry_lies_within_the_page(self, tmp_path) -> None:
        path = make_block_pdf(tmp_path / "inset.pdf")
        page = extract_page_layout(path).pages[0]
        for block in page.blocks:
            assert block.x0 >= 0
            assert block.y0 >= 0
            assert block.x1 <= page.page_width
            assert block.y1 <= page.page_height


# --------------------------------------------------------------------------- #
# Content and page association
# --------------------------------------------------------------------------- #


class TestContent:
    def test_text_content_is_preserved(self, tmp_path) -> None:
        path = make_simple_pdf(tmp_path / "text.pdf")
        page = extract_page_layout(path).pages[0]
        assert page.text  # blocks carried the page's text
        assert "Unique body line number 1 of the document" in page.text

    def test_span_text_joins_into_line_and_block(self, tmp_path) -> None:
        path = make_multi_span_pdf(tmp_path / "spans.pdf")
        page = extract_page_layout(path).pages[0]
        block = page.blocks[0]
        lines = block.lines
        assert len(lines) >= 1
        for line in lines:
            assert line.text == "".join(span.text for span in line.spans)
        assert block.text == "\n".join(line.text for line in block.lines)

    def test_blocks_preserve_extraction_order(self, tmp_path) -> None:
        path = make_block_pdf(tmp_path / "order.pdf")
        page = extract_page_layout(path).pages[0]
        orders = [block.order for block in page.blocks]
        assert sorted(orders) == orders
        assert orders == list(range(len(orders)))


class TestPages:
    def test_multiple_pages_retain_page_association(self, tmp_path) -> None:
        path = make_simple_pdf(tmp_path / "multi.pdf", pages=3)
        layout = extract_page_layout(path)
        assert len(layout.pages) == 3
        page_numbers = [p.page_number for p in layout.pages]
        assert page_numbers == [1, 2, 3]
        for page in layout.pages:
            assert "Unique body line number" in page.text
            # Each page's text mentions its own physical page number.
            marker = f"line number {page.page_number} of the document"
            assert marker in page.text

    def test_document_totals_across_pages(self, tmp_path) -> None:
        path = make_simple_pdf(tmp_path / "multi.pdf", pages=3)
        layout = extract_page_layout(path)
        blocks_expected = sum(len(page.blocks) for page in layout.pages)
        assert layout.block_count == blocks_expected
        assert layout.image_block_count == 0
        assert layout.block_count is not None

    def test_image_pages_are_represented_without_failure(self, tmp_path) -> None:
        path = make_scanned_pdf(tmp_path / "scanned.pdf", pages=2)
        layout = extract_page_layout(path)
        assert len(layout.pages) == 2
        for page in layout.pages:
            assert page.blocks == ()
            assert page.image_block_count >= 1
            assert page.text == ""


# --------------------------------------------------------------------------- #
# Font and style metadata
# --------------------------------------------------------------------------- #


class TestFontMetadata:
    def test_font_name_and_size_preserved(self, tmp_path) -> None:
        path = make_block_pdf(tmp_path / "fonts.pdf")
        page = extract_page_layout(path).pages[0]
        assert len(page.blocks) >= 3

        title = page.blocks[0]
        assert title.lines
        span = title.lines[0].spans[0]
        assert span.font_name == "Helvetica-Bold"
        assert span.font_size == pytest.approx(14.0)

        footer = page.blocks[-1]
        footer_span = footer.lines[0].spans[0]
        assert footer_span.font_name == "Helvetica"
        assert footer_span.font_size == pytest.approx(9.0)

    def test_font_flags_decode_from_raw_flags(self, tmp_path) -> None:
        path = make_block_pdf(tmp_path / "flags.pdf")
        page = extract_page_layout(path).pages[0]

        title = page.blocks[0]
        title_span = title.lines[0].spans[0]
        assert title_span.raw_flags is not None
        assert FontFlags.BOLD in title_span.font_flags

        body = page.blocks[1]
        body_span = body.lines[0].spans[0]
        assert body_span.raw_flags is not None
        assert FontFlags.BOLD not in body_span.font_flags

    def test_span_color_preserved_when_provided(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        page.insert_text((72.0, 200.0), "Colored text line here", color=(1, 0, 0))
        doc.save(str(tmp_path / "color.pdf"))
        doc.close()

        layout = extract_page_layout(tmp_path / "color.pdf")
        span = layout.pages[0].blocks[0].lines[0].spans[0]
        assert span.color is not None

    def test_multiple_spans_keep_own_font_metadata(self, tmp_path) -> None:
        path = make_multi_span_pdf(tmp_path / "span_meta.pdf")
        page = extract_page_layout(path).pages[0]
        block = page.blocks[0]
        spans = [span for line in block.lines for span in line.spans]
        font_names = {span.font_name for span in spans}
        assert font_names == {"Helvetica", "Helvetica-Bold"}
        # Each span keeps its own size, and both report the 12 pt value.
        assert len(spans) == 2
        assert all(span.font_size == pytest.approx(12.0) for span in spans)


# --------------------------------------------------------------------------- #
# Line metadata
# --------------------------------------------------------------------------- #


class TestLineMetadata:
    def test_line_direction_and_writing_mode(self, tmp_path) -> None:
        path = make_simple_pdf(tmp_path / "dirlines.pdf")
        page = extract_page_layout(path).pages[0]
        for line in (l for b in page.blocks for l in b.lines):
            assert line.direction == (1.0, 0.0)
            assert line.writing_mode == 0

    def test_line_order_is_dense_within_a_block(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        # 16 pt baseline spacing keeps both lines in one PyMuPDF block.
        page.insert_text((72.0, 200.0), "first line of the block", fontname="helv", fontsize=12)
        page.insert_text((72.0, 216.0), "second line of the block", fontname="helv", fontsize=12)
        doc.save(str(tmp_path / "two_lines.pdf"))
        doc.close()

        page = extract_page_layout(tmp_path / "two_lines.pdf").pages[0]
        first_block = page.blocks[0]
        assert len(first_block.lines) == 2
        assert [line.order for line in first_block.lines] == [0, 1]


# --------------------------------------------------------------------------- #
# Robustness and edge cases
# --------------------------------------------------------------------------- #


class TestRobustness:
    def test_missing_file_raises_pdf_read_error(self, tmp_path) -> None:
        with pytest.raises(PDFReadError):
            extract_page_layout(tmp_path / "missing.pdf")

    def test_corrupt_file_raises_pdf_read_error(self, tmp_path) -> None:
        path = tmp_path / "corrupt.pdf"
        path.write_bytes(b"this is definitely not a pdf")
        with pytest.raises(PDFReadError):
            extract_page_layout(path)

    def test_zero_page_pdf_raises_empty_error(self, tmp_path) -> None:
        path = tmp_path / "empty.pdf"
        path.write_bytes(ZERO_PAGE_PDF)
        with pytest.raises(EmptyPDFError):
            extract_page_layout(path)

    def test_accepts_open_document_without_closing_it(self, tmp_path) -> None:
        doc = pymupdf.open(make_simple_pdf(tmp_path / "open.pdf", pages=2))
        try:
            layout = extract_page_layout(doc)
            assert len(layout.pages) == 2
            assert not doc.is_closed
        finally:
            doc.close()

    def test_repeated_extraction_is_deterministic(self, tmp_path) -> None:
        path = make_block_pdf(tmp_path / "deterministic.pdf")
        first = extract_page_layout(path)
        second = extract_page_layout(path)
        assert first == second


# --------------------------------------------------------------------------- #
# decode_font_flags / valid_bbox unit behavior
# --------------------------------------------------------------------------- #


class TestHelpers:
    def test_decode_font_flags_folds_legacy_bit(self) -> None:
        # 1 << 3 (the reserved monospace legacy bit) always joins MONOSPACED.
        assert FontFlags.MONOSPACED in decode_font_flags(1 << 3)

    def test_decode_font_flags_returns_known_bits(self) -> None:
        assert decode_font_flags(2) == FontFlags.ITALIC
        assert decode_font_flags(16) == FontFlags.BOLD
        assert decode_font_flags(20) == FontFlags.BOLD | FontFlags.SERIFED

    def test_decode_font_flags_tolerates_unknown_values(self) -> None:
        assert decode_font_flags(None) == FontFlags(0)
        assert decode_font_flags(-1) == FontFlags(0)
        assert decode_font_flags(True) == FontFlags(0)  # bool is not a flag int

    def test_valid_bbox_asserts_convention(self) -> None:
        assert valid_bbox((0.0, 0.0, 10.0, 20.0)) is True
        assert valid_bbox((0.0, 20.0, 10.0, 0.0)) is False  # y1 < y0
        assert valid_bbox((10.0, 0.0, 0.0, 20.0)) is False  # x1 < x0
        assert valid_bbox((0.0, 0.0, 10.0, 20.0, 0.0)) is False  # wrong arity