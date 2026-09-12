"""Tests for deterministic reading-order reconstruction (Milestone 2.2).

The reconstruction consumes the Milestone 2.1 layout-aware representation
and re-orders each page's text blocks using bounding-box geometry, leaving
the M2.1 extraction order untouched as provenance.

All PDFs are generated on the fly with PyMuPDF and the layout dataclasses
are constructed directly where precise geometry is required; nothing here
requires internet access or external fixture files.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter.pdf import (
    PageLayout,
    LayoutBlock,
    LayoutPage,
    OrderedBlock,
    OrderedLayout,
    OrderedPage,
    COLUMN_OVERLAP_RATIO,
    ROW_OVERLAP_TOLERANCE_PT,
    ROW_TOP_TOLERANCE_PT,
    extract_page_layout,
    reconstruct_page_order,
    reconstruct_read_order,
)

# --------------------------------------------------------------------------- #
# Construction helpers
# --------------------------------------------------------------------------- #

PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def make_block(text: str, bbox: tuple[float, float, float, float], order: int) -> LayoutBlock:
    """A minimal text block with the given text, bbox, and source order."""
    return LayoutBlock(text=text, bbox=bbox, order=order, number=order)


def make_page(blocks: list[LayoutBlock], *, page_number: int = 1) -> LayoutPage:
    """A page whose ``blocks`` are ordered exactly as given (source order)."""
    return LayoutPage(
        page_number=page_number,
        page_width=PAGE_WIDTH,
        page_height=PAGE_HEIGHT,
        blocks=tuple(blocks),
    )


def reading_texts(page: LayoutPage) -> list[str]:
    """The page's block texts in reconstructed reading order."""
    return [block.text for block in reconstruct_page_order(page)]


