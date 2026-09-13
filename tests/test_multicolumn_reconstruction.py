"""Tests for multi-column reconstruction (Milestone 2.6, part 1)."""

from __future__ import annotations

from kindle_converter.pdf import (
    COLUMN_FULL_WIDTH_FRACTION,
    LayoutBlock,
    LayoutPage,
    PageLayout,
    reconstruct_page_order,
    reconstruct_read_order,
)

PAGE_WIDTH = 595
PAGE_HEIGHT = 842
LX0, LX1 = 72, 200
RX0, RX1 = 330, 430


def make_block(
    text: str, bbox: tuple[float, float, float, float], order: int
) -> LayoutBlock:
    return LayoutBlock(text=text, bbox=bbox, order=order, number=order)


def make_page(blocks: list[LayoutBlock], *, page_number: int = 1) -> LayoutPage:
    return LayoutPage(
        page_number=page_number,
        page_width=PAGE_WIDTH,
        page_height=PAGE_HEIGHT,
        blocks=tuple(blocks),
    )


def reading_texts(page: LayoutPage) -> list[str]:
    return [block.text for block in reconstruct_page_order(page)]


def col_block(
    text: str, x0: float, x1: float, y0: float, order: int
) -> LayoutBlock:
    return make_block(text, (x0, y0, x1, y0 + 20), order)


class TestTwoColumn:
    def test_two_column_basic_column_major(self) -> None:
        page = make_page(
            [
                col_block("L1", LX0, LX1, 100, 0),
                col_block("R1", RX0, RX1, 100, 1),
                col_block("L2", LX0, LX1, 150, 2),
                col_block("R2", RX0, RX1, 150, 3),
                col_block("L3", LX0, LX1, 200, 4),
                col_block("R3", RX0, RX1, 200, 5),
            ]
        )
        assert reading_texts(page) == ["L1", "L2", "L3", "R1", "R2", "R3"]

    def test_two_column_uneven_columns(self) -> None:
        page = make_page(
            [
                col_block("L1", LX0, LX1, 100, 0),
                col_block("R1", RX0, RX1, 100, 1),
                col_block("L2", LX0, LX1, 150, 2),
                col_block("R2", RX0, RX1, 150, 3),
                col_block("L3", LX0, LX1, 200, 4),
                col_block("L4", LX0, LX1, 250, 5),
            ]
        )
        assert reading_texts(page) == ["L1", "L2", "L3", "L4", "R1", "R2"]

    def test_two_column_unequal_widths(self) -> None:
        page = make_page(
            [
                col_block("L1", 50, 180, 100, 0),
                col_block("R1", 220, 520, 100, 1),
                col_block("L2", 50, 180, 150, 2),
                col_block("R2", 220, 520, 150, 3),
            ]
        )
        assert reading_texts(page) == ["L1", "L2", "R1", "R2"]

    def test_two_column_small_gap_and_offsets(self) -> None:
        page = make_page(
            [
                col_block("L1", 72, 200, 100, 0),
                col_block("R1", 220, 330, 104, 1),
                col_block("L2", 72, 200, 150, 2),
                col_block("R2", 220, 330, 156, 3),
            ]
        )
        assert reading_texts(page) == ["L1", "L2", "R1", "R2"]


class TestFullWidth:
    def test_heading_above_two_columns(self) -> None:
        # Genuinely full-width heading (spans the column region): it stays
        # outside every column and reads first.
        page = make_page(
            [
                col_block("L1", LX0, LX1, 140, 0),
                col_block("R1", RX0, RX1, 140, 1),
                make_block("Chapter 4", (60, 60, 440, 85), 2),
                col_block("L2", LX0, LX1, 190, 3),
                col_block("R2", RX0, RX1, 190, 4),
            ]
        )
        assert reading_texts(page) == [
            "Chapter 4", "L1", "L2", "R1", "R2",
        ]

    def test_full_width_note_after_columns(self) -> None:
        page = make_page(
            [
                col_block("L1", LX0, LX1, 100, 0),
                col_block("R1", RX0, RX1, 100, 1),
                col_block("L2", LX0, LX1, 150, 2),
                col_block("R2", RX0, RX1, 150, 3),
                make_block("Full-width note", (60, 210, 530, 235), 4),
            ]
        )
        assert reading_texts(page) == [
            "L1", "L2", "R1", "R2", "Full-width note",
        ]

    def test_full_width_block_between_columns(self) -> None:
        page = make_page(
            [
                col_block("L1", LX0, LX1, 100, 0),
                col_block("R1", RX0, RX1, 100, 1),
                make_block("Separator", (60, 155, 530, 180), 2),
                col_block("L2", LX0, LX1, 200, 3),
                col_block("R2", RX0, RX1, 200, 4),
            ]
        )
        assert reading_texts(page) == [
            "L1", "R1", "Separator", "L2", "R2",
        ]

    def test_bridge_block_does_not_collapse_columns(self) -> None:
        page = make_page(
            [
                col_block("L1", LX0, LX1, 100, 0),
                col_block("R1", RX0, RX1, 100, 1),
                col_block("L2", LX0, LX1, 160, 2),
                col_block("R2", RX0, RX1, 160, 3),
                make_block("Bridge", (60, 220, 530, 245), 4),
            ]
        )
        assert reading_texts(page) == ["L1", "L2", "R1", "R2", "Bridge"]


