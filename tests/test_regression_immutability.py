"""Immutability and provenance regression tests (M2.8).

Verifies that the reconstruction pipeline never mutates upstream
representations and that provenance survives end-to-end.

For representative cases we capture the original state of the M2 objects
before reconstruction, run the pipeline, and then verify the originals are
unchanged. We also verify that provenance (source lines, source blocks,
source order, reading-order indices, page number) survives intact.
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


def _make_block(text, bbox, order, lines=None):
    if lines is None:
        lines = (_make_line(text, bbox, order),)
    return LayoutBlock(text=text, bbox=bbox, order=order, lines=lines)


def _make_page(blocks, page_number=1):
    return LayoutPage(
        page_number=page_number,
        page_width=PAGE_WIDTH,
        page_height=PAGE_HEIGHT,
        blocks=tuple(blocks),
    )


# --------------------------------------------------------------------------- #
# Layout immutability
# --------------------------------------------------------------------------- #


class TestLayoutImmutability:
    def test_reading_order_does_not_mutate_layout_page(self) -> None:
        """Running reading-order reconstruction must not mutate the source
        LayoutPage or its blocks."""
        block_a = _make_block("A", (72, 100, 400, 120), 0)
        block_b = _make_block("B", (72, 160, 400, 180), 1)
        page = _make_page([block_a, block_b])

        # Capture original state.
        original_blocks = page.blocks
        original_orders = [b.order for b in page.blocks]
        original_texts = [b.text for b in page.blocks]

        # Run reading-order reconstruction.
        reconstruct_read_order(page)

        # Verify source page is unchanged.
        assert page.blocks is original_blocks
        assert [b.order for b in page.blocks] == original_orders
        assert [b.text for b in page.blocks] == original_texts

    def test_paragraph_reconstruction_does_not_mutate_ordered_page(
        self,
    ) -> None:
        """Running paragraph reconstruction must not mutate the source
        OrderedPage or its blocks."""
        block_a = _make_block("A", (72, 100, 400, 120), 0)
        block_b = _make_block("B", (72, 116, 400, 132), 1)
        page = _make_page([block_a, block_b])
        ordered = reconstruct_read_order(page)

        # Capture original state.
        original_blocks = ordered.blocks
        original_texts = [b.text for b in ordered.blocks]

        # Run paragraph reconstruction.
        reconstruct_paragraphs(ordered)

        # Verify source is unchanged.
        assert ordered.blocks is original_blocks
        assert [b.text for b in ordered.blocks] == original_texts


# --------------------------------------------------------------------------- #
# Provenance survival
# --------------------------------------------------------------------------- #


class TestProvenanceSurvival:
    def test_source_order_survives_through_pipeline(self, tmp_path) -> None:
        """Source order (extraction order) must survive through the full
        pipeline and be available on OrderedBlock."""
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 100), "First extracted", fontname="helv", fontsize=12)
        page.insert_text((72, 160), "Second extracted", fontname="helv", fontsize=12)
        page.insert_text((72, 220), "Third extracted", fontname="helv", fontsize=12)
        path = str(tmp_path / "provenance.pdf")
        doc.save(path)
        doc.close()

        layout = extract_page_layout(path)
        ordered = reconstruct_read_order(layout)

        # Every ordered block must carry its source order.
        for ob in ordered.pages[0].blocks:
            assert isinstance(ob.source_order, int)

    def test_line_order_survives_in_paragraphs(self, tmp_path) -> None:
        """Source line orders must survive in reconstructed paragraphs."""
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 100), "Line alpha", fontname="helv", fontsize=12)
        page.insert_text((72, 116), "Line beta", fontname="helv", fontsize=12)
        page.insert_text((72, 200), "Line gamma", fontname="helv", fontsize=12)
        path = str(tmp_path / "line_provenance.pdf")
        doc.save(path)
        doc.close()

        layout = extract_page_layout(path)
        document, paragraph_layout, _, _ = reconstruct_layout(layout)

        # At least one paragraph should have source_line_orders.
        all_orders = []
        for p in paragraph_layout.pages[0].paragraphs:
            all_orders.extend(p.source_line_orders)
        assert len(all_orders) >= 1

    def test_page_number_survives_through_pipeline(self, tmp_path) -> None:
        """Page numbers must survive through the full pipeline."""
        doc = pymupdf.open()
        for _ in range(3):
            page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
            page.insert_text(
                (72, 200),
                "Body text on this page here.",
                fontname="helv",
                fontsize=12,
            )
        path = str(tmp_path / "page_provenance.pdf")
        doc.save(path)
        doc.close()

        layout = extract_page_layout(path)
        document, _, _, _ = reconstruct_layout(layout)

        for page_number, page in enumerate(document.pages, start=1):
            for element in page.elements:
                assert element.paragraph.page_number == page_number

    def test_source_lines_reference_original_objects(self, tmp_path) -> None:
        """ReconstructedParagraph.source_lines must reference the original
        TextLine objects, not copies."""
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text(
            (72, 200),
            "Original line text here.",
            fontname="helv",
            fontsize=12,
        )
        path = str(tmp_path / "source_lines.pdf")
        doc.save(path)
        doc.close()

        layout = extract_page_layout(path)
        document, paragraph_layout, _, _ = reconstruct_layout(layout)

        # Get the original lines from the layout.
        layout_lines = set()
        for block in layout.pages[0].blocks:
            for line in block.lines:
                layout_lines.add(id(line))

        # The paragraph's source_lines must reference the same objects.
        for para in paragraph_layout.pages[0].paragraphs:
            for line in para.source_lines:
                assert id(line) in layout_lines