def make_two_column_pdf(path) -> str:
    """A two-column PDF whose blocks are interleaved in extraction order.

    Both columns have the same left edge per column; the right column's
    baselines are offset below the left column's so PyMuPDF keeps every
    cell as its own block. The blocks therefore arrive at the layout layer
    interleaved (A1, B1, A2, B2, ...) even though a human reads them
    column-wise (A1, A2, A3, B1, B2, B3).
    """
    doc = pymupdf.open()
    pdf_page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    cells = [
        ("A1 left-row-one", (72, 100)),
        ("B1 right-row-one", (340, 120)),
        ("A2 left-row-two", (72, 140)),
        ("B2 right-row-two", (340, 160)),
        ("A3 left-row-three", (72, 180)),
        ("B3 right-row-three", (340, 200)),
    ]
    for text, (x, y) in cells:
        pdf_page.insert_text((x, y), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


# --------------------------------------------------------------------------- #
# 1. Simple vertically stacked blocks
# --------------------------------------------------------------------------- #


class TestVerticalStacking:
    def test_stacked_blocks_read_top_to_bottom(self) -> None:
        page = make_page(
            [
                make_block("Block A top", (72, 100, 400, 120), 0),
                make_block("Block B middle", (72, 160, 400, 180), 1),
                make_block("Block C bottom", (72, 220, 400, 240), 2),
            ]
        )
        assert reading_texts(page) == [
            "Block A top",
            "Block B middle",
            "Block C bottom",
        ]

    def test_stacked_blocks_are_reordered_in_reading_order(self) -> None:
        # The extraction order is deliberately scrambled: the block that is
        # lowest on the page was extracted first. Geometry, not extraction
        # order, must drive the result.
        page = make_page(
            [
                make_block("Block C bottom", (72, 220, 400, 240), 0),
                make_block("Block A top", (72, 100, 400, 120), 1),
                make_block("Block B middle", (72, 160, 400, 180), 2),
            ]
        )
        assert reading_texts(page) == [
            "Block A top",
            "Block B middle",
            "Block C bottom",
        ]


# --------------------------------------------------------------------------- #
# 2. Same-row horizontal blocks
# --------------------------------------------------------------------------- #


class TestSameRow:
    def test_left_block_before_right_block(self) -> None:
        # Two blocks on the same baseline, the right one extracted first.
        page = make_page(
            [
                make_block("Block B right", (330, 100, 430, 120), 0),
                make_block("Block A left", (72, 100, 200, 120), 1),
            ]
        )
        assert reading_texts(page) == ["Block A left", "Block B right"]

    def test_three_blocks_on_the_same_row_read_left_to_right(self) -> None:
        page = make_page(
            [
                make_block("center", (250, 100, 330, 120), 0),
                make_block("left", (72, 100, 180, 120), 1),
                make_block("right", (400, 100, 480, 120), 2),
            ]
        )
        assert reading_texts(page) == ["left", "center", "right"]

    def test_same_row_blocks_that_overlap_in_x(self) -> None:
        # Wide adjacent blocks share part of their x-range; they are one
        # column component, but the left-to-right row rule still applies.
        page = make_page(
            [
                make_block("wide right", (300, 100, 520, 120), 0),
                make_block("wide left", (72, 100, 330, 120), 1),
            ]
        )
        assert reading_texts(page) == ["wide left", "wide right"]


# --------------------------------------------------------------------------- #
# 3. Floating-point noise tolerance
# --------------------------------------------------------------------------- #


class TestFloatingPointNoise:
    def test_same_row_blocks_with_subpoint_y_noise(self) -> None:
        # Identical baselines with tiny y jitter are still the same row.
        page = make_page(
            [
                make_block("right noised", (330, 100.4, 430, 120.4), 0),
                make_block("left clean", (72, 100.0, 200, 120.0), 1),
                make_block("center noised", (250, 99.9, 330, 119.9), 2),
            ]
        )
        assert reading_texts(page) == ["left clean", "center noised", "right noised"]

    def test_stacked_blocks_with_fuzzy_y_boundaries(self) -> None:
        # The vertical gap between rows is much larger than the noise, so
        # rows stay separated regardless of the exact floating-point y.
        page = make_page(
            [
                make_block("Row B", (72, 200.3, 400, 220.3), 0),
                make_block("Row A", (72, 99.7, 400, 119.7), 1),
            ]
        )
        assert reading_texts(page) == ["Row A", "Row B"]

    def test_tolerance_constants_are_positive(self) -> None:
        # The centralized tolerances must remain meaningful.
        assert ROW_OVERLAP_TOLERANCE_PT > 0
        assert ROW_TOP_TOLERANCE_PT > 0
        assert 0 < COLUMN_OVERLAP_RATIO <= 1


# --------------------------------------------------------------------------- #
# 4. Two obvious columns
# --------------------------------------------------------------------------- #


class TestObviousColumns:
    def test_column_major_read_order_for_two_columns(self) -> None:
        # A1 | B1 / A2 | B2 / A3 | B3 -> A1 A2 A3 B1 B2 B3.
        page = make_page(
            [
                make_block("A1", (72, 100, 200, 120), 0),
                make_block("B1", (330, 100, 430, 120), 1),
                make_block("A2", (72, 150, 200, 170), 2),
                make_block("B2", (330, 150, 430, 170), 3),
                make_block("A3", (72, 200, 200, 220), 4),
                make_block("B3", (330, 200, 430, 220), 5),
            ]
        )
        assert reading_texts(page) == [
            "A1", "A2", "A3", "B1", "B2", "B3",
        ]

    def test_column_major_order_differs_from_extraction_order(self) -> None:
        # The same geometry in a scrambled extraction order still resolves
        # to the column-wise reading order, never to the extraction order.
        page = make_page(
            [
                make_block("A1", (72, 100, 200, 120), 0),
                make_block("B1", (330, 100, 430, 120), 1),
                make_block("A2", (72, 150, 200, 170), 2),
                make_block("B2", (330, 150, 430, 170), 3),
                make_block("A3", (72, 200, 200, 220), 4),
                make_block("B3", (330, 200, 430, 220), 5),
            ]
        )
        assert [b.order for b in page.blocks] == [0, 1, 2, 3, 4, 5]
        assert reading_texts(page) != [b.text for b in page.blocks]

    def test_two_columns_from_a_real_pdf(self, tmp_path) -> None:
        # The same contract end-to-end: interleaved text blocks extracted
        # from a synthetic two-column PDF are re-ordered column-wise.
        layout = extract_page_layout(make_two_column_pdf(tmp_path / "col.pdf"))
        assert len(layout.pages) == 1
        texts = reading_texts(layout.pages[0])
        assert [text.split()[0] for text in texts] == [
            "A1", "A2", "A3", "B1", "B2", "B3",
        ]

    def test_full_width_block_bridges_columns_conservatively(self) -> None:
        # A page-wide title overlaps both columns, so the columns are not
        # "obvious" to M2.2. The result is the documented conservative
        # fallback: top-to-bottom / left-to-right, never an error.
        page = make_page(
            [
                make_block("A1", (72, 120, 200, 140), 0),
                make_block("B1", (330, 120, 430, 140), 1),
                make_block("The Wide Title", (60, 50, 500, 70), 2),
                make_block("A2", (72, 180, 200, 200), 3),
            ]
        )
        assert reading_texts(page) == ["The Wide Title", "A1", "B1", "A2"]


# --------------------------------------------------------------------------- #
# 5. Multiple pages never reorder pages
# --------------------------------------------------------------------------- #


class TestMultiplePages:
    def test_pages_keep_their_order(self) -> None:
        first = make_page(
            [make_block("bottom-of-page-one", (72, 300, 400, 320), 0),
             make_block("top-of-page-one", (72, 100, 400, 120), 1)],
            page_number=1,
        )
        second = make_page(
            [make_block("top-of-page-two", (72, 100, 400, 120), 0),
             make_block("bottom-of-page-two", (72, 320, 400, 340), 1)],
            page_number=2,
        )
        layout = PageLayout(pages=(first, second))
        ordered = reconstruct_read_order(layout)
        assert isinstance(ordered, OrderedLayout)
        assert [p.page_number for p in layout.pages] == [1, 2]
        assert [p.page_number for p in ordered.pages] == [1, 2]
        assert [b.text for b in ordered.pages[0].blocks] == [
            "top-of-page-one", "bottom-of-page-one",
        ]
        assert [b.text for b in ordered.pages[1].blocks] == [
            "top-of-page-two", "bottom-of-page-two",
        ]

    def test_reconstruction_is_per_page(self) -> None:
        # Every page is re-ordered independently; no block ever moves
        # across a page boundary.
        page_one = make_page(
            [
                make_block("P1 low", (72, 300, 400, 320), 0),
                make_block("P1 mid", (72, 200, 400, 220), 1),
            ]
        )
        page_two = make_page(
            [
                make_block("P2 high", (72, 50, 400, 70), 0),
            ]
        )
        ordered = reconstruct_read_order(PageLayout(pages=(page_one, page_two)))
        assert [b.text for b in ordered.pages[0].blocks] == ["P1 mid", "P1 low"]
        assert [b.text for b in ordered.pages[1].blocks] == ["P2 high"]


# --------------------------------------------------------------------------- #
# 6. Original extraction order is preserved as provenance
# --------------------------------------------------------------------------- #


class TestSourceOrderPreserved:
    def test_source_layout_is_never_mutated(self) -> None:
        page = make_page(
            [
                make_block("B", (330, 100, 400, 120), 0),
                make_block("A", (72, 100, 200, 120), 1),
            ]
        )
        before = list(page.blocks)
        reconstruct_read_order(page)
        assert list(page.blocks) == before
        assert page.blocks[0].order == 0
        assert page.blocks[1].order == 1

    def test_ordered_blocks_carry_their_own_source_order(self) -> None:
        page = make_page(
            [
                make_block("A1", (72, 100, 200, 120), 0),
                make_block("B1", (330, 100, 430, 120), 1),
                make_block("A2", (72, 150, 200, 170), 2),
                make_block("B2", (330, 150, 430, 170), 3),
            ]
        )
        ordered = reconstruct_read_order(page)
        assert isinstance(ordered, OrderedPage)
        # Reading order is A1 A2 B1 B2; each keeps its own original index.
        assert [b.text for b in ordered.blocks] == ["A1", "A2", "B1", "B2"]
        assert [b.source_order for b in ordered.blocks] == [0, 2, 1, 3]
        # The reconstructed sequence is a permutation (no loss, no dupes).
        assert sorted(b.source_order for b in ordered.blocks) == [0, 1, 2, 3]
        # The M2.1 page is untouched and remains the source of truth.
        assert [b.order for b in page.blocks] == [0, 1, 2, 3]

    def test_source_order_lookup_is_identity_based(self) -> None:
        page = make_page(
            [
                make_block("left", (72, 100, 200, 120), 0),
                make_block("right", (330, 100, 430, 120), 1),
            ]
        )
        ordered = reconstruct_read_order(page)
        assert ordered.source_order(page.blocks[0]) == 0
        assert ordered.source_order(page.blocks[1]) == 1
        with pytest.raises(ValueError):
            ordered.source_order(make_block("foreign", (0, 0, 10, 10), 99))

    def test_ordered_block_exposes_geometry_and_provenance(self) -> None:
        page = make_page(
            [
                make_block("cell", (72, 100, 200, 120), 0),
            ]
        )
        ordered = reconstruct_read_order(page)
        assert isinstance(ordered.blocks[0], OrderedBlock)
        block = ordered.blocks[0]
        assert block.text == "cell"
        assert block.x0 == 72
        assert block.y0 == 100
        assert block.page_order == 0
        assert block.source_order == 0
        assert block.order == 0  # provenance alias


# --------------------------------------------------------------------------- #
# 7. Determinism
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_same_input_produces_same_order(self) -> None:
        page = make_page(
            [
                make_block("A1", (72, 100, 200, 120), 0),
                make_block("B1", (330, 100, 430, 120), 1),
                make_block("A2", (72, 150, 200, 170), 2),
                make_block("B2", (330, 150, 430, 170), 3),
                make_block("A3", (72, 200, 200, 220), 4),
                make_block("B3", (330, 200, 430, 220), 5),
            ]
        )
        first = reconstruct_read_order(page)
        second = reconstruct_read_order(page)
        assert first == second
        assert reading_texts(page) == reading_texts(page)

    def test_exact_geometry_ties_break_by_source_order(self) -> None:
        # Two blocks with identical geometry can only be disambiguated by
        # their preserved source order; the output must be stable.
        page = make_page(
            [
                make_block("drawn first", (72, 100, 400, 120), 0),
                make_block("drawn second", (72, 100, 400, 120), 1),
            ]
        )
        assert reading_texts(page) == ["drawn first", "drawn second"]
        assert reading_texts(page) == reading_texts(page)


# --------------------------------------------------------------------------- #
# 8 & 9. Empty page and single-block page
# --------------------------------------------------------------------------- #


class TestTrivialPages:
    def test_empty_page_returns_valid_empty_result(self) -> None:
        page = make_page([])
        assert reconstruct_page_order(page) == ()
        ordered = reconstruct_read_order(page)
        assert ordered.blocks == ()
        assert ordered.text == ""
        assert ordered.page_number == 1

    def test_single_block_page_is_unchanged(self) -> None:
        page = make_page([make_block("only block", (72, 100, 400, 120), 0)])
        assert reconstruct_page_order(page) == (page.blocks[0],)
        ordered = reconstruct_read_order(page)
        assert [b.text for b in ordered.blocks] == ["only block"]
        assert ordered.blocks[0].page_order == 0

    def test_image_only_page_is_handled(self, tmp_path) -> None:
        # Non-text blocks are never ordered; an image-only page resolves to
        # an empty text reconstruction without failing.
        doc = pymupdf.open()
        pdf_page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100))
        pdf_page.insert_image(pymupdf.Rect(70, 100, 400, 300), pixmap=pixmap)
        doc.save(str(tmp_path / "image_only.pdf"))
        doc.close()
        layout = extract_page_layout(tmp_path / "image_only.pdf")
        page_obj = layout.pages[0]
        assert page_obj.blocks == ()
        assert page_obj.image_block_count >= 1
        ordered = reconstruct_read_order(page_obj)
        assert ordered.blocks == ()
        assert ordered.image_block_count == page_obj.image_block_count