class TestSingleColumnSafety:
    def test_single_column_stack_unchanged(self) -> None:
        page = make_page(
            [
                make_block("P1", (72, 100, 500, 130), 0),
                make_block("P2", (72, 150, 500, 180), 1),
                make_block("P3", (72, 200, 500, 230), 2),
                make_block("P4", (72, 250, 500, 280), 3),
            ]
        )
        assert reading_texts(page) == ["P1", "P2", "P3", "P4"]

    def test_varying_paragraph_widths_stay_single_column(self) -> None:
        page = make_page(
            [
                make_block("Wide", (72, 100, 500, 120), 0),
                make_block("Narrow", (72, 150, 350, 170), 1),
                make_block("Medium", (72, 200, 430, 220), 2),
                make_block("Wide again", (72, 250, 510, 270), 3),
            ]
        )
        assert reading_texts(page) == ["Wide", "Narrow", "Medium", "Wide again"]

    def test_indented_paragraph_no_false_column(self) -> None:
        page = make_page(
            [
                make_block("P1", (72, 100, 500, 120), 0),
                make_block("Indented", (120, 150, 500, 170), 1),
                make_block("P3", (72, 200, 500, 220), 2),
                make_block("P4", (72, 250, 500, 270), 3),
            ]
        )
        assert reading_texts(page) == ["P1", "Indented", "P3", "P4"]

    def test_centered_heading_no_false_column(self) -> None:
        page = make_page(
            [
                make_block("Heading", (220, 60, 375, 85), 0),
                make_block("P1", (72, 110, 500, 130), 1),
                make_block("P2", (72, 150, 500, 170), 2),
                make_block("P3", (72, 190, 500, 210), 3),
            ]
        )
        assert reading_texts(page) == ["Heading", "P1", "P2", "P3"]

    def test_page_number_no_false_column(self) -> None:
        page = make_page(
            [
                make_block("P1", (72, 100, 500, 130), 0),
                make_block("P2", (72, 160, 500, 190), 1),
                make_block("P3", (72, 220, 500, 250), 2),
                make_block("12", (285, 780, 310, 800), 3),
            ]
        )
        assert reading_texts(page) == ["P1", "P2", "P3", "12"]

    def test_too_few_blocks_no_columns(self) -> None:
        page = make_page(
            [
                col_block("L", LX0, LX1, 100, 0),
                col_block("R", RX0, RX1, 100, 1),
            ]
        )
        assert reading_texts(page) == ["L", "R"]

    def test_overlapping_clusters_never_interleave_rows(self) -> None:
        # Wide clusters may share a small x-overlap tolerance, but blocks
        # from one x-region still read together (column-major safety).
        page = make_page(
            [
                make_block("A1", (72, 100, 330, 120), 0),
                make_block("B1", (320, 100, 520, 120), 1),
                make_block("A2", (72, 160, 330, 180), 2),
                make_block("B2", (320, 160, 520, 180), 3),
            ]
        )
        assert reading_texts(page) == ["A1", "A2", "B1", "B2"]


class TestCrossingBlocks:
    def test_slight_overlap_stays_in_stronger_column(self) -> None:
        # "Wide L2" slightly crosses the gutter but mostly sits in the left
        # column; it must not merge the columns or jump to the right one.
        page = make_page(
            [
                col_block("L1", LX0, LX1, 100, 0),
                col_block("R1", RX0, RX1, 100, 1),
                make_block("Wide L2", (LX0, 150, 250, 170), 2),
                col_block("R2", RX0, RX1, 150, 3),
            ]
        )
        assert reading_texts(page) == ["L1", "Wide L2", "R1", "R2"]

    def test_touching_columns_fall_back_to_legacy(self) -> None:
        # Wide touching clusters (no gutter) are not accepted as M2.6
        # columns, but the M2.2 legacy grouping already keeps x-distinct
        # clusters apart, so the order stays column-major either way.
        page = make_page(
            [
                make_block("L1", (72, 100, 300, 120), 0),
                make_block("R1", (300, 100, 530, 120), 1),
                make_block("L2", (72, 160, 300, 180), 2),
                make_block("R2", (300, 160, 530, 180), 3),
            ]
        )
        assert reading_texts(page) == ["L1", "L2", "R1", "R2"]


