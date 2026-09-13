"""Tests for header and footer detection (Milestone 2.5)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from kindle_converter.pdf import (
    FontFlags,
    HeaderFooterType,
    LayoutBlock,
    LayoutPage,
    PageLayout,
    ParagraphLayout,
    ParagraphPage,
    ReconstructedParagraph,
    TextLine,
    TextSpan,
    classify_header_footer_paragraphs,
    classify_paragraphs,
    detect_headers_footers,
    reconstruct_paragraphs,
    reconstruct_read_order,
)

# --------------------------------------------------------------------------- #
# Test constants
# --------------------------------------------------------------------------- #

PAGE_WIDTH = 595
PAGE_HEIGHT = 842


# --------------------------------------------------------------------------- #
# Construction helpers
# --------------------------------------------------------------------------- #

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
    page_width: float = PAGE_WIDTH,
    page_height: float = PAGE_HEIGHT,
) -> ParagraphPage:
    """A ParagraphPage wrapping the given paragraphs."""
    synced = tuple(replace(para, page_number=page_number) for para in paragraphs)
    page = LayoutPage(
        page_number=page_number,
        page_width=page_width,
        page_height=page_height,
        blocks=tuple(
            block
            for para in synced
            for block in para.source_blocks
        ),
    )
    return ParagraphPage(page=page, paragraphs=synced)


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
# 1. Basic header detection
# --------------------------------------------------------------------------- #

class TestBasicHeaderDetection:
    def test_repeated_header_text_across_three_pages(self) -> None:
        """Header text repeated at the top of three consecutive pages should be detected."""
        header_text = "The Great Gatsby"
        header_para = heading_paragraph(
            header_text,
            y0=80,  # Near top of page
            font_size=24,
            font_name="Helvetica-Bold",
            flags=FontFlags.BOLD,
        )
        
        # Create three pages with the same header
        page1 = make_page((header_para,), page_number=1)
        page2 = make_page((header_para,), page_number=2)
        page3 = make_page((header_para,), page_number=3)
        
        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)
        
        # Should detect one header candidate
        assert len(result.detected) == 1
        header = result.detected[0]
        assert header.kind == HeaderFooterType.HEADER
        assert header.text == header_text
        assert len(header.pages) == 3
        assert header.confidence >= 0.5  # Should have reasonable confidence
        
        # Verify provenance
        assert len(header.source_paragraphs) == 3
        for para in header.source_paragraphs:
            assert para.page_number in (1, 2, 3)


class TestBasicFooterDetection:
    def test_repeated_footer_text_across_three_pages(self) -> None:
        """Footer text repeated at the bottom of three consecutive pages should be detected."""
        footer_text = "Copyright 2023"
        footer_para = body_paragraph(
            footer_text,
            y0=750,  # Near bottom of page
            font_size=12,
            font_name="Helvetica",
        )
        
        # Create three pages with the same footer
        page1 = make_page((footer_para,), page_number=1)
        page2 = make_page((footer_para,), page_number=2)
        page3 = make_page((footer_para,), page_number=3)
        
        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)
        
        # Should detect one footer candidate
        assert len(result.detected) == 1
        footer = result.detected[0]
        assert footer.kind == HeaderFooterType.FOOTER
        assert footer.text == footer_text
        assert len(footer.pages) == 3
        assert footer.confidence >= 0.5
        
        # Verify provenance
        assert len(footer.source_paragraphs) == 3
        for para in footer.source_paragraphs:
            assert para.page_number in (1, 2, 3)


# --------------------------------------------------------------------------- #
# 2. Page number detection
# --------------------------------------------------------------------------- #

class TestPageNumberDetection:
    def test_numeric_page_number_sequence(self) -> None:
        """Sequence '1, 2, 3, 4' at the bottom of pages should be detected as page-number footer."""
        # Create a page with a numeric sequence at the bottom
        page1 = make_page(
            (
                body_paragraph("First page body text here", y0=100),
                body_paragraph("More content on page one", y0=200),
                body_paragraph("1", y0=780),  # Bottom of page 1
            ),
            page_number=1,
        )
        page2 = make_page(
            (
                body_paragraph("Second page body text here", y0=100),
                body_paragraph("More content on page two", y0=200),
                body_paragraph("2", y0=780),  # Bottom of page 2
            ),
            page_number=2,
        )
        page3 = make_page(
            (
                body_paragraph("Third page body text here", y0=100),
                body_paragraph("More content on page three", y0=200),
                body_paragraph("3", y0=780),  # Bottom of page 3
            ),
            page_number=3,
        )
        page4 = make_page(
            (
                body_paragraph("Fourth page body text here", y0=100),
                body_paragraph("More content on page four", y0=200),
                body_paragraph("4", y0=780),  # Bottom of page 4
            ),
            page_number=4,
        )
        
        layout = make_layout((page1, page2, page3, page4))
        result = detect_headers_footers(layout)
        
        # Varied page numbers form one page-number pattern candidate.
        assert len(result.detected) == 1
        footer = result.detected[0]
        assert footer.kind == HeaderFooterType.FOOTER
        assert len(footer.pages) == 4
        assert "page_number_sequence" in footer.reasons
        assert "page_number_pattern" in footer.reasons


# --------------------------------------------------------------------------- #
# 3. Repetition alone is insufficient
# --------------------------------------------------------------------------- #

class TestRepetitionAloneIsInsufficient:
    def test_repeated_body_text_not_classified_as_header(self) -> None:
        """Repeated body text in different positions should not be classified as header."""
        repeated_text = "This is repeated text"
        
        # Page 1: top of page
        page1 = make_page(
            (
                body_paragraph(repeated_text, y0=80),  # Top of page 1
            ),
            page_number=1,
        )
        
        # Page 2: middle of page (not header region)
        page2 = make_page(
            (
                body_paragraph(repeated_text, y0=300),  # Middle of page 2
            ),
            page_number=2,
        )
        
        # Page 3: bottom of page (not footer region)
        page3 = make_page(
            (
                body_paragraph(repeated_text, y0=600),  # Bottom of page 3
            ),
            page_number=3,
        )
        
        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)
        
        # Should not detect any headers or footers
        assert len(result.detected) == 0


# --------------------------------------------------------------------------- #
# 4. Positional consistency requirements
# --------------------------------------------------------------------------- #

class TestPositionalConsistency:
    def test_varying_vertical_positions_prevent_classification(self) -> None:
        """Same text at varying vertical positions should not be confidently classified."""
        repeated_text = "Varying position text"
        
        # Page 1: top of page
        page1 = make_page(
            (
                body_paragraph(repeated_text, y0=80),  # Top of page 1
            ),
            page_number=1,
        )
        
        # Page 2: middle of page
        page2 = make_page(
            (
                body_paragraph(repeated_text, y0=350),  # Middle of page 2
            ),
            page_number=2,
        )
        
        # Page 3: bottom of page
        page3 = make_page(
            (
                body_paragraph(repeated_text, y0=650),  # Bottom of page 3
            ),
            page_number=3,
        )
        
        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)
        
        # Should not detect any headers or footers due to inconsistent positioning
        assert len(result.detected) == 0


# --------------------------------------------------------------------------- #
# 5. Header vs footer distinction
# --------------------------------------------------------------------------- #

class TestHeaderFooterDistinction:
    def test_top_text_classified_as_header(self) -> None:
        """Text at top of page should be classified as header, not footer."""
        header_text = "Chapter Title"
        page1 = make_page(
            (
                body_paragraph(header_text, y0=80),  # Top of page
            ),
            page_number=1,
        )
        page2 = make_page(
            (
                body_paragraph(header_text, y0=80),  # Top of page
            ),
            page_number=2,
        )
        page3 = make_page(
            (
                body_paragraph(header_text, y0=80),  # Top of page
            ),
            page_number=3,
        )
        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)
        
        assert len(result.detected) == 1
        header = result.detected[0]
        assert header.kind == HeaderFooterType.HEADER
        assert header.text == header_text


class TestBottomTextClassification:
    def test_bottom_text_classified_as_footer(self) -> None:
        """Text at bottom of page should be classified as footer, not header."""
        footer_text = "Page 1"
        page1 = make_page(
            (
                body_paragraph(footer_text, y0=780),  # Bottom of page
            ),
            page_number=1,
        )
        page2 = make_page(
            (
                body_paragraph(footer_text, y0=780),  # Bottom of page
            ),
            page_number=2,
        )
        page3 = make_page(
            (
                body_paragraph(footer_text, y0=780),  # Bottom of page
            ),
            page_number=3,
        )
        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)
        
        assert len(result.detected) == 1
        footer = result.detected[0]
        assert footer.kind == HeaderFooterType.FOOTER
        assert footer.text == footer_text


# --------------------------------------------------------------------------- #
# 6. Different page sizes
# --------------------------------------------------------------------------- #

class TestDifferentPageSizes:
    def test_normalized_geometry_works(self) -> None:
        """Detector should work with different page sizes via normalized geometry."""
        # Same relative header position (~9.5% of page height) on three
        # differently sized pages; MIN_REPEATED_PAGES requires 3 pages.
        small_page = make_page(
            (
                body_paragraph("Header", y0=80),
            ),
            page_number=1,
            page_width=595,
            page_height=842,
        )
        medium_page = make_page(
            (
                body_paragraph("Header", y0=95),
            ),
            page_number=2,
            page_width=612,
            page_height=1000,
        )
        large_page = make_page(
            (
                body_paragraph("Header", y0=66),
            ),
            page_number=3,
            page_width=500,
            page_height=700,
        )

        layout = make_layout((small_page, medium_page, large_page))
        result = detect_headers_footers(layout)

        # Should detect one header candidate across all three sizes.
        assert len(result.detected) == 1
        header = result.detected[0]
        assert header.kind == HeaderFooterType.HEADER
        assert len(header.pages) == 3


class TestTwoPageDocument:
    def test_no_false_repeated_page_classification(self) -> None:
        """Two-page document should not falsely claim repeated-page evidence."""
        header_text = "Chapter One"
        page1 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=1,
        )
        page2 = make_page(
            (
                body_paragraph("Different header", y0=80),
            ),
            page_number=2,
        )
        
        layout = make_layout((page1, page2))
        result = detect_headers_footers(layout)
        
        # Should not detect any repeated headers/footers (need >=3 pages)
        assert len(result.detected) == 0


# --------------------------------------------------------------------------- #
# 8. Single-page document requirements
# --------------------------------------------------------------------------- #

class TestSinglePageDocument:
    def test_no_false_detection_on_single_page(self) -> None:
        """Single-page document should not produce false repeated-page detection."""
        header_text = "Single Page Header"
        page1 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=1,
        )
        layout = make_layout((page1,))
        result = detect_headers_footers(layout)
        
        # Should not detect repeated pages (need >=3 pages)
        assert len(result.detected) == 0


# --------------------------------------------------------------------------- #
# 9. Chapter headings should not be merged
# --------------------------------------------------------------------------- #

class TestChapterHeadings:
    def test_different_chapter_headings_not_merged(self) -> None:
        """Different chapter headings should not be merged into one repeated header."""
        page1 = make_page(
            (
                body_paragraph("Chapter 1", y0=80),
            ),
            page_number=1,
        )
        page2 = make_page(
            (
                body_paragraph("Chapter 2", y0=80),
            ),
            page_number=2,
        )
        page3 = make_page(
            (
                body_paragraph("Chapter 3", y0=80),
            ),
            page_number=3,
        )

        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)

        # Each heading occurs on only one page: no repetition evidence,
        # so nothing is classified (no merging, no single-page headers).
        assert len(result.detected) == 0


class TestOrdinaryNumericBody:
    def test_numeric_body_text_not_classified(self) -> None:
        """Body text containing numbers should not be classified as page-number footer."""
        page1 = make_page(
            (
                body_paragraph("Normal body with number 123", y0=150),
            ),
            page_number=1,
        )
        page2 = make_page(
            (
                body_paragraph("More body with number 456", y0=150),
            ),
            page_number=2,
        )
        page3 = make_page(
            (
                body_paragraph("Even more body with number 789", y0=150),
            ),
            page_number=3,
        )
        
        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)
        
        # Should not detect page-number footer
        assert len(result.detected) == 0


# --------------------------------------------------------------------------- #
# 11. Provenance preservation
# --------------------------------------------------------------------------- #

class TestProvenance:
    def test_detected_candidates_retain_source_paragraphs(self) -> None:
        """Detected candidates should retain correct source paragraphs."""
        header_text = "Preserved Header"
        page1 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=1,
        )
        page2 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=2,
        )
        page3 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=3,
        )
        
        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)
        
        assert len(result.detected) == 1
        header = result.detected[0]
        assert len(header.source_paragraphs) == 3
        # Verify all source paragraphs are from the correct pages
        page_numbers = {para.page_number for para in header.source_paragraphs}
        assert page_numbers == {1, 2, 3}


# --------------------------------------------------------------------------- #
# 12. Determinism
# --------------------------------------------------------------------------- #

class TestDeterminism:
    def test_same_input_produces_identical_output(self) -> None:
        """Detector should be deterministic - same input produces same output."""
        header_text = "Deterministic Header"
        page1 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=1,
        )
        page2 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=2,
        )
        page3 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=3,
        )
        
        layout1 = make_layout((page1, page2, page3))
        layout2 = make_layout((page1, page2, page3))
        
        result1 = detect_headers_footers(layout1)
        result2 = detect_headers_footers(layout2)
        
        assert result1.detected == result2.detected
        assert result1.pages == result2.pages
        assert result1.detected == result2.detected


# --------------------------------------------------------------------------- #
# 13. Multiple candidates per page
# --------------------------------------------------------------------------- #

class TestMultipleCandidatesPerPage:
    def test_multiple_genuine_headers_per_page(self) -> None:
        """Multiple genuine headers on the same page should be detected separately."""
        page1 = make_page(
            (
                body_paragraph("Main Title", y0=80),      # Header 1
                body_paragraph("Running Head", y0=85),   # Header 2
            ),
            page_number=1,
        )
        
        layout = make_layout((page1,))
        result = detect_headers_footers(layout)
        
        # Should not detect headers on a single page (need >=3 pages)
        assert len(result.detected) == 0


# --------------------------------------------------------------------------- #
# 14. Same text at both top and bottom
# --------------------------------------------------------------------------- #

class TestSameTextTopAndBottom:
    def test_same_text_at_top_and_bottom(self) -> None:
        """Same text at top and bottom should be treated as separate candidates."""
        repeated_text = "Running Text"
        
        page1 = make_page(
            (
                body_paragraph(repeated_text, y0=80),  # Top of page
            ),
            page_number=1,
        )
        page2 = make_page(
            (
                body_paragraph(repeated_text, y0=780),  # Bottom of page
            ),
            page_number=1,
        )
        
        layout = make_layout((page1, page2))
        result = detect_headers_footers(layout)
        
        # Should not detect (need >=3 pages, but we only have 2 pages)
        assert len(result.detected) == 0


# --------------------------------------------------------------------------- #
# 15. Empty pages
# --------------------------------------------------------------------------- #

class TestEmptyPages:
    def test_empty_pages_no_false_positives(self) -> None:
        """Pages with no content should not cause false detection."""
        empty_page = make_page((), page_number=1)
        layout = make_layout((empty_page,))
        result = detect_headers_footers(layout)
        
        # Should not detect any headers or footers
        assert len(result.detected) == 0


# --------------------------------------------------------------------------- #
# 16. Minimum repetition threshold
# --------------------------------------------------------------------------- #

class TestMinimumRepetitionThreshold:
    def test_three_pages_required(self) -> None:
        """Detector should require at least 3 pages for classification."""
        header_text = "Three Page Header"
        
        # Only two pages with header
        page1 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=1,
        )
        page2 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=2,
        )
        
        layout = make_layout((page1, page2))
        result = detect_headers_footers(layout)
        
        # Should not detect due to insufficient pages (need >=3)
        assert len(result.detected) == 0


# --------------------------------------------------------------------------- #
# 17. Confidence scoring boundaries
# --------------------------------------------------------------------------- #

class TestConfidenceScoring:
    def test_confidence_above_threshold(self) -> None:
        """Candidate with sufficient evidence should have confidence >= 0.5."""
        header_text = "High Confidence Header"
        page1 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=1,
        )
        page2 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=2,
        )
        page3 = make_page(
            (
                body_paragraph(header_text, y0=80),
            ),
            page_number=3,
        )
        
        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)
        
        assert len(result.detected) == 1
        assert result.detected[0].confidence >= 0.5


# --------------------------------------------------------------------------- #
# 18. Page-number text forms ("Page N" sequence)
# --------------------------------------------------------------------------- #

class TestPageNumberTextForms:
    def test_page_number_text_sequence(self) -> None:
        """'Page 1 / Page 2 / Page 3' in the footer form one pattern candidate."""
        page1 = make_page(
            (
                body_paragraph("First page body text here", y0=100),
                body_paragraph("Page 1", y0=780),
            ),
            page_number=1,
        )
        page2 = make_page(
            (
                body_paragraph("Second page body text here", y0=100),
                body_paragraph("Page 2", y0=780),
            ),
            page_number=2,
        )
        page3 = make_page(
            (
                body_paragraph("Third page body text here", y0=100),
                body_paragraph("Page 3", y0=780),
            ),
            page_number=3,
        )

        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)

        assert len(result.detected) == 1
        footer = result.detected[0]
        assert footer.kind == HeaderFooterType.FOOTER
        assert set(footer.pages) == {1, 2, 3}
        assert "page_number_sequence" in footer.reasons


# --------------------------------------------------------------------------- #
# 19. Repetition-position interaction and position-only rejection
# --------------------------------------------------------------------------- #

class TestPositionAloneIsInsufficient:
    def test_unique_text_near_edges_not_detected(self) -> None:
        """Unique (non-repeated) text near an edge is not page furniture."""
        page1 = make_page((body_paragraph("Unique top text", y0=80),), page_number=1)
        page2 = make_page((body_paragraph("Other top text", y0=80),), page_number=2)
        page3 = make_page((body_paragraph("Third top text", y0=80),), page_number=3)
        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)
        assert len(result.detected) == 0

    def test_lone_footer_text_not_detected(self) -> None:
        """A lone bottom-of-page paragraph on one page is not a footer."""
        page1 = make_page((body_paragraph("Lone footer", y0=780),), page_number=1)
        layout = make_layout((page1,))
        result = detect_headers_footers(layout)
        assert len(result.detected) == 0


# --------------------------------------------------------------------------- #
# 20. Positional consistency and near-threshold behavior
# --------------------------------------------------------------------------- #

class TestPositionalConsistency:
    def test_repeat_with_vertical_drift_rejected(self) -> None:
        """Same text drifting more than the tolerance is ambiguous: rejected."""
        page1 = make_page((body_paragraph("Drifting Header", y0=40),), page_number=1)
        page2 = make_page((body_paragraph("Drifting Header", y0=80),), page_number=2)
        page3 = make_page((body_paragraph("Drifting Header", y0=120),), page_number=3)
        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)
        assert len(result.detected) == 0

    def test_repeat_within_tolerance_accepted(self) -> None:
        """Same text within the tolerance band is accepted as a header."""
        page1 = make_page((body_paragraph("Stable Header", y0=80),), page_number=1)
        page2 = make_page((body_paragraph("Stable Header", y0=84),), page_number=2)
        page3 = make_page((body_paragraph("Stable Header", y0=76),), page_number=3)
        layout = make_layout((page1, page2, page3))
        result = detect_headers_footers(layout)
        assert len(result.detected) == 1
        assert result.detected[0].kind == HeaderFooterType.HEADER

    def test_threshold_boundary_header_region(self) -> None:
        """At the region boundary the candidate is in-region; past it is not."""
        in_pages = tuple(
            make_page((body_paragraph("Boundary Header", y0=126),), page_number=n)
            for n in (1, 2, 3)
        )
        in_result = detect_headers_footers(make_layout(in_pages))
        assert len(in_result.detected) == 1
        assert in_result.detected[0].kind == HeaderFooterType.HEADER
        out_pages = tuple(
            make_page((body_paragraph("Boundary Header", y0=140),), page_number=n)
            for n in (1, 2, 3)
        )
        out_result = detect_headers_footers(make_layout(out_pages))
        assert len(out_result.detected) == 0


# --------------------------------------------------------------------------- #
# 21. Reading order, case normalization and multiple candidates
# --------------------------------------------------------------------------- #

class TestReadingOrderPreserved:
    def test_detection_preserves_input_order(self) -> None:
        """Detection must not reorder M2.3 paragraphs or pages."""
        pages = (
            make_page(
                (
                    body_paragraph("Running Title", y0=80),
                    body_paragraph("Body one", y0=300),
                    body_paragraph("1", y0=780),
                ),
                page_number=1,
            ),
            make_page(
                (
                    body_paragraph("Running Title", y0=80),
                    body_paragraph("Body two", y0=300),
                    body_paragraph("2", y0=780),
                ),
                page_number=2,
            ),
            make_page(
                (
                    body_paragraph("Running Title", y0=80),
                    body_paragraph("Body three", y0=300),
                    body_paragraph("3", y0=780),
                ),
                page_number=3,
            ),
        )
        layout = make_layout(pages)
        before = tuple(
            tuple(para.text for para in page.paragraphs) for page in layout.pages
        )
        result = detect_headers_footers(layout)
        after = tuple(
            tuple(para.text for para in page.paragraphs) for page in layout.pages
        )
        assert after == before
        assert len(result.detected) == 2

    def test_case_insensitive_repetition(self) -> None:
        """'THE GREAT GATSBY' and 'The Great Gatsby' compare as one header."""
        pages = tuple(
            make_page((body_paragraph(text, y0=80),), page_number=n)
            for n, text in (
                (1, "THE GREAT GATSBY"),
                (2, "The Great Gatsby"),
                (3, "the great gatsby"),
            )
        )
        result = detect_headers_footers(make_layout(pages))
        assert len(result.detected) == 1
        assert result.detected[0].kind == HeaderFooterType.HEADER


class TestMultipleCandidates:
    def test_two_genuine_headers_detected(self) -> None:
        """Two distinct repeated header lines are detected independently."""
        pages = tuple(
            make_page(
                (
                    body_paragraph("Book Title", y0=60),
                    body_paragraph("Chapter 4", y0=100),
                    body_paragraph(f"Body text on page {n}", y0=300),
                ),
                page_number=n,
            )
            for n in (1, 2, 3)
        )
        result = detect_headers_footers(make_layout(pages))
        texts = sorted(item.text for item in result.detected)
        assert texts == ["Book Title", "Chapter 4"]
        assert all(item.kind == HeaderFooterType.HEADER for item in result.detected)


# --------------------------------------------------------------------------- #
# 22. Same text at top and bottom (independent positions)
# --------------------------------------------------------------------------- #

class TestSameTextTopAndBottomIndependent:
    def test_top_and_bottom_form_separate_candidates(self) -> None:
        """Identical text at top and bottom yields one HEADER and one FOOTER."""
        pages = tuple(
            make_page(
                (
                    body_paragraph("Running Text", y0=80),
                    body_paragraph(f"Middle body {n}", y0=400),
                    body_paragraph("Running Text", y0=780),
                ),
                page_number=n,
            )
            for n in (1, 2, 3)
        )
        result = detect_headers_footers(make_layout(pages))
        assert len(result.detected) == 2
        kinds = sorted(item.kind for item in result.detected)
        assert kinds == [HeaderFooterType.FOOTER, HeaderFooterType.HEADER]


# --------------------------------------------------------------------------- #
# 23. Public API surface and paragraph-level classification
# --------------------------------------------------------------------------- #

class TestPublicApiSurface:
    def test_constants_exported_with_spec_defaults(self) -> None:
        import kindle_converter.pdf as pdf_api

        assert pdf_api.MIN_REPEATED_PAGES == 3
        assert pdf_api.HEADER_REGION_FRACTION == 0.15
        assert pdf_api.FOOTER_REGION_FRACTION == 0.15
        assert pdf_api.HEADER_POSITION_TOLERANCE_FRACTION == 0.03
        assert pdf_api.FOOTER_POSITION_TOLERANCE_FRACTION == 0.03
        for name in (
            "detect_headers_footers",
            "classify_header_footer_paragraphs",
            "DetectedHeaderFooter",
            "HeaderFooterLayout",
            "HeaderFooterPage",
            "HeaderFooterType",
            "ClassifiedHeaderFooterParagraph",
            "MIN_REPEATED_PAGES",
            "HEADER_REGION_FRACTION",
            "FOOTER_REGION_FRACTION",
        ):
            assert name in pdf_api.__all__

    def test_invalid_source_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            detect_headers_footers(object())  # type: ignore[arg-type]

    def test_single_page_source_accepted(self) -> None:
        pages = tuple(
            make_page((body_paragraph("Lone Header", y0=80),), page_number=n)
            for n in (1, 2, 3)
        )
        page_only = detect_headers_footers(pages[0])
        assert page_only.detected == ()
        assert [p.page_number for p in page_only.pages] == [1]

    def test_classify_maps_every_paragraph_in_order(self) -> None:
        paras = (
            body_paragraph("Running Title", y0=80),
            body_paragraph("Body copy here", y0=300),
        )
        pages = tuple(make_page(paras, page_number=n) for n in (1, 2, 3))
        layout = make_layout(pages)
        detected_layout, classified = classify_header_footer_paragraphs(layout)
        assert len(classified) == 6
        assert [c.paragraph.text for c in classified] == [
            "Running Title", "Body copy here",
            "Running Title", "Body copy here",
            "Running Title", "Body copy here",
        ]
        assert [c.kind for c in classified] == [
            HeaderFooterType.HEADER, None,
            HeaderFooterType.HEADER, None,
            HeaderFooterType.HEADER, None,
        ]
        assert classified[0].score == detected_layout.detected[0].confidence
        assert classified[1].score == 0.0
        assert classified[1].reasons == ()


# --------------------------------------------------------------------------- #
# 24. Page-number forms and conservative numeric handling
# --------------------------------------------------------------------------- #

class TestPageNumberForms:
    @pytest.mark.parametrize(
        "texts",
        [
            ("- 1 -", "- 2 -", "- 3 -"),
            ("1 / 250", "2 / 250", "3 / 250"),
            ("Page 1 of 250", "Page 2 of 250", "Page 3 of 250"),
        ],
    )
    def test_page_number_forms_detected_as_footer(
        self, texts: tuple[str, str, str]
    ) -> None:
        pages = tuple(
            make_page(
                (
                    body_paragraph(f"Body on page {n}", y0=200),
                    body_paragraph(text, y0=780),
                ),
                page_number=n,
            )
            for n, text in zip((1, 2, 3), texts)
        )
        result = detect_headers_footers(make_layout(pages))
        assert len(result.detected) == 1
        footer = result.detected[0]
        assert footer.kind == HeaderFooterType.FOOTER
        assert "page_number_sequence" in footer.reasons
        assert footer.confidence >= 0.5

    def test_non_monotonic_numbers_not_merged(self) -> None:
        """Arbitrary changing numbers (3, 1, 2) are never generalized."""
        pages = tuple(
            make_page((body_paragraph(text, y0=780),), page_number=n)
            for n, text in ((1, "3"), (2, "1"), (3, "2"))
        )
        result = detect_headers_footers(make_layout(pages))
        assert len(result.detected) == 0

    def test_year_near_bottom_has_no_page_number_reasons(self) -> None:
        """A repeated '1947' gets no page-number evidence upgrade."""
        pages = tuple(
            make_page(
                (
                    body_paragraph(f"Body on page {n}", y0=200),
                    body_paragraph("1947", y0=780),
                ),
                page_number=n,
            )
            for n in (1, 2, 3)
        )
        result = detect_headers_footers(make_layout(pages))
        for item in result.detected:
            assert "page_number_pattern" not in item.reasons
            assert "page_number_sequence" not in item.reasons


# --------------------------------------------------------------------------- #
# 25. Long body text is never furniture
# --------------------------------------------------------------------------- #

class TestLongBodyTextRejected:
    def test_very_long_repeated_edge_text_rejected(self) -> None:
        long_text = "Lorem ipsum dolor sit amet, " * 12  # >160 chars
        assert len(long_text) > 160
        pages = tuple(
            make_page((body_paragraph(long_text, y0=80),), page_number=n)
            for n in (1, 2, 3)
        )
        result = detect_headers_footers(make_layout(pages))
        assert len(result.detected) == 0


# --------------------------------------------------------------------------- #
# End of tests
# --------------------------------------------------------------------------- #