# --------------------------------------------------------------------------- #
# 10. Partial vertical overlap
# --------------------------------------------------------------------------- #


class TestPartialVerticalOverlap:
    def test_partial_overlap_does_not_merge_rows(self) -> None:
        # R2's top overlaps R1's bottom and R3's top overlaps R2's bottom
        # (a "staircase" band). Blocks with similar y coordinates must NOT
        # be flattened into one row; the reading order stays strict
        # top-to-bottom even though the extraction order is scrambled.
        page = make_page(
            [
                make_block("R2 middle", (120, 114, 400, 134), 0),
                make_block("R3 final", (72, 130, 400, 150), 1),
                make_block("R1 first", (72, 100, 400, 120), 2),
            ]
        )
        assert reading_texts(page) == ["R1 first", "R2 middle", "R3 final"]

    def test_tall_block_does_not_drag_lower_blocks(self) -> None:
        # A tall block (e.g. a drop-cap or an image caption float) partially
        # overlaps the block below it. The lower block must still follow it,
        # not join its row and get emitted before an in-between sibling.
        page = make_page(
            [
                make_block("tall block", (72, 100, 200, 320), 0),
                make_block("lower text", (250, 300, 500, 320), 1),
            ]
        )
        assert reading_texts(page) == ["tall block", "lower text"]