class TestThresholdsAndMixed:
    def test_gap_at_threshold_boundary(self) -> None:
        just_below = make_page(
            [
                make_block("L1", (72, 100, 200, 120), 0),
                make_block("R1", (203, 100, 330, 120), 1),
                make_block("L2", (72, 160, 200, 180), 2),
                make_block("R2", (203, 160, 330, 180), 3),
            ]
        )
        assert reading_texts(just_below) == ["L1", "L2", "R1", "R2"]
        at_threshold = make_page(
            [
                make_block("L1", (72, 100, 200, 120), 0),
                make_block("R1", (204, 100, 330, 120), 1),
                make_block("L2", (72, 160, 200, 180), 2),
                make_block("R2", (204, 160, 330, 180), 3),
            ]
        )
        assert reading_texts(at_threshold) == ["L1", "L2", "R1", "R2"]

    def test_full_width_fraction_boundary(self) -> None:
        frac = COLUMN_FULL_WIDTH_FRACTION
        just_below = make_page(
            [
                make_block("L1", (72, 140, 200, 160), 0),
                make_block("R1", (330, 140, 430, 160), 1),
                make_block("Body", (72, 60, 460, 85), 2),
                make_block("L2", (72, 190, 200, 210), 3),
                make_block("R2", (330, 190, 430, 210), 4),
            ]
        )
        assert just_below.blocks[2].width < frac * 470.0
        assert reading_texts(just_below)[0] == "Body"
        full = make_page(
            [
                make_block("L1", (72, 140, 200, 160), 0),
                make_block("R1", (330, 140, 430, 160), 1),
                make_block("Title", (60, 60, 530, 85), 2),
                make_block("L2", (72, 190, 200, 210), 3),
                make_block("R2", (330, 190, 430, 210), 4),
            ]
        )
        assert full.blocks[2].width >= frac * 470.0
        assert reading_texts(full)[0] == "Title"

    def test_mixed_single_and_multicolumn_pages(self) -> None:
        single = make_page(
            [
                make_block("S1", (72, 100, 500, 120), 0),
                make_block("S2", (72, 150, 500, 170), 1),
            ],
            page_number=1,
        )
        multi = make_page(
            [
                col_block("L1", LX0, LX1, 100, 0),
                col_block("R1", RX0, RX1, 100, 1),
                col_block("L2", LX0, LX1, 150, 2),
                col_block("R2", RX0, RX1, 150, 3),
            ],
            page_number=2,
        )
        layout = PageLayout(pages=(single, multi))
        ordered = reconstruct_read_order(layout)
        assert [b.text for b in ordered.pages[0].blocks] == ["S1", "S2"]
        assert [b.text for b in ordered.pages[1].blocks] == [
            "L1", "L2", "R1", "R2",
        ]

    def test_mixed_heading_columns_note_on_one_page(self) -> None:
        page = make_page(
            [
                make_block("Title", (60, 50, 530, 75), 0),
                col_block("L1", LX0, LX1, 100, 1),
                col_block("R1", RX0, RX1, 100, 2),
                col_block("L2", LX0, LX1, 150, 3),
                col_block("R2", RX0, RX1, 150, 4),
                make_block("Note", (60, 210, 530, 235), 5),
            ]
        )
        assert reading_texts(page) == [
            "Title", "L1", "L2", "R1", "R2", "Note",
        ]


class TestProvenanceDeterminismTrivial:
    def test_provenance_survives_multicolumn(self) -> None:
        page = make_page(
            [
                col_block("L1", LX0, LX1, 100, 0),
                col_block("R1", RX0, RX1, 100, 1),
                col_block("L2", LX0, LX1, 150, 2),
                col_block("R2", RX0, RX1, 150, 3),
            ]
        )
        ordered = reconstruct_read_order(page)
        assert [b.text for b in ordered.blocks] == ["L1", "L2", "R1", "R2"]
        assert [b.source_order for b in ordered.blocks] == [0, 2, 1, 3]
        assert [b.order for b in page.blocks] == [0, 1, 2, 3]

    def test_determinism_repeated_calls(self) -> None:
        page = make_page(
            [
                col_block("R2", RX0, RX1, 150, 0),
                col_block("L1", LX0, LX1, 100, 1),
                make_block("Bridge", (60, 210, 530, 235), 2),
                col_block("R1", RX0, RX1, 100, 3),
                col_block("L2", LX0, LX1, 150, 4),
            ]
        )
        first = reading_texts(page)
        for _ in range(5):
            assert reading_texts(page) == first
        assert reconstruct_read_order(page) == reconstruct_read_order(page)

    def test_empty_and_single_block(self) -> None:
        assert reconstruct_page_order(make_page([])) == ()
        solo = make_page([make_block("only", (72, 100, 400, 120), 0)])
        assert reading_texts(solo) == ["only"]


class TestThreeColumn:
    def test_three_column_major_order(self) -> None:
        page = make_page(
            [
                col_block("C1a", 50, 150, 100, 0),
                col_block("C2a", 220, 320, 100, 1),
                col_block("C3a", 390, 490, 100, 2),
                col_block("C1b", 50, 150, 160, 3),
                col_block("C2b", 220, 320, 160, 4),
                col_block("C3b", 390, 490, 160, 5),
            ]
        )
        assert reading_texts(page) == ["C1a", "C1b", "C2a", "C2b", "C3a", "C3b"]
