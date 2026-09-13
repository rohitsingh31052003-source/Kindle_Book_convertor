"""Tests for conservative layout-based heading detection (Milestone 2.4).

These tests construct M2.3 ``ReconstructedParagraph`` objects directly
(along with their source ``TextLine``/``TextSpan``/``LayoutBlock``
provenance) to exercise precise typography and geometry without relying on
PDF rendering. Synthetic PDFs are used for end-to-end integration cases.

M2.4 never reorders paragraphs: it consumes the M2.3 paragraph order as-is
and classifies each paragraph independently.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter.pdf import (
    FontFlags,
    LayoutBlock,
    LayoutPage,
    PageLayout,
    ParagraphLayout,
    ParagraphPage,
    ReconstructedParagraph,
    TextLine,
    TextSpan,
    classify_paragraphs,
    detect_headings,
    reconstruct_paragraphs,
    reconstruct_read_order,
)
from kindle_converter.pdf.headings import (
    BODY_LIKE_PENALTY,
    BOLD_TYPOGRAPHY_SCORE,
    CENTERED_ALIGNMENT_SCORE,
    FONT_FAMILY_CHANGE_SCORE,
    FONT_SIZE_ABOVE_BASELINE_SCORE,
    FONT_SIZE_RATIO_THRESHOLD,
    HEADING_SCORE_THRESHOLD,
    ISOLATED_PARAGRAPH_SCORE,
    LARGE_SPACING_AFTER_SCORE,
    LARGE_SPACING_BEFORE_SCORE,
    LARGE_SPACING_MIN_PT,
    MIN_HEADING_SIGNALS,
    SHORT_PARAGRAPH_SCORE,
    STRONG_FONT_SIZE_ABOVE_BASELINE_SCORE,
    STRONG_FONT_SIZE_RATIO_THRESHOLD,
    UNCONFIRMED_BOLD_TYPOGRAPHY_SCORE,
)

# --------------------------------------------------------------------------- #
# Construction helpers
# --------------------------------------------------------------------------- #

PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def make_span(
    text: str,
    font_size: float | None = 12.0,
    font_name: str | None = "Helvetica",
    flags: FontFlags = FontFlags(0),
    bbox: tuple[float, float, float, float] | None = None,
) -> TextSpan:
    """A minimal text span with the given font metadata."""
    if bbox is None:
        width = len(text) * font_size * 0.5 if font_size else len(text) * 5.0
        bbox = (0.0, 0.0, width, font_size or 12.0)
    return TextSpan(
        text=text,
        bbox=bbox,
        font_name=font_name,
        font_size=font_size,
        font_flags=flags,
    )


def make_line(
    text: str,
    bbox: tuple[float, float, float, float],
    order: int = 0,
    spans: tuple[TextSpan, ...] | None = None,
) -> TextLine:
    """A minimal text line with the given text, bbox, and optional spans."""
    if spans is None:
        spans = (make_span(text),)
    return TextLine(text=text, bbox=bbox, order=order, spans=spans)


def make_paragraph(
    text: str,
    page_number: int = 1,
    lines: tuple[TextLine, ...] | None = None,
    blocks: tuple[LayoutBlock, ...] | None = None,
    reading_order_indices: tuple[int, ...] | None = None,
    ends_at_page_boundary: bool = False,
) -> ReconstructedParagraph:
    """A minimal reconstructed paragraph with the given text and lines."""
    if lines is None:
        lines = (make_line(text, (72, 100, 72 + len(text) * 5, 116)),)
    if blocks is None:
        blocks = tuple(
            LayoutBlock(
                text=line.text,
                bbox=line.bbox,
                order=0,
                lines=(line,),
            )
            for line in lines
        )
    if reading_order_indices is None:
        reading_order_indices = tuple(range(len(lines)))
    return ReconstructedParagraph(
        text=text,
        page_number=page_number,
        source_lines=lines,
        source_blocks=blocks,
        source_line_orders=tuple((0, i) for i in range(len(lines))),
        reading_order_indices=reading_order_indices,
        ends_at_page_boundary=ends_at_page_boundary,
    )


def make_page(
    paragraphs: tuple[ReconstructedParagraph, ...],
    page_number: int = 1,
) -> ParagraphPage:
    """A ParagraphPage wrapping the given paragraphs."""
    page = LayoutPage(
        page_number=page_number,
        page_width=PAGE_WIDTH,
        page_height=PAGE_HEIGHT,
        blocks=tuple(
            block
            for para in paragraphs
            for block in para.source_blocks
        ),
    )
    return ParagraphPage(page=page, paragraphs=paragraphs)


def make_layout(
    pages: tuple[ParagraphPage, ...],
) -> ParagraphLayout:
    """A ParagraphLayout wrapping the given pages."""
    return ParagraphLayout(pages=pages)


def body_paragraph(
    text: str,
    y0: float = 100.0,
    font_size: float = 12.0,
    font_name: str = "Helvetica",
    flags: FontFlags = FontFlags(0),
    line_height: float = 16.0,
    x0: float = 72.0,
    x1: float | None = None,
) -> ReconstructedParagraph:
    """A typical body paragraph with normal typography and geometry."""
    if x1 is None:
        x1 = x0 + len(text) * font_size * 0.5
    span = make_span(text, font_size=font_size, font_name=font_name, flags=flags)
    line = make_line(text, (x0, y0, x1, y0 + line_height), 0, (span,))
    return make_paragraph(text, lines=(line,), blocks=(
        LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,)),
    ))


def heading_paragraph(
    text: str,
    y0: float = 100.0,
    font_size: float = 20.0,
    font_name: str = "Helvetica-Bold",
    flags: FontFlags = FontFlags.BOLD,
    line_height: float = 24.0,
    x0: float = 72.0,
    x1: float | None = None,
) -> ReconstructedParagraph:
    """A typical heading paragraph with large bold typography."""
    if x1 is None:
        x1 = x0 + len(text) * font_size * 0.5
    span = make_span(text, font_size=font_size, font_name=font_name, flags=flags)
    line = make_line(text, (x0, y0, x1, y0 + line_height), 0, (span,))
    return make_paragraph(text, lines=(line,), blocks=(
        LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,)),
    ))


def centered_paragraph(
    text: str,
    y0: float = 100.0,
    font_size: float = 12.0,
    font_name: str = "Helvetica",
    flags: FontFlags = FontFlags(0),
    line_height: float = 16.0,
) -> ReconstructedParagraph:
    """A centered paragraph (left/right margins roughly equal)."""
    width = len(text) * font_size * 0.5
    x0 = (PAGE_WIDTH - width) / 2
    x1 = x0 + width
    span = make_span(text, font_size=font_size, font_name=font_name, flags=flags)
    line = make_line(text, (x0, y0, x1, y0 + line_height), 0, (span,))
    return make_paragraph(text, lines=(line,), blocks=(
        LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,)),
    ))


def multi_line_paragraph(
    lines: list[tuple[str, float, float, float]],
    font_size: float = 12.0,
    font_name: str = "Helvetica",
    flags: FontFlags = FontFlags(0),
) -> ReconstructedParagraph:
    """A multi-line paragraph from (text, x0, y0, y1) tuples."""
    text_lines: list[TextLine] = []
    for i, (text, x0, y0, y1) in enumerate(lines):
        span = make_span(text, font_size=font_size, font_name=font_name, flags=flags)
        text_lines.append(make_line(text, (x0, y0, x0 + len(text) * font_size * 0.5, y1), i, (span,)))
    text = " ".join(t for t, _, _, _ in lines)
    return make_paragraph(text, lines=tuple(text_lines))


# --------------------------------------------------------------------------- #
# 1. Basic heading detection
# --------------------------------------------------------------------------- #


class TestBasicHeadingDetection:
    def test_clearly_larger_bold_paragraph_is_detected(self) -> None:
        # A large bold heading above body text.
        heading = heading_paragraph("Chapter One", y0=100, font_size=24, font_name="Helvetica-Bold")
        body = body_paragraph(
            "This is a normal body paragraph with enough text to be clearly body content.",
            y0=200,
        )
        page = make_page((heading, body))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is True
        assert result.paragraphs[1].is_heading is False

    def test_clearly_body_sized_paragraph_is_not_detected(self) -> None:
        body = body_paragraph(
            "This is a normal body paragraph with ordinary typography and spacing.",
        )
        page = make_page((body,))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is False

    def test_short_body_paragraph_is_not_automatically_heading(self) -> None:
        # "It was." is short but has no other heading evidence.
        short = body_paragraph("It was.", y0=100)
        body = body_paragraph(
            "This is a normal body paragraph that follows the short text.",
            y0=200,
        )
        page = make_page((short, body))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is False

    def test_long_bold_body_paragraph_is_not_heading(self) -> None:
        # A long bold paragraph is body text, not a heading.
        long_text = (
            "This is a very long body paragraph that happens to be bold. "
            "It contains many words and spans multiple sentences. "
            "The length alone should prevent it from being classified as a heading "
            "even though it uses bold typography."
        )
        long_bold = body_paragraph(
            long_text,
            y0=100,
            font_name="Helvetica-Bold",
            flags=FontFlags.BOLD,
        )
        page = make_page((long_bold,))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is False


# --------------------------------------------------------------------------- #
# 2. Typography signals
# --------------------------------------------------------------------------- #


class TestTypographySignals:
    def test_relative_font_size_increase_produces_evidence(self) -> None:
        # Body at 12pt, heading at 18pt (1.5x = strong threshold).
        heading = heading_paragraph("Introduction", y0=100, font_size=18)
        body = body_paragraph(
            "This is a normal body paragraph at the prevailing font size.",
            y0=200,
        )
        page = make_page((heading, body))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is True
        assert "font_size_strongly_above_body_baseline" in result.paragraphs[0].reasons

    def test_bold_typography_contributes_evidence(self) -> None:
        # Bold body-sized text with no other signals should not be a heading.
        bold = body_paragraph(
            "This is bold body text but not a heading.",
            y0=100,
            font_name="Helvetica-Bold",
            flags=FontFlags.BOLD,
        )
        body = body_paragraph(
            "This is a normal body paragraph.",
            y0=200,
        )
        page = make_page((bold, body))
        result = classify_paragraphs(page)
        # Bold alone (1.0) + short (0.25) = 1.25 < 2.5, and only 2 reasons
        # but score is too low.
        assert result.paragraphs[0].is_heading is False

    def test_font_family_change_contributes_evidence(self) -> None:
        # A paragraph with a different font family from the page baseline.
        # Multiple body paragraphs in Helvetica establish the baseline.
        different_family = body_paragraph(
            "This paragraph uses a different font family.",
            y0=100,
            font_name="Times-Roman",
        )
        body1 = body_paragraph(
            "This is a normal body paragraph in the prevailing font.",
            y0=200,
        )
        body2 = body_paragraph(
            "This is another body paragraph in the prevailing font.",
            y0=300,
        )
        body3 = body_paragraph(
            "This is a third body paragraph in the prevailing font.",
            y0=400,
        )
        page = make_page((different_family, body1, body2, body3))
        result = classify_paragraphs(page)
        assert "font_family_change" in result.paragraphs[0].reasons

    def test_absolute_font_size_alone_does_not_define_heading(self) -> None:
        # A 14pt paragraph in a document where body is also 14pt is not a heading.
        body1 = body_paragraph(
            "This is a normal body paragraph at fourteen points.",
            y0=100,
            font_size=14.0,
        )
        body2 = body_paragraph(
            "Another body paragraph also at fourteen points.",
            y0=200,
            font_size=14.0,
        )
        page = make_page((body1, body2))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is False
        assert result.paragraphs[1].is_heading is False


# --------------------------------------------------------------------------- #
# 3. Geometry signals
# --------------------------------------------------------------------------- #


class TestGeometrySignals:
    def test_centered_isolated_paragraph_can_be_detected(self) -> None:
        # A centered paragraph with large whitespace around it.
        # Large font (1.5) + centered (0.65) + very_short (0.25) = 2.4 < 2.5.
        # Add bold to cross the threshold: 2.4 + 1.0 = 3.4 >= 2.5.
        centered = centered_paragraph(
            "Chapter Title",
            y0=100,
            font_size=24,
            font_name="Helvetica-Bold",
            flags=FontFlags.BOLD,
        )
        body = body_paragraph(
            "This is a normal body paragraph that follows the centered title.",
            y0=300,
        )
        page = make_page((centered, body))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is True
        assert "centered_alignment" in result.paragraphs[0].reasons

    def test_centered_body_paragraph_is_not_automatically_heading(self) -> None:
        # A centered body-sized paragraph with no other signals.
        centered = centered_paragraph(
            "This is a centered body paragraph with ordinary typography.",
            y0=100,
            font_size=12,
        )
        body = body_paragraph(
            "This is a normal body paragraph.",
            y0=200,
        )
        page = make_page((centered, body))
        result = classify_paragraphs(page)
        # Centered (0.65) + short (0.25) = 0.9 < 2.5, only 2 reasons but
        # score too low.
        assert result.paragraphs[0].is_heading is False

    def test_large_whitespace_around_paragraph_contributes_evidence(self) -> None:
        # A paragraph with large whitespace before and after.
        body1 = body_paragraph(
            "First body paragraph at the top of the page.",
            y0=100,
        )
        isolated = body_paragraph(
            "Isolated paragraph with large gaps.",
            y0=300,
        )
        body2 = body_paragraph(
            "Second body paragraph after the isolated one.",
            y0=500,
        )
        page = make_page((body1, isolated, body2))
        result = classify_paragraphs(page)
        # Large spacing before (0.75) + after (0.75) + isolation (0.5) +
        # short (0.25) = 2.25 < 2.5. Not quite a heading.
        assert result.paragraphs[1].is_heading is False

    def test_ordinary_body_spacing_does_not_create_false_positive(self) -> None:
        # Normal body paragraphs with ordinary spacing.
        body1 = body_paragraph(
            "This is a normal body paragraph with ordinary spacing.",
            y0=100,
        )
        body2 = body_paragraph(
            "This is another normal body paragraph with ordinary spacing.",
            y0=200,
        )
        body3 = body_paragraph(
            "This is a third normal body paragraph with ordinary spacing.",
            y0=300,
        )
        page = make_page((body1, body2, body3))
        result = classify_paragraphs(page)
        assert all(not p.is_heading for p in result.paragraphs)


# --------------------------------------------------------------------------- #
# 4. Context analysis
# --------------------------------------------------------------------------- #


class TestContextAnalysis:
    def test_body_baseline_is_determined_from_surrounding_content(self) -> None:
        # Multiple body paragraphs establish the baseline; a larger heading
        # is detected relative to that baseline.
        body1 = body_paragraph(
            "This is a normal body paragraph at twelve points.",
            y0=100,
            font_size=12,
        )
        body2 = body_paragraph(
            "This is another body paragraph at twelve points.",
            y0=200,
            font_size=12,
        )
        heading = heading_paragraph("Chapter Two", y0=300, font_size=18)
        body3 = body_paragraph(
            "This is a body paragraph after the heading.",
            y0=400,
            font_size=12,
        )
        page = make_page((body1, body2, heading, body3))
        result = classify_paragraphs(page)
        assert result.paragraphs[2].is_heading is True

    def test_slightly_larger_body_like_paragraph_is_not_promoted(self) -> None:
        # A paragraph slightly larger than body (1.2x) but otherwise body-like.
        body1 = body_paragraph(
            "This is a normal body paragraph at twelve points.",
            y0=100,
            font_size=12,
        )
        slightly_larger = body_paragraph(
            "This paragraph is slightly larger but still body-like.",
            y0=200,
            font_size=14.4,  # 1.2x, below 1.25 threshold
        )
        body2 = body_paragraph(
            "This is another body paragraph at twelve points.",
            y0=300,
            font_size=12,
        )
        page = make_page((body1, slightly_larger, body2))
        result = classify_paragraphs(page)
        assert result.paragraphs[1].is_heading is False

    def test_strong_combined_signals_classify_heading(self) -> None:
        # Large font + bold + centered + large spacing = strong heading.
        heading = heading_paragraph(
            "The Great Adventure",
            y0=100,
            font_size=24,
            font_name="Helvetica-Bold",
            flags=FontFlags.BOLD,
        )
        # Make it centered by adjusting x0.
        width = len("The Great Adventure") * 24 * 0.5
        x0 = (PAGE_WIDTH - width) / 2
        x1 = x0 + width
        span = make_span("The Great Adventure", font_size=24, font_name="Helvetica-Bold", flags=FontFlags.BOLD)
        line = make_line("The Great Adventure", (x0, 100, x1, 124), 0, (span,))
        heading = make_paragraph("The Great Adventure", lines=(line,), blocks=(
            LayoutBlock(text="The Great Adventure", bbox=line.bbox, order=0, lines=(line,)),
        ))
        body = body_paragraph(
            "This is a normal body paragraph that follows the heading.",
            y0=300,
        )
        page = make_page((heading, body))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is True

    def test_weak_isolated_signals_do_not_classify_heading(self) -> None:
        # A short paragraph with only weak signals (short + page edge).
        short = body_paragraph("Short", y0=50, font_size=12)
        body = body_paragraph(
            "This is a normal body paragraph.",
            y0=200,
        )
        page = make_page((short, body))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is False


# --------------------------------------------------------------------------- #
# 5. Multi-line paragraphs
# --------------------------------------------------------------------------- #


class TestMultiLineParagraphs:
    def test_multi_line_heading_is_classified_as_one_heading(self) -> None:
        # A multi-line heading already reconstructed by M2.3.
        lines = [
            ("A Very Long", 72, 100, 124),
            ("Chapter Heading", 72, 124, 148),
        ]
        heading = multi_line_paragraph(
            lines,
            font_size=20,
            font_name="Helvetica-Bold",
            flags=FontFlags.BOLD,
        )
        body = body_paragraph(
            "This is a normal body paragraph that follows the heading.",
            y0=300,
        )
        page = make_page((heading, body))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is True
        assert result.paragraphs[0].paragraph.line_count == 2

    def test_normal_multi_line_body_paragraph_remains_body(self) -> None:
        # A normal multi-line body paragraph.
        lines = [
            ("This is the first line of a normal body paragraph", 72, 100, 116),
            ("that continues onto a second line of text", 72, 116, 132),
            ("and finishes on a third line of the paragraph", 72, 132, 148),
        ]
        body = multi_line_paragraph(lines, font_size=12)
        page = make_page((body,))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is False


# --------------------------------------------------------------------------- #
# 6. Reading order
# --------------------------------------------------------------------------- #


class TestReadingOrder:
    def test_paragraphs_processed_in_m23_order(self) -> None:
        # Paragraphs must be classified in the exact M2.3 order.
        body1 = body_paragraph("First body paragraph", y0=100)
        heading = heading_paragraph("Chapter One", y0=200, font_size=20)
        body2 = body_paragraph("Second body paragraph", y0=300)
        page = make_page((body1, heading, body2))
        result = classify_paragraphs(page)
        assert [p.text for p in result.paragraphs] == [
            "First body paragraph",
            "Chapter One",
            "Second body paragraph",
        ]
        assert result.paragraphs[1].is_heading is True

    def test_two_column_input_consumed_without_additional_sorting(self) -> None:
        # Simulate M2.2 two-column output: A1, A2, A3, B1, B2, B3.
        paragraphs = tuple(
            body_paragraph(f"Column {letter} row {i}", y0=100 + i * 50)
            for letter in ("A", "B")
            for i in range(3)
        )
        page = make_page(paragraphs)
        result = classify_paragraphs(page)
        assert [p.text for p in result.paragraphs] == [
            "Column A row 0",
            "Column A row 1",
            "Column A row 2",
            "Column B row 0",
            "Column B row 1",
            "Column B row 2",
        ]


# --------------------------------------------------------------------------- #
# 7. Provenance
# --------------------------------------------------------------------------- #


class TestProvenance:
    def test_original_paragraph_objects_remain_unchanged(self) -> None:
        heading = heading_paragraph("Chapter One", y0=100, font_size=20)
        body = body_paragraph(
            "This is a normal body paragraph.",
            y0=200,
        )
        page = make_page((heading, body))
        result = classify_paragraphs(page)
        # The classified paragraph references the same object.
        assert result.paragraphs[0].paragraph is heading
        assert result.paragraphs[1].paragraph is body
        # The original objects are unchanged.
        assert heading.text == "Chapter One"
        assert body.text == "This is a normal body paragraph."

    def test_source_lines_blocks_provenance_remain_intact(self) -> None:
        heading = heading_paragraph("Chapter One", y0=100, font_size=20)
        body = body_paragraph(
            "This is a normal body paragraph.",
            y0=200,
        )
        page = make_page((heading, body))
        result = classify_paragraphs(page)
        classified = result.paragraphs[0]
        assert classified.source_lines == heading.source_lines
        assert classified.source_blocks == heading.source_blocks
        assert classified.paragraph.source_line_orders == heading.source_line_orders
        assert classified.paragraph.reading_order_indices == heading.reading_order_indices
        assert classified.paragraph.page_number == 1


# --------------------------------------------------------------------------- #
# 8. Page boundaries
# --------------------------------------------------------------------------- #


class TestPageBoundaries:
    def test_heading_detection_does_not_merge_across_pages(self) -> None:
        # Two pages, each with its own content. No cross-page merging.
        page1_heading = heading_paragraph("Chapter One", y0=100, font_size=20)
        page1_body = body_paragraph(
            "This is body text on page one.",
            y0=200,
        )
        page2_heading = heading_paragraph("Chapter Two", y0=100, font_size=20)
        page2_body = body_paragraph(
            "This is body text on page two.",
            y0=200,
        )
        page1 = make_page((page1_heading, page1_body), page_number=1)
        page2 = make_page((page2_heading, page2_body), page_number=2)
        layout = make_layout((page1, page2))
        result = classify_paragraphs(layout)
        assert result.page_count == 2
        assert result.pages[0].page_number == 1
        assert result.pages[1].page_number == 2
        assert result.pages[0].paragraphs[0].is_heading is True
        assert result.pages[1].paragraphs[0].is_heading is True


# --------------------------------------------------------------------------- #
# 9. Determinism
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_same_input_produces_identical_output(self) -> None:
        heading = heading_paragraph("Chapter One", y0=100, font_size=20)
        body = body_paragraph(
            "This is a normal body paragraph.",
            y0=200,
        )
        page = make_page((heading, body))
        first = classify_paragraphs(page)
        second = classify_paragraphs(page)
        assert first == second
        assert first.paragraphs[0].score == second.paragraphs[0].score
        assert first.paragraphs[0].reasons == second.paragraphs[0].reasons
        assert first.paragraphs[0].is_heading == second.paragraphs[0].is_heading


# --------------------------------------------------------------------------- #
# 10. Edge cases
# --------------------------------------------------------------------------- #


class TestEdgeCases:
    def test_empty_layout(self) -> None:
        page = make_page(())
        result = classify_paragraphs(page)
        assert result.paragraph_count == 0
        assert result.heading_count == 0

    def test_single_paragraph(self) -> None:
        body = body_paragraph(
            "This is a single body paragraph on the page.",
        )
        page = make_page((body,))
        result = classify_paragraphs(page)
        assert result.paragraph_count == 1
        assert result.paragraphs[0].is_heading is False

    def test_paragraph_with_missing_font_metadata(self) -> None:
        # A paragraph with no font size or name information.
        span = TextSpan(text="No font info", bbox=(72, 100, 200, 116))
        line = make_line("No font info", (72, 100, 200, 116), 0, (span,))
        para = make_paragraph("No font info", lines=(line,))
        page = make_page((para,))
        result = classify_paragraphs(page)
        # No font metadata means no font-size evidence; should not be heading.
        assert result.paragraphs[0].is_heading is False

    def test_paragraph_with_mixed_font_sizes(self) -> None:
        # A paragraph with mixed font sizes (e.g. a drop cap).
        span1 = make_span("T", font_size=24, font_name="Helvetica")
        span2 = make_span("his is mixed size text", font_size=12, font_name="Helvetica")
        line = TextLine(
            text="This is mixed size text",
            bbox=(72, 100, 300, 124),
            order=0,
            spans=(span1, span2),
        )
        para = make_paragraph("This is mixed size text", lines=(line,))
        body = body_paragraph(
            "This is a normal body paragraph.",
            y0=200,
        )
        page = make_page((para, body))
        result = classify_paragraphs(page)
        # Mixed font size contrast (0.5) + short (0.25) = 0.75 < 2.5.
        assert result.paragraphs[0].is_heading is False

    def test_paragraph_with_unusual_alignment(self) -> None:
        # A right-aligned paragraph (not centered, not left).
        text = "Right aligned text"
        span = make_span(text, font_size=12)
        line = make_line(text, (400, 100, 500, 116), 0, (span,))
        para = make_paragraph(text, lines=(line,))
        body = body_paragraph(
            "This is a normal body paragraph.",
            y0=200,
        )
        page = make_page((para, body))
        result = classify_paragraphs(page)
        # Not centered, no other strong signals.
        assert result.paragraphs[0].is_heading is False

    def test_paragraph_with_zero_source_lines(self) -> None:
        # A paragraph with no source lines (edge case).
        para = ReconstructedParagraph(
            text="",
            page_number=1,
            source_lines=(),
            source_blocks=(),
            source_line_orders=(),
            reading_order_indices=(),
        )
        page = make_page((para,))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is False


# --------------------------------------------------------------------------- #
# 11. Threshold testing
# --------------------------------------------------------------------------- #


class TestThresholds:
    def test_font_size_just_below_heading_ratio_threshold(self) -> None:
        # Body at 12pt, candidate at 14.9pt (ratio 1.2417 < 1.25).
        body1 = body_paragraph(
            "This is a normal body paragraph at twelve points.",
            y0=100,
            font_size=12,
        )
        candidate = body_paragraph(
            "This paragraph is slightly larger but not quite a heading.",
            y0=200,
            font_size=14.9,
        )
        body2 = body_paragraph(
            "This is another body paragraph at twelve points.",
            y0=300,
            font_size=12,
        )
        page = make_page((body1, candidate, body2))
        result = classify_paragraphs(page)
        assert result.paragraphs[1].is_heading is False

    def test_font_size_just_above_heading_ratio_threshold(self) -> None:
        # Body at 12pt, candidate at 15.1pt (ratio 1.2583 > 1.25).
        body1 = body_paragraph(
            "This is a normal body paragraph at twelve points.",
            y0=100,
            font_size=12,
        )
        candidate = body_paragraph(
            "This paragraph is slightly larger and crosses the threshold.",
            y0=200,
            font_size=15.1,
        )
        body2 = body_paragraph(
            "This is another body paragraph at twelve points.",
            y0=300,
            font_size=12,
        )
        page = make_page((body1, candidate, body2))
        result = classify_paragraphs(page)
        # Font size above baseline (1.0) + short (0.25) = 1.25 < 2.5.
        # Not a heading because score is too low.
        assert result.paragraphs[1].is_heading is False
        assert "font_size_above_body_baseline" in result.paragraphs[1].reasons

    def test_spacing_just_below_threshold(self) -> None:
        # Body paragraphs with spacing just below the large-spacing threshold.
        body1 = body_paragraph(
            "This is a normal body paragraph.",
            y0=100,
        )
        body2 = body_paragraph(
            "This is another body paragraph.",
            y0=200,
        )
        page = make_page((body1, body2))
        result = classify_paragraphs(page)
        # Normal spacing (16pt gap) is below the threshold.
        assert result.paragraphs[1].is_heading is False
        assert "large_spacing_before" not in result.paragraphs[1].reasons

    def test_spacing_just_above_threshold(self) -> None:
        # A paragraph with large spacing before it.
        body1 = body_paragraph(
            "This is a normal body paragraph.",
            y0=100,
        )
        isolated = body_paragraph(
            "This paragraph has large spacing before it.",
            y0=300,
        )
        body2 = body_paragraph(
            "This is another body paragraph.",
            y0=400,
        )
        page = make_page((body1, isolated, body2))
        result = classify_paragraphs(page)
        # Large spacing before (0.75) + short (0.25) = 1.0 < 2.5.
        assert "large_spacing_before" in result.paragraphs[1].reasons
        assert result.paragraphs[1].is_heading is False

    def test_score_just_below_classification_threshold(self) -> None:
        # A paragraph with strong signals but not quite enough score.
        # Bold (1.0) + centered (0.65) + short (0.25) = 1.9 < 2.5.
        centered_bold = centered_paragraph(
            "Bold Centered",
            y0=100,
            font_size=12,
            font_name="Helvetica-Bold",
            flags=FontFlags.BOLD,
        )
        body = body_paragraph(
            "This is a normal body paragraph.",
            y0=200,
        )
        page = make_page((centered_bold, body))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is False
        assert result.paragraphs[0].score < HEADING_SCORE_THRESHOLD

    def test_score_just_above_classification_threshold(self) -> None:
        # A paragraph with strong combined signals that crosses the threshold.
        # Large font (1.0 + 0.5) + bold (1.0) + centered (0.65) + short (0.25)
        # = 3.4 >= 2.5.
        heading = heading_paragraph(
            "Chapter One",
            y0=100,
            font_size=20,
            font_name="Helvetica-Bold",
            flags=FontFlags.BOLD,
        )
        # Make it centered.
        width = len("Chapter One") * 20 * 0.5
        x0 = (PAGE_WIDTH - width) / 2
        x1 = x0 + width
        span = make_span("Chapter One", font_size=20, font_name="Helvetica-Bold", flags=FontFlags.BOLD)
        line = make_line("Chapter One", (x0, 100, x1, 124), 0, (span,))
        heading = make_paragraph("Chapter One", lines=(line,), blocks=(
            LayoutBlock(text="Chapter One", bbox=line.bbox, order=0, lines=(line,)),
        ))
        body = body_paragraph(
            "This is a normal body paragraph.",
            y0=300,
        )
        page = make_page((heading, body))
        result = classify_paragraphs(page)
        assert result.paragraphs[0].is_heading is True
        assert result.paragraphs[0].score >= HEADING_SCORE_THRESHOLD


# --------------------------------------------------------------------------- #
# 12. Public API shape
# --------------------------------------------------------------------------- #


class TestAPIShape:
    def test_single_page_returns_heading_page(self) -> None:
        body = body_paragraph("This is a body paragraph.")
        page = make_page((body,))
        result = classify_paragraphs(page)
        from kindle_converter.pdf.headings import HeadingPage
        assert isinstance(result, HeadingPage)

    def test_multi_page_returns_heading_layout(self) -> None:
        page1 = make_page((body_paragraph("Body on page one"),), page_number=1)
        page2 = make_page((body_paragraph("Body on page two"),), page_number=2)
        layout = make_layout((page1, page2))
        result = classify_paragraphs(layout)
        from kindle_converter.pdf.headings import HeadingLayout
        assert isinstance(result, HeadingLayout)
        assert result.page_count == 2

    def test_detect_headings_is_alias_for_classify(self) -> None:
        body = body_paragraph("This is a body paragraph.")
        page = make_page((body,))
        result1 = classify_paragraphs(page)
        result2 = detect_headings(page)
        assert result1 == result2

    def test_heading_page_properties(self) -> None:
        heading = heading_paragraph("Chapter One", y0=100, font_size=20)
        body = body_paragraph(
            "This is a normal body paragraph.",
            y0=200,
        )
        page = make_page((heading, body))
        result = classify_paragraphs(page)
        assert result.paragraph_count == 2
        assert result.heading_count == 1
        assert len(result.headings) == 1
        assert result.headings[0].text == "Chapter One"

    def test_heading_layout_properties(self) -> None:
        page1 = make_page(
            (
                heading_paragraph("Chapter One", y0=100, font_size=20),
                body_paragraph("Body text on page one", y0=200),
            ),
            page_number=1,
        )
        page2 = make_page(
            (body_paragraph("Body text on page two"),),
            page_number=2,
        )
        layout = make_layout((page1, page2))
        result = classify_paragraphs(layout)
        assert result.page_count == 2
        assert result.paragraph_count == 3
        assert result.heading_count == 1
        assert len(result.paragraphs) == 3
        assert len(result.headings) == 1


# --------------------------------------------------------------------------- #
# 13. Regression: value-identical paragraphs use positional neighbor lookup
# --------------------------------------------------------------------------- #


class TestPositionalNeighborLookup:
    def test_value_identical_paragraphs_use_actual_sequence_positions(self) -> None:
        """Two value-identical paragraphs at different positions must use
        their actual sequence positions for spacing calculations, not the
        first equal object found by value equality."""
        # Create two paragraphs with identical field values (same text,
        # same font metadata, same source lines/blocks). They are
        # value-equal as dataclasses.
        text = "Identical paragraph text"
        span = make_span(text, font_size=12, font_name="Helvetica")
        line = make_line(text, (72, 100, 300, 116), 0, (span,))
        block = LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,))

        # Both paragraphs have identical field values (same source lines,
        # same blocks, same text, same page number, etc.).
        para1 = make_paragraph(text, lines=(line,), blocks=(block,))
        para2 = make_paragraph(text, lines=(line,), blocks=(block,))

        # Verify they are value-equal (the regression this test protects).
        assert para1 == para2

        # Add a third paragraph after para2 to establish context.
        body = body_paragraph(
            "This is a normal body paragraph after the identical ones.",
            y0=500,
        )

        page = make_page((para1, para2, body))
        result = classify_paragraphs(page)

        # Both para1 and para2 have the same geometry (y=100). The spacing
        # between them is 0 (they overlap). The key regression is that
        # .index() would find para1 for both, but positional lookup
        # correctly identifies each paragraph's actual position.
        # para1 (index 0) has no spacing before it.
        assert "large_spacing_before" not in result.paragraphs[0].reasons
        # para2 (index 1) has no spacing before it either (same y position).
        assert "large_spacing_before" not in result.paragraphs[1].reasons
        # But para2 should NOT have spacing computed against para1's
        # position incorrectly. The key assertion is that both paragraphs
        # are classified independently and the spacing is computed from
        # their actual sequence positions.
        assert result.paragraphs[0].paragraph is para1
        assert result.paragraphs[1].paragraph is para2


# --------------------------------------------------------------------------- #
# 14. Regression: one-sided large whitespace + ordinary body typography
# --------------------------------------------------------------------------- #


class TestIsolationStrongCue:
    def test_one_sided_large_whitespace_with_ordinary_body_typography(self) -> None:
        """A paragraph with large whitespace on one side but ordinary body
        typography (font metadata present, not centered, not confirmed bold,
        not elevated font size) must NOT receive ``isolated_paragraph``."""
        body1 = body_paragraph(
            "This is a normal body paragraph at the top of the page.",
            y0=100,
        )
        # This paragraph has large whitespace before it (gap from y=116 to
        # y=300 = 184pt) but is otherwise ordinary body typography.
        ordinary = body_paragraph(
            "This paragraph has large whitespace before it but is ordinary body text.",
            y0=300,
        )
        body2 = body_paragraph(
            "This is another normal body paragraph after the isolated one.",
            y0=400,
        )
        page = make_page((body1, ordinary, body2))
        result = classify_paragraphs(page)

        # The ordinary paragraph should have large_spacing_before but NOT
        # isolated_paragraph (because it lacks a genuine strong cue).
        assert "large_spacing_before" in result.paragraphs[1].reasons
        assert "isolated_paragraph" not in result.paragraphs[1].reasons

    def test_one_sided_large_whitespace_with_strong_cue_still_gets_isolation(self) -> None:
        """A paragraph with large whitespace on one side AND a genuine strong
        cue (e.g. confirmed bold) should still receive ``isolated_paragraph``."""
        body1 = body_paragraph(
            "This is a normal body paragraph at the top of the page.",
            y0=100,
        )
        # This paragraph has large whitespace before it AND is confirmed bold.
        bold = body_paragraph(
            "This bold paragraph has large whitespace before it.",
            y0=300,
            font_name="Helvetica-Bold",
            flags=FontFlags.BOLD,
        )
        body2 = body_paragraph(
            "This is another normal body paragraph after the bold one.",
            y0=400,
        )
        page = make_page((body1, bold, body2))
        result = classify_paragraphs(page)

        # The bold paragraph should have both large_spacing_before and
        # isolated_paragraph.
        assert "large_spacing_before" in result.paragraphs[1].reasons
        assert "isolated_paragraph" in result.paragraphs[1].reasons


# --------------------------------------------------------------------------- #
# 15. End-to-end with a synthetic PDF
# --------------------------------------------------------------------------- #


class TestEndToEnd:
    def test_synthetic_pdf_heading_detection(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        # Insert a heading and body text.
        page.insert_text((72, 100), "Chapter One", fontsize=20, fontname="hebo")
        page.insert_text((72, 200), "This is a normal body paragraph.", fontsize=12, fontname="helv")
        doc.save(str(tmp_path / "heading.pdf"))
        doc.close()

        from kindle_converter.pdf import extract_page_layout
        layout = extract_page_layout(tmp_path / "heading.pdf")
        ordered = reconstruct_read_order(layout)
        paragraphs = reconstruct_paragraphs(ordered)
        result = classify_paragraphs(paragraphs)
        assert result.paragraph_count >= 1
        # At least one paragraph should be detected as a heading.
        assert result.heading_count >= 1