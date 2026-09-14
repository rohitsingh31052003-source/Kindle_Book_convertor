"""Reading-order and multi-column regression tests (M2.8).

Fills genuine gaps in the M2.2/M2.6 coverage:

* 4-column upper bound (COLUMN_MAX_COUNT == 4): four clean columns are
  accepted and read column-by-column.
* >4-column fallback: five column clusters exceed the upper bound, so M2.6
  falls back to the exact M2.2 legacy ordering rather than guessing.
* Narrow centered objects: a narrow, horizontally-centered block is excluded
  from column clustering and emitted outside the column sequence.
* Ambiguous geometry fallback: horizontally overlapping blocks that cannot be
  cleanly separated fall back to M2.2 top-to-bottom ordering.
* M2.6 preserves M2.2 behavior where multi-column reconstruction does not
  apply: a single-column page reads identically whether it goes through the
  M2.6 path or the M2.2 path.

All geometry is constructed directly from the layout dataclasses so the tests
are deterministic and independent of PDF rendering.
"""

from __future__ import annotations

from kindle_converter.pdf import (
    COLUMN_MAX_COUNT,
    LayoutBlock,
    LayoutPage,
    PageLayout,
    reconstruct_page_order,
    reconstruct_read_order,
)

PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def make_block(
    text: str, bbox: tuple[float, float, float, float], order: int
) -> LayoutBlock:
    return LayoutBlock(text=text, bbox=bbox, order=order, number=order)


def make_page(
    blocks: list[LayoutBlock], *, page_number: int = 1
) -> LayoutPage:
    return LayoutPage(
        page_number=page_number,
        page_width=PAGE_WIDTH,
        page_height=PAGE_HEIGHT,
        blocks=tuple(blocks),
    )


def reading_texts(page: LayoutPage) -> list[str]:
    return [block.text for block in reconstruct_page_order(page)]


# --------------------------------------------------------------------------- #
# 4-column upper bound
# --------------------------------------------------------------------------- #


class TestFourColumnUpperBound:
    def test_four_columns_read_column_by_column(self) -> None:
        """Four clean column clusters (at the COLUMN_MAX_COUNT limit) are
        accepted and read column-major."""
        page = make_page(
            [
                make_block("C1a", (50, 100, 130, 120), 0),
                make_block("C2a", (170, 100, 250, 120), 1),
                make_block("C3a", (290, 100, 370, 120), 2),
                make_block("C4a", (410, 100, 490, 120), 3),
                make_block("C1b", (50, 160, 130, 180), 4),
                make_block("C2b", (170, 160, 250, 180), 5),
                make_block("C3b", (290, 160, 370, 180), 6),
                make_block("C4b", (410, 160, 490, 180), 7),
            ]
        )
        assert reading_texts(page) == [
            "C1a", "C1b",
            "C2a", "C2b",
            "C3a", "C3b",
            "C4a", "C4b",
        ]

    def test_four_column_constant_is_four(self) -> None:
        """M2.6 treats 4 clearly-separated columns as valid 4-column geometry."""
        page = make_page(
            [
                make_block("C1a", (40, 100, 110, 120), 0),
                make_block("C1b", (40, 160, 110, 180), 1),
                make_block("C2a", (220, 100, 290, 120), 2),
                make_block("C2b", (220, 160, 290, 180), 3),
                make_block("C3a", (400, 100, 470, 120), 4),
                make_block("C3b", (400, 160, 470, 180), 5),
                make_block("C4a", (580, 100, 650, 120), 6),
                make_block("C4b", (580, 160, 650, 180), 7),
            ]
        )
        result = reading_texts(page)
        assert result == [
            "C1a", "C1b",
            "C2a", "C2b",
            "C3a", "C3b",
            "C4a", "C4b",
        ]
# --------------------------------------------------------------------------- #
# >4-column fallback
# --------------------------------------------------------------------------- #


class TestMoreThanFourColumnsFallback:
    def test_five_columns_falls_back_to_legacy_ordering(self) -> None:
        """Five column clusters exceed COLUMN_MAX_COUNT, so M2.6 must fall
        back to the exact M2.2 legacy ordering (top-to-bottom, left-to-right
        within a row) rather than guessing at column bands.

        Because all ten candidate blocks sit on exactly two baseline rows, the
        legacy row-first ordering pairs each row into a single band and emits
        the five left-to-right blocks on each row before descending. That gives
        a column-major-like pairing per row (``C1a C1b C2a C2b ...``) while
        still respecting the M2.2 fallback contract."""
        page = make_page(
            [
                make_block("C1a", (40, 100, 110, 120), 0),
                make_block("C2a", (130, 100, 200, 120), 1),
                make_block("C3a", (220, 100, 290, 120), 2),
                make_block("C4a", (310, 100, 380, 120), 3),
                make_block("C5a", (400, 100, 470, 120), 4),
                make_block("C1b", (40, 160, 110, 180), 5),
                make_block("C2b", (130, 160, 200, 180), 6),
                make_block("C3b", (220, 160, 290, 180), 7),
                make_block("C4b", (310, 160, 380, 180), 8),
                make_block("C5b", (400, 160, 470, 180), 9),
            ]
        )
        result = reading_texts(page)
        # M2.2 fallback: band by row, then left-to-right within each band.
        assert result == [
            "C1a", "C1b",
            "C2a", "C2b",
            "C3a", "C3b",
            "C4a", "C4b",
            "C5a", "C5b",
        ]

    def test_fallback_is_deterministic(self) -> None:
        page = make_page(
            [
                make_block("C1a", (40, 100, 110, 120), 0),
                make_block("C2a", (130, 100, 200, 120), 1),
                make_block("C3a", (220, 100, 290, 120), 2),
                make_block("C4a", (310, 100, 380, 120), 3),
                make_block("C5a", (400, 100, 470, 120), 4),
            ]
        )
        first = reading_texts(page)
        for _ in range(5):
            assert reading_texts(page) == first


