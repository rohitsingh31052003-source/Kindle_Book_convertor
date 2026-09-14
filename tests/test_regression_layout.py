"""Layout extraction regression tests (M2.8).

Fills genuine gaps in the M2.1 layout coverage:

* empty/sparse pages (a document mixing text pages and blank pages)
* deterministic extraction across repeated calls
* malformed/invalid geometry handling where already supported

These tests verify the layout extraction contract without duplicating the
existing test_pdf_layout.py coverage.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter.pdf import (
    LayoutBlock,
    LayoutPage,
    PageLayout,
    TextLine,
    TextSpan,
    extract_page_layout,
)

PAGE_WIDTH = 595
PAGE_HEIGHT = 842


# --------------------------------------------------------------------------- #
# Empty / sparse pages
# --------------------------------------------------------------------------- #


class TestEmptySparsePages:
    def test_blank_page_produces_empty_layout_page(self, tmp_path) -> None:
        """A document with a blank page followed by a text page must produce
        a layout with an empty block list for the blank page."""
        doc = pymupdf.open()
        # Page 1: blank (no text).
        doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        # Page 2: text.
        page2 = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page2.insert_text(
            (72, 200),
            "Text on the second page of the document.",
            fontname="helv",
            fontsize=12,
        )
        path = tmp_path / "sparse.pdf"
        doc.save(str(path))
        doc.close()

        layout = extract_page_layout(path)
        assert len(layout.pages) == 2
        # The blank page should have no text blocks.
        assert layout.pages[0].blocks == ()
        # The text page should have at least one block.
        assert len(layout.pages[1].blocks) >= 1

    def test_multiple_blank_pages_between_text(self, tmp_path) -> None:
        """Multiple blank pages between text pages must each produce empty
        block lists."""
        doc = pymupdf.open()
        # Text, blank, blank, text.
        p1 = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        p1.insert_text((72, 200), "First text page content.", fontname="helv", fontsize=12)
        doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        p4 = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        p4.insert_text((72, 200), "Last text page content.", fontname="helv", fontsize=12)
        path = tmp_path / "multi_blank.pdf"
        doc.save(str(path))
        doc.close()

        layout = extract_page_layout(path)
        assert len(layout.pages) == 4
        assert len(layout.pages[0].blocks) >= 1
        assert layout.pages[1].blocks == ()
        assert layout.pages[2].blocks == ()
        assert len(layout.pages[3].blocks) >= 1

    def test_text_page_block_count_matches_blocks(self, tmp_path) -> None:
        """The PageLayout.block_count must equal the sum of blocks across
        all pages."""
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 200), "Single block of text.", fontname="helv", fontsize=12)
        path = tmp_path / "single_block.pdf"
        doc.save(str(path))
        doc.close()

        layout = extract_page_layout(path)
        total = sum(len(p.blocks) for p in layout.pages)
        assert layout.block_count == total


# --------------------------------------------------------------------------- #
# Deterministic extraction
# --------------------------------------------------------------------------- #


class TestDeterministicExtraction:
    def test_repeated_extraction_produces_identical_layout(
        self, tmp_path
    ) -> None:
        """Extracting the same PDF twice must produce identical layout."""
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 200), "Deterministic text here.", fontname="helv", fontsize=12)
        page.insert_text((72, 300), "Another line of text.", fontname="helv", fontsize=12)
        path = tmp_path / "det.pdf"
        doc.save(str(path))
        doc.close()

        first = extract_page_layout(path)
        for _ in range(5):
            again = extract_page_layout(path)
            assert first == again

    def test_block_order_matches_extraction_order(self, tmp_path) -> None:
        """Block order must match PyMuPDF's 'dict' extraction order."""
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 200), "Block one text", fontname="helv", fontsize=12)
        page.insert_text((72, 300), "Block two text", fontname="helv", fontsize=12)
        path = tmp_path / "order.pdf"
        doc.save(str(path))
        doc.close()

        layout = extract_page_layout(path)
        assert [b.text for b in layout.pages[0].blocks] == [
            "Block one text",
            "Block two text",
        ]