# --------------------------------------------------------------------------- #
# 11. Different block sizes
# --------------------------------------------------------------------------- #


class TestDifferentSizes:
    def test_mixed_heights_read_stably_top_to_bottom(self) -> None:
        page = make_page(
            [
                make_block("smallest fourth", (72, 340, 300, 355), 0),
                make_block("middling third", (72, 220, 500, 320), 1),
                make_block("title first", (72, 100, 500, 140), 2),
                make_block("small second", (72, 160, 300, 175), 3),
                make_block("small third", (72, 185, 300, 200), 4),
            ]
        )
        assert reading_texts(page) == [
            "title first", "small second", "small third",
            "middling third", "smallest fourth",
        ]

    def test_same_row_blocks_with_different_heights(self) -> None:
        # A short block and a taller block on the same baseline share the
        # row even though their heights differ.
        page = make_page(
            [
                make_block("tallish", (350, 105, 450, 118), 0),
                make_block("title", (72, 100, 500, 140), 1),
            ]
        )
        assert reading_texts(page) == ["title", "tallish"]


# --------------------------------------------------------------------------- #
# Contract shape
# --------------------------------------------------------------------------- #


class TestAPIShape:
    def test_reconstruct_read_order_returns_matching_types(self) -> None:
        page = make_page([make_block("one", (72, 100, 400, 120), 0)])
        ordered = reconstruct_read_order(page)
        assert isinstance(ordered, OrderedPage)
        layout = PageLayout(pages=(page,))
        ordered_layout = reconstruct_read_order(layout)
        assert isinstance(ordered_layout, OrderedLayout)
        assert isinstance(ordered_layout.pages[0], OrderedPage)
        assert ordered_layout.block_count == 1

    def test_ordered_page_proxies_page_geometry(self) -> None:
        page = make_page([make_block("one", (72, 100, 400, 120), 0)])
        ordered = reconstruct_read_order(page)
        assert ordered.page_width == PAGE_WIDTH
        assert ordered.page_height == PAGE_HEIGHT
        assert ordered.page is page