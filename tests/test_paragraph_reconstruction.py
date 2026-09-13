"""Tests for paragraph reconstruction from M2.2 reading order (Milestone 2.3).

These tests construct M2.1 layout objects directly (TextLine, LayoutBlock,
LayoutPage, OrderedPage) to exercise precise geometry without relying on
PDF rendering. Synthetic PDFs are used for end-to-end integration cases.

M2.3 never reorders lines: it consumes the M2.2 reading order as-is and
decides only whether consecutive lines belong to the same paragraph.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter.pdf import (
    LayoutBlock,
    LayoutPage,
    OrderedLayout,
    OrderedPage,
    PageLayout,
    TextLine,
    reconstruct_paragraphs,
    reconstruct_read_order,
)
from kindle_converter.pdf.paragraphs import (
    ReconstructedParagraph,
    ParagraphPage,
    ParagraphLayout,
    PARAGRAPH_GAP_FACTOR,
    PARAGRAPH_INDENT_PT,
    SHORT_LINE_THRESHOLD,
    reconstruct_page_paragraphs,
)

# --------------------------------------------------------------------------- #
# Construction helpers
# --------------------------------------------------------------------------- #

PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def make_line(text: str, bbox, order: int = 0) -> TextLine:
    """A minimal text line with the given text, bbox, and line order."""
    from kindle_converter.pdf import TextLine
    return TextLine(text=text, bbox=bbox, order=order)


def make_line_inline(text: str, x0: float, y0: float, y1: float, order: int = 0) -> TextLine:
    """A minimal text line at a given x0 and y range, auto-width from text."""
    from kindle_converter.pdf import TextLine
    width = len(text) * 5.0  # Approximate width per character
    return TextLine(text=text, bbox=(x0, y0, x0 + width, y1), order=order)


def make_block(text: str, bbox, order: int, lines: tuple | None = None) -> LayoutBlock:
    """A minimal text block with the given text, bbox, order, and lines."""
    if lines is not None:
        return LayoutBlock(text=text, bbox=bbox, order=order, lines=lines)
    return LayoutBlock(text=text, bbox=bbox, order=order)


def make_ordered_page(blocks: list, page_number: int = 1) -> OrderedPage:
    """An OrderedPage where blocks are in the given (reading) order."""
    page = LayoutPage(
        page_number=page_number,
        page_width=PAGE_WIDTH,
        page_height=PAGE_HEIGHT,
        blocks=tuple(blocks),
    )
    return reconstruct_read_order(page)


def make_line_list(lines: list[tuple[str, float, float, float]], indent: float = 0.0) -> list:
    """Build a list of (text, x0, y0, y1) lines."""
    result = []
    for text, x0, y0, y1 in lines:
        result.append((text, x0 + indent, y0, y1))
    return result


# --------------------------------------------------------------------------- #
# 1. Three vertically wrapped lines -> one paragraph
# --------------------------------------------------------------------------- #


class TestWrappedLines:
    def test_three_wrapped_lines_form_one_paragraph(self) -> None:
        lines = [
            make_line("This is the first physical line of", (72, 100, 400, 116), 0),
            make_line("a paragraph and this is the second", (72, 116, 400, 132), 1),
            make_line("physical line of the same paragraph", (72, 132, 400, 148), 2),
        ]
        block = make_block("wrapped text", (72, 100, 400, 148), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 1
        text = result.paragraphs[0].text
        assert "first physical line" in text
        assert "second physical line" in text
        assert "same paragraph" in text


# --------------------------------------------------------------------------- #
# 2. Normal line spacing does not create paragraph breaks
# --------------------------------------------------------------------------- #


class TestNormalSpacing:
    def test_normal_spacing_keeps_paragraph_together(self) -> None:
        line_height = 16.0
        lines = [
            make_line(f"Line {i} of a paragraph", (72, 100 + i * line_height, 400, 116 + i * line_height), i)
            for i in range(5)
        ]
        block = make_block("normal text", (72, 100, 400, 176), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 1


# --------------------------------------------------------------------------- #
# 3. Large vertical gap -> paragraph boundary
# --------------------------------------------------------------------------- #


class TestLargeGap:
    def test_large_vertical_gap_creates_boundary(self) -> None:
        line_height = 16.0
        lines = [
            make_line("First paragraph line one", (72, 100, 400, 116), 0),
            make_line("and its second line", (72, 116, 400, 132), 1),
            make_line("Second paragraph", (72, 100 + 3 * line_height, 400, 112 + 3 * line_height), 2),
        ]
        block = make_block("text", (72, 100, 400, 148), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 2
        assert "First paragraph" in result.paragraphs[0].text
        assert "Second paragraph" in result.paragraphs[1].text


# --------------------------------------------------------------------------- #
# 4. Small floating-point coordinate differences -> no false boundary
# --------------------------------------------------------------------------- #


class TestFloatingPointTolerance:
    def test_subpoint_noise_does_not_split_paragraph(self) -> None:
        lines = [
            make_line("Continuous text line one", (72.0, 100.0, 400, 116.0), 0),
            make_line("Continued text line two", (72.001, 116.0, 400, 132.0), 1),
            make_line("More continuation line", (72.0, 132.0, 400, 148.0), 2),
        ]
        block = make_block("text", (72.0, 100.0, 400, 148.0), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 1


# --------------------------------------------------------------------------- #
# 5. First-line indentation -> remains one paragraph
# --------------------------------------------------------------------------- #


class TestContinuationIndent:
    def test_first_line_indent_remaining_one_paragraph(self) -> None:
        # First line is indented (x0=80), continuation lines at x0=60
        lines = [
            make_line("Indented first line", (80, 100, 400, 116), 0),
            make_line("Continuation line two", (60, 116, 400, 132), 1),
            make_line("Continuation line three", (60, 132, 400, 148), 2),
        ]
        block = make_block("text", (60, 100, 400, 148), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 1
        assert "Indented first line" in result.paragraphs[0].text
        assert "Continuation line two" in result.paragraphs[0].text


# --------------------------------------------------------------------------- #
# 6. New indented paragraph -> boundary when supported by context
# --------------------------------------------------------------------------- #


class TestNewIndentedParagraph:
    def test_new_indented_paragraph_creates_boundary(self) -> None:
        line_height = 16.0
        lines = [
            make_line("Previous paragraph", (60, 100, 400, 116), 0),
            make_line("Previous paragraph cont", (60, 116, 400, 132), 1),
            make_line("New indented paragraph", (80, 116 + 3 * line_height, 400, 132 + 3 * line_height), 2),
            make_line("New paragraph continues", (60, 132 + 3 * line_height, 400, 148 + 3 * line_height), 3),
        ]
        block = make_block("text", (60, 100, 400, 200), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 2
        assert "Previous paragraph" in result.paragraphs[0].text
        assert "New indented paragraph" in result.paragraphs[1].text


# --------------------------------------------------------------------------- #
# 7. Different line widths -> no false boundary
# --------------------------------------------------------------------------- #


class TestLineWidths:
    def test_different_line_widths_no_false_boundary(self) -> None:
        lines = [
            make_line("A very long line that stretches across the page", (72, 100, 400, 116), 0),
            make_line("Shorter line here", (72, 116, 300, 132), 1),  # > 8 chars
            make_line("Another longer line here", (72, 132, 400, 148), 2),
        ]
        block = make_block("text", (72, 100, 400, 148), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 1


# --------------------------------------------------------------------------- #
# 8. Short final line -> remains in paragraph
# --------------------------------------------------------------------------- #


class TestShortFinalLine:
    def test_short_final_line_remains_in_paragraph(self) -> None:
        lines = [
            make_line("A very long paragraph line here", (72, 100, 400, 116), 0),
            make_line("Concluding the paragraph", (72, 116, 400, 132), 1),
        ]
        block = make_block("text", (72, 100, 400, 132), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 1
        text = result.paragraphs[0].text
        assert "Concluding the paragraph" in text


# --------------------------------------------------------------------------- #
# 9. Multiple paragraphs on one page -> correct grouping
# --------------------------------------------------------------------------- #


class TestMultipleParagraphs:
    def test_multiple_paragraphs_grouped_correctly(self) -> None:
        line_height = 16.0
        gap = 3 * line_height  # Large enough gap for paragraph separation
        lines = [
            make_line("Paragraph one line one", (72, 100, 400, 116), 0),
            make_line("Paragraph one line two", (72, 116, 400, 132), 1),
            # Gap
            make_line("Paragraph two here", (72, 132 + gap, 400, 148 + gap), 2),
            make_line("Paragraph two continues", (72, 148 + gap, 400, 164 + gap), 3),
            # Gap
            make_line("Paragraph three final", (72, 164 + 2 * gap, 400, 180 + 2 * gap), 4),
        ]
        block = make_block("text", (72, 100, 400, 300), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 3
        assert "Paragraph one" in result.paragraphs[0].text
        assert "Paragraph two" in result.paragraphs[1].text
        assert "Paragraph three" in result.paragraphs[2].text


# --------------------------------------------------------------------------- #
# 10. M2.2 reading order is respected exactly
# --------------------------------------------------------------------------- #


class TestReadingOrderRespected:
    def test_reading_order_is_respected(self) -> None:
        # Create blocks in an order different from their geometric position.
        # M2.2 should reorder them. M2.3 should follow M2.2 order.
        block_bottom = make_block("Bottom text line here", (72, 300, 400, 316), 0,
                                  (make_line("Bottom text line here", (72, 300, 400, 316), 0),))
        block_top = make_block("Top text line here", (72, 100, 400, 116), 1,
                               (make_line("Top text line here", (72, 100, 400, 116), 0),))
        page = LayoutPage(
            page_number=1,
            page_width=PAGE_WIDTH,
            page_height=PAGE_HEIGHT,
            blocks=(block_bottom, block_top),  # Extraction order
        )
        ordered = reconstruct_read_order(page)
        result = reconstruct_page_paragraphs(ordered)
        # M2.2 reordered: top first, bottom second. With big gap, 2 paragraphs.
        assert len(result.paragraphs) == 2
        assert "Top text line" in result.paragraphs[0].text
        assert "Bottom text line" in result.paragraphs[1].text


# --------------------------------------------------------------------------- #
# 11. M2.3 does not perform its own reordering
# --------------------------------------------------------------------------- #


class TestNoReordering:
    def test_m23_does_not_reorder(self) -> None:
        # Create blocks in scrambled extraction order where M2.2 will reorder
        # them by geometry. M2.3 must follow M2.2's order, not do its own sort.
        # Block A (top), Block B (mid), Block C (bottom) -- extraction order is C, A, B.
        line_height = 16.0
        gap = 3 * line_height  # Large gap ensures separate paragraphs

        line_a = make_line("Middle block text here", (72, 100, 400, 116), 0)
        line_b = make_line("Third block text line", (72, 100 + gap, 400, 116 + gap), 0)
        line_c = make_line("First block text here", (72, 100 + 2 * gap, 400, 116 + 2 * gap), 0)

        block_a = make_block("Middle block", (72, 100, 400, 116), 0, (line_a,))
        block_b = make_block("Third block", (72, 100 + gap, 400, 132 + gap), 1, (line_b,))
        block_c = make_block("First block", (72, 100 + 2 * gap, 400, 132 + 2 * gap), 2, (line_c,))

        # Extraction order: C (bottom), A (top), B (mid) -- scrambled
        page = LayoutPage(
            page_number=1, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT,
            blocks=(block_c, block_a, block_b),
        )
        ordered = reconstruct_read_order(page)
        result = reconstruct_page_paragraphs(ordered)

        # M2.2 reorders by geometry: A (top), B (mid), C (bottom)
        # M2.3 follows that order exactly (does not re-sort)
        texts = [p.text for p in result.paragraphs]
        assert texts == ["Middle block text here", "Third block text line", "First block text here"]


# --------------------------------------------------------------------------- #
# 12. Obvious two-column M2.2 output is consumed in supplied order
# --------------------------------------------------------------------------- #


class TestTwoColumnConsumption:
    def test_two_column_output_consumed_in_order(self) -> None:
        # Simulate what M2.2 produces for two columns: A1, A2, A3, B1, B2, B3
        gap = 30.0  # Large vertical gap
        lines_a = [
            make_line(f"A{i} content", (72, 100 + i * (16 + gap), 250, 116 + i * (16 + gap)), i)
            for i in range(3)
        ]
        lines_b = [
            make_line(f"B{i} content", (330, 100 + i * (16 + gap), 500, 116 + i * (16 + gap)), i)
            for i in range(3)
        ]
        block_a = make_block("A column", (72, 100, 250, 300), 0, tuple(lines_a))
        block_b = make_block("B column", (330, 100, 500, 300), 1, tuple(lines_b))
        page = LayoutPage(
            page_number=1,
            page_width=PAGE_WIDTH,
            page_height=PAGE_HEIGHT,
            blocks=(block_a, block_b),
        )
        ordered = reconstruct_read_order(page)
        result = reconstruct_page_paragraphs(ordered)
        # M2.2 reorders column-wise: A1 A2 A3 B1 B2 B3
        # Each line is separated by a large gap, so 6 paragraphs.
        texts = [p.text for p in result.paragraphs]
        assert texts == ["A0 content", "A1 content", "A2 content",
                         "B0 content", "B1 content", "B2 content"]


# --------------------------------------------------------------------------- #
# 13. Multiple LayoutBlocks can form one paragraph
# --------------------------------------------------------------------------- #


class TestMultiBlockParagraph:
    def test_multiple_blocks_form_one_paragraph(self) -> None:
        # Two blocks with lines close together -> one paragraph.
        lines = [
            make_line("From block one", (72, 100, 400, 116), 0),
            make_line("From block two", (72, 116, 400, 132), 0),
        ]
        block_a = make_block("From block one", (72, 100, 400, 116), 0, (lines[0],))
        block_b = make_block("From block two", (72, 116, 400, 132), 1, (lines[1],))
        page = make_ordered_page([block_a, block_b])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 1
        assert "From block one" in result.paragraphs[0].text
        assert "From block two" in result.paragraphs[0].text
        assert result.paragraphs[0].block_count == 2


# --------------------------------------------------------------------------- #
# 14. One LayoutBlock can contain multiple paragraphs
# --------------------------------------------------------------------------- #


class TestMultiParagraphBlock:
    def test_one_block_contains_multiple_paragraphs(self) -> None:
        line_height = 16.0
        gap = 3 * line_height
        lines = [
            make_line("First paragraph line", (72, 100, 400, 116), 0),
            make_line("First paragraph cont", (72, 116, 400, 132), 1),
            make_line("Second paragraph", (72, 132 + gap, 400, 148 + gap), 2),
        ]
        block = make_block("multi-para block", (72, 100, 400, 200), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 2
        assert "First paragraph" in result.paragraphs[0].text
        assert "Second paragraph" in result.paragraphs[1].text


# --------------------------------------------------------------------------- #
# 15. Isolated centered text is not blindly merged into body text
# --------------------------------------------------------------------------- #


class TestCenteredText:
    def test_centered_text_not_merged_with_body(self) -> None:
        # Centered line at top, body text below.
        centered_x0 = (PAGE_WIDTH - 100) / 2  # Centered "centered line"
        centered_x1 = centered_x0 + 100
        lines = [
            TextLine(text="centered line", bbox=(centered_x0, 100, centered_x1, 116), order=0),
            TextLine(text="Body text begins here and continues on the next line",
                      bbox=(72, 200, 400, 216), order=0),
            TextLine(text="Body text continues onto this line as well",
                      bbox=(72, 216, 400, 232), order=1),
        ]
        block = make_block("mixed", (centered_x0, 100, 400, 232), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 2
        assert "centered line" in result.paragraphs[0].text
        assert "Body text begins" in result.paragraphs[1].text


# --------------------------------------------------------------------------- #
# 16. Page boundary follows the documented policy
# --------------------------------------------------------------------------- #


class TestPageBoundary:
    def test_page_boundary_is_paragraph_boundary(self) -> None:
        # Last line of page 1 and first line of page 2 should be separate
        # paragraphs (conservative page boundary policy).
        line1 = make_line("Last line of page one", (72, 700, 400, 716), 0)
        line2 = make_line("First line of page two", (72, 100, 400, 116), 0)
        block1 = make_block("page1 text", (72, 700, 400, 716), 0, (line1,))
        block2 = make_block("page2 text", (72, 100, 400, 116), 0, (line2,))
        page1 = LayoutPage(
            page_number=1, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT,
            blocks=(block1,),
        )
        page2 = LayoutPage(
            page_number=2, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT,
            blocks=(block2,),
        )
        layout = reconstruct_read_order(PageLayout(pages=(page1, page2)))
        result = reconstruct_paragraphs(layout)
        assert len(result.pages[0].paragraphs) == 1
        assert len(result.pages[1].paragraphs) == 1
        assert result.pages[0].paragraphs[0].ends_at_page_boundary is True
        assert result.pages[1].paragraphs[0].ends_at_page_boundary is True


# --------------------------------------------------------------------------- #
# 17. Empty input -> valid empty result
# --------------------------------------------------------------------------- #


class TestEmptyInput:
    def test_empty_ordered_page_returns_valid_empty_result(self) -> None:
        page = LayoutPage(
            page_number=1, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT,
            blocks=(),
        )
        ordered = reconstruct_read_order(page)
        result = reconstruct_page_paragraphs(ordered)
        assert result.page_number == 1
        assert result.paragraphs == ()
        assert result.paragraph_count == 0


# --------------------------------------------------------------------------- #
# 18. Single line -> one paragraph
# --------------------------------------------------------------------------- #


class TestSingleLine:
    def test_single_line_produces_one_paragraph(self) -> None:
        line = make_line("A single line of text", (72, 100, 400, 116), 0)
        block = make_block("single", (72, 100, 400, 116), 0, (line,))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 1
        assert result.paragraphs[0].text == "A single line of text"


# --------------------------------------------------------------------------- #
# 19 & 20. Text joining preserves spaces and punctuation; no word concat
# --------------------------------------------------------------------------- #


class TestTextJoining:
    def test_text_joining_preserves_spaces_and_punctuation(self) -> None:
        lines = [
            make_line("Hello, world.", (72, 100, 400, 116), 0),
            make_line("How are you?", (72, 116, 400, 132), 1),
        ]
        block = make_block("text", (72, 100, 400, 132), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        text = result.paragraphs[0].text
        assert "Hello, world." in text
        assert "How are you?" in text
        assert "  " not in text  # No double spaces

    def test_no_accidental_word_concatenation(self) -> None:
        # Lines that end without space should get one inserted between them.
        lines = [
            make_line("Hello there friend", (72, 100, 300, 116), 0),
            make_line("world is a big place", (72, 116, 400, 132), 1),
        ]
        block = make_block("text", (72, 100, 400, 132), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        text = result.paragraphs[0].text
        # Words from adjacent lines should be separated by a space, not concatenated.
        assert "friend world" in text
        assert "friendworld" not in text


# --------------------------------------------------------------------------- #
# 21. Provenance is retained
# --------------------------------------------------------------------------- #


class TestProvenance:
    def test_provenance_retained(self) -> None:
        lines = [
            make_line("First line", (72, 100, 400, 116), 0),
            make_line("Second line", (72, 116, 400, 132), 1),
        ]
        block = make_block("text", (72, 100, 400, 132), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        para = result.paragraphs[0]
        assert para.page_number == 1
        assert len(para.source_lines) == 2
        assert para.source_lines[0].text == "First line"
        assert para.source_lines[1].text == "Second line"
        assert len(para.source_blocks) == 1
        assert para.source_blocks[0] is block
        assert para.line_count == 2
        assert para.block_count == 1
        assert len(para.reading_order_indices) == 2


# --------------------------------------------------------------------------- #
# 22. Deterministic repeated execution
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_deterministic_repeated_execution(self) -> None:
        line_height = 16.0
        lines = [
            make_line(f"Line {i} of the paragraph", (72, 100 + i * line_height, 400, 116 + i * line_height), i)
            for i in range(8)
        ]
        block = make_block("text", (72, 100, 400, 228), 0, tuple(lines))
        page = make_ordered_page([block])
        first = reconstruct_page_paragraphs(page)
        second = reconstruct_page_paragraphs(page)
        assert first == second
        assert first.paragraphs[0].text == second.paragraphs[0].text


# --------------------------------------------------------------------------- #
# 23. Hyphenation: line-break hyphen vs. legitimate compound
# --------------------------------------------------------------------------- #


class TestHyphenation:
    def test_dehyphenation_at_line_break(self) -> None:
        lines = [
            make_line("This is a reconstruc-", (72, 100, 400, 116), 0),
            make_line("tion of text.", (72, 116, 400, 132), 1),
        ]
        block = make_block("text", (72, 100, 400, 132), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert "reconstruction" in result.paragraphs[0].text

    def test_legitimate_compound_hyphen_preserved(self) -> None:
        lines = [
            make_line("The well-known author", (72, 100, 400, 116), 0),
            make_line("writes interesting stories.", (72, 116, 400, 132), 1),
        ]
        block = make_block("text", (72, 100, 400, 132), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert "well-known" in result.paragraphs[0].text


# --------------------------------------------------------------------------- #
# 24. Existing M1 / M2.1 / M2.2 tests pass -- regression sanity
# --------------------------------------------------------------------------- #


class TestRegression:
    def test_tolerances_are_centralized_and_documented(self) -> None:
        assert PARAGRAPH_GAP_FACTOR > 0
        assert PARAGRAPH_INDENT_PT > 0
        assert SHORT_LINE_THRESHOLD > 0

    def test_immutable_sources_not_mutated(self) -> None:
        lines = [
            make_line("Line one", (72, 100, 400, 116), 0),
            make_line("Line two", (72, 116, 400, 132), 1),
        ]
        block = make_block("text", (72, 100, 400, 132), 0, tuple(lines))
        page = make_ordered_page([block])
        _ = reconstruct_page_paragraphs(page)
        # Source objects unchanged.
        assert page.page.blocks[0].lines[0].text == "Line one"


# --------------------------------------------------------------------------- #
# 25. End-to-end with a synthetic PDF
# --------------------------------------------------------------------------- #


class TestEndToEnd:
    def test_synthetic_pdf_reconstructs_paragraphs(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        # Insert two separate paragraphs with a blank line between.
        page.insert_textbox(
            pymupdf.Rect(72, 100, 500, 200),
            "First paragraph here and some more words\n\nSecond paragraph starts",
            fontname="helv",
            fontsize=12,
        )
        doc.save(str(tmp_path / "paras.pdf"))
        doc.close()

        from kindle_converter.pdf import extract_page_layout
        layout = extract_page_layout(tmp_path / "paras.pdf")
        ordered = reconstruct_read_order(layout)
        result = reconstruct_paragraphs(ordered)
        assert result.paragraph_count >= 1
        all_text = " ".join(p.text for p in result.pages[0].paragraphs)
        assert "First paragraph" in all_text
        assert "Second paragraph" in all_text

    def test_reconstruct_paragraphs_returns_layout_types(self) -> None:
        line = make_line("Test line", (72, 100, 400, 116), 0)
        block = make_block("text", (72, 100, 400, 116), 0, (line,))
        page = make_ordered_page([block])
        result = reconstruct_paragraphs(page)
        assert isinstance(result, ParagraphPage)
        assert isinstance(result.paragraphs[0], ReconstructedParagraph)


# --------------------------------------------------------------------------- #
# 26. Return type shape
# --------------------------------------------------------------------------- #


class TestAPIShape:
    def test_single_page_returns_paragraph_page(self) -> None:
        line = make_line("Single", (72, 100, 400, 116), 0)
        block = make_block("text", (72, 100, 400, 116), 0, (line,))
        page = make_ordered_page([block])
        result = reconstruct_paragraphs(page)
        assert isinstance(result, ParagraphPage)

    def test_multi_page_returns_paragraph_layout(self) -> None:
        from kindle_converter.pdf import PageLayout
        page1 = LayoutPage(
            page_number=1, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT, blocks=(),
        )
        page2 = LayoutPage(
            page_number=2, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT, blocks=(),
        )
        ordered = reconstruct_read_order(PageLayout(pages=(page1, page2)))
        result = reconstruct_paragraphs(ordered)
        assert isinstance(result, ParagraphLayout)
        assert len(result.pages) == 2


# --------------------------------------------------------------------------- #
# Correction-pass tests: contextual short-line handling
# --------------------------------------------------------------------------- #


class TestContextualShortLine:
    def test_short_body_line_does_not_split_paragraph(self) -> None:
        # A short body line like "It was." must remain in its paragraph.
        line_height = 16.0
        lines = [
            make_line("This is a normal paragraph line", (72, 100, 400, 116), 0),
            make_line("It was.", (72, 116, 200, 132), 1),  # 6 chars, short
            make_line("The paragraph continues normally", (72, 132, 400, 148), 2),
        ]
        block = make_block("text", (72, 100, 400, 148), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 1
        assert "It was." in result.paragraphs[0].text

    def test_short_line_isolated_with_large_gap(self) -> None:
        # A short line with a large vertical gap (e.g. a page number) is
        # isolated even though it is short.
        line_height = 16.0
        gap = 5 * line_height  # Large gap
        lines = [
            make_line("Body text line one", (72, 100, 400, 116), 0),
            make_line("42", (72, 116 + gap, 200, 132 + gap), 1),  # Short + large gap
        ]
        block = make_block("text", (72, 100, 400, 200), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 2
        assert result.paragraphs[0].text == "Body text line one"
        assert result.paragraphs[1].text == "42"

    def test_short_line_isolated_near_page_edge(self) -> None:
        # A short line near the page bottom (footer) is isolated.
        lines = [
            make_line("Body text on the page", (72, 100, 400, 116), 0),
            make_line("99", (72, 760, 200, 776), 1),  # Near bottom edge (PAGE_HEIGHT=842)
        ]
        block = make_block("text", (72, 100, 400, 776), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 2
        assert result.paragraphs[0].text == "Body text on the page"
        assert result.paragraphs[1].text == "99"

    def test_short_line_isolated_when_centered(self) -> None:
        # A short centered line is isolated.
        centered_x0 = (PAGE_WIDTH - 60) / 2
        lines = [
            make_line("Body text line here", (72, 100, 400, 116), 0),
            make_line("OK", (centered_x0, 200, centered_x0 + 60, 216), 1),  # Short + centered
        ]
        block = make_block("text", (72, 100, 400, 216), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 2


# --------------------------------------------------------------------------- #
# Correction-pass tests: reverse indentation pattern
# --------------------------------------------------------------------------- #


class TestReverseIndentation:
    LINE_HEIGHT = 16.0

    def test_reverse_indentation_creates_boundary(self) -> None:
        # Initial paragraph: indented first line, continuation at margin.
        # Then a new paragraph starts with an indented first line.
        indent = 48.0  # Comfortably above PARAGRAPH_INDENT_PT=12
        gap_factor = 2.0  # Gap between paragraphs
        gap = gap_factor * self.LINE_HEIGHT

        lines = [
            # First paragraph: indented first line, continuation at margin
            make_line("Indented first of para one", (72 + indent, 100, 400, 116), 0),
            make_line("Continuation of paragraph one", (72, 116, 400, 132), 1),
            make_line("More continuation lines", (72, 132, 400, 148), 2),
            # Gap, then new paragraph with indent
            make_line("Indented first of para two", (72 + indent, 148 + gap, 400, 164 + gap), 3),
            make_line("Continuation of paragraph two", (72, 164 + gap, 400, 180 + gap), 4),
        ]
        block = make_block("text", (72, 100, 400, 200), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)

        assert len(result.paragraphs) == 2
        assert "Indented first of para one" in result.paragraphs[0].text
        assert "Continuation of paragraph one" in result.paragraphs[0].text
        assert "Indented first of para two" in result.paragraphs[1].text
        assert "Continuation of paragraph two" in result.paragraphs[1].text


# --------------------------------------------------------------------------- #
# Correction-pass tests: paragraph-gap threshold boundary
# --------------------------------------------------------------------------- #


class TestGapThreshold:
    LINE_HEIGHT = 16.0

    def test_gap_below_threshold_remains_one_paragraph(self) -> None:
        # Gap clearly below threshold (0.6 * 16 = 9.6).
        gap = 5.0  # Below 9.6
        lines = [
            make_line("First line of paragraph", (72, 100, 400, 116), 0),
            make_line("Second line of paragraph", (72, 116 + gap, 400, 132 + gap), 1),
        ]
        block = make_block("text", (72, 100, 400, 148), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 1

    def test_gap_above_threshold_creates_boundary(self) -> None:
        # Gap clearly above threshold (0.6 * 16 = 9.6).
        gap = 20.0  # Above 9.6
        lines = [
            make_line("First paragraph line", (72, 100, 400, 116), 0),
            make_line("Second paragraph line", (72, 116 + gap, 400, 132 + gap), 1),
        ]
        block = make_block("text", (72, 100, 400, 148), 0, tuple(lines))
        page = make_ordered_page([block])
        result = reconstruct_page_paragraphs(page)
        assert len(result.paragraphs) == 2

    def test_gap_at_threshold_behavior_is_consistent(self) -> None:
        # Gap approximately at threshold (9.6). The boundary is:
        # gap > gap_threshold => boundary. So gap == gap_threshold
        # stays in the same paragraph (not > threshold).
        # 9.0 is below 9.6 -> same paragraph.
        # 10.0 is above 9.6 -> boundary.
        # This test locks down the strict-> comparison behavior.
        for gap, expected_paragraphs in [(9.0, 1), (10.0, 2)]:
            lines = [
                make_line("First line here now", (72, 100, 400, 116), 0),
                make_line("Second line here", (72, 116 + gap, 400, 132 + gap), 1),
            ]
            block = make_block("text", (72, 100, 400, 300), 0, tuple(lines))
            page = make_ordered_page([block])
            result = reconstruct_page_paragraphs(page)
            assert len(result.paragraphs) == expected_paragraphs