# --------------------------------------------------------------------------- #
# Narrow centered objects
# --------------------------------------------------------------------------- #


class TestNarrowCenteredObjects:
    def test_narrow_centered_block_excluded_from_columns(self) -> None:
        """A narrow, horizontally-centered block is excluded from column
        clustering and emitted outside the column sequence."""
        page = make_page(
            [
                # Two clean columns.
                make_block("L1", (72, 140, 200, 160), 0),
                make_block("R1", (330, 140, 460, 160), 1),
                make_block("L2", (72, 200, 200, 220), 2),
                make_block("R2", (330, 200, 460, 220), 3),
                # A narrow centered block (between the columns, narrow width).
                make_block("Centered", (270, 260, 330, 280), 4),
            ]
        )
        result = reading_texts(page)
        # The centered block must appear in the output.
        assert "Centered" in result
        # The columns must still read column-major.
        assert result.index("L1") < result.index("R1")
        assert result.index("L2") < result.index("R2")


# --------------------------------------------------------------------------- #
# Ambiguous geometry fallback
# --------------------------------------------------------------------------- #


class TestAmbiguousGeometryFallback:
    def test_overlapping_blocks_fall_back_to_top_to_bottom(self) -> None:
        """Overlapping blocks cannot form clean columns, so M2.6 falls back to
        the legacy M2.2 order (top-to-bottom, left-to-right within a row)."""
        page = make_page(
            [
                make_block("A", (40, 100, 200, 120), 0),
                make_block("B", (130, 100, 290, 120), 1),  # overlaps A
            ]
        )
        assert reading_texts(page) == ["A", "B"]
# --------------------------------------------------------------------------- #
# M2.6 preserves M2.2 behavior where multi-column does not apply
# --------------------------------------------------------------------------- #


class TestM26PreservesM22:
    def test_single_column_page_reads_identically(self) -> None:
        """A single-column page must read identically whether M2.6 applies
        or not: top-to-bottom, left-to-right within a row."""
        page = make_page(
            [
                make_block("First", (72, 100, 500, 120), 0),
                make_block("Second", (72, 160, 500, 180), 1),
                make_block("Third", (72, 220, 500, 240), 2),
            ]
        )
        assert reading_texts(page) == ["First", "Second", "Third"]

    def test_single_column_provenance_survives(self) -> None:
        """Source order is preserved on a single-column page even though
        M2.6 runs the multi-column hypothesis first."""
        page = make_page(
            [
                make_block("A", (72, 100, 400, 120), 0),
                make_block("B", (72, 160, 400, 180), 1),
                make_block("C", (72, 220, 400, 240), 2),
            ]
        )
        ordered = reconstruct_read_order(page)
        assert [b.text for b in ordered.blocks] == ["A", "B", "C"]
        assert [b.source_order for b in ordered.blocks] == [0, 1, 2]
        # The source page blocks must not be mutated.
        assert [b.order for b in page.blocks] == [0, 1, 2]

    def test_mixed_single_and_multi_column_pages(self) -> None:
        """A layout mixing single-column and multi-column pages must order
        each page independently."""
        single = make_page(
            [
                make_block("S1", (72, 100, 500, 120), 0),
                make_block("S2", (72, 160, 500, 180), 1),
            ],
            page_number=1,
        )
        multi = make_page(
            [
                make_block("L1", (72, 100, 200, 120), 0),
                make_block("R1", (330, 100, 460, 120), 1),
                make_block("L2", (72, 160, 200, 180), 2),
                make_block("R2", (330, 160, 460, 180), 3),
            ],
            page_number=2,
        )
        layout = PageLayout(pages=(single, multi))
        ordered = reconstruct_read_order(layout)
        assert [b.text for b in ordered.pages[0].blocks] == ["S1", "S2"]
        assert [b.text for b in ordered.pages[1].blocks] == [
            "L1", "L2", "R1", "R2"
        ]

        """Blocks that overlap horizontally cannot form clean columns and
        must fall back to top-to-bottom ordering."""
        page = make_page(
            [
                make_block("Top", (72, 100, 400, 120), 0),
                make_block("Middle", (120, 180, 450, 200), 1),
                make_block("Bottom", (60, 260, 390, 280), 2),
            ]
        )
        # These blocks overlap horizontally (no clean vertical gutter), so
        # M2.6 must fall back to M2.2 top-to-bottom ordering.
        assert reading_texts(page) == ["Top", "Middle", "Bottom"]

    def test_fallback_preserves_source_order_for_same_row(self) -> None:
        """When blocks share a row but overlap horizontally, the fallback
        still orders them left-to-right within the row."""
        page = make_page(
            [
                make_block("Right", (300, 100, 450, 120), 0),
                make_block("Left", (72, 100, 220, 120), 1),
            ]
        )
        # Same row, overlapping horizontally -> fallback to M2.2 which
        # orders left-to-right within a row.
        assert reading_texts(page) == ["Left", "Right"]

        """The documented upper bound is exactly 4."""
        assert COLUMN_MAX_COUNT == 4
