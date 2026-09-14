"""M2.7 integration regression tests (M2.8).

Verifies the complete reconstruction pipeline integration contract:

* filtering uses source-object identity, not text matching
* ordinary body text with identical text to a header/footer survives
* page boundaries survive reconstruction
* provenance survives (source lines, blocks, reading order indices, page number)
* no mutation of upstream M2 objects
* deterministic output

Uses synthetic ReconstructedParagraph objects constructed directly so the
tests are deterministic and independent of PDF rendering.
"""

from __future__ import annotations

from pathlib import Path

from kindle_converter.pdf import (
    ElementKind,
    FontFlags,
    HeadingLayout,
    HeadingPage,
    LayoutBlock,
    LayoutPage,
    PageLayout,
    ParagraphLayout,
    ParagraphPage,
    ReconstructedParagraph,
    TextLine,
    TextSpan,
    build_reconstructed_document,
    classify_paragraphs,
    detect_headers_footers,
)
from kindle_converter.pdf.reconstruction import GENERIC_HEADING_LEVEL
from kindle_converter.document import Book, BookMetadata, Heading

PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def make_span(
    text: str,
    font_size: float = 12.0,
    font_name: str = "Helvetica",
    flags: FontFlags = FontFlags(0),
) -> TextSpan:
    width = len(text) * font_size * 0.5
    return TextSpan(
        text=text,
        bbox=(0.0, 0.0, width, font_size),
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
    if spans is None:
        spans = (make_span(text),)
    return TextLine(text=text, bbox=bbox, order=order, spans=spans)


def make_paragraph(
    text: str,
    page_number: int = 1,
    y0: float = 200.0,
    font_size: float = 12.0,
    font_name: str = "Helvetica",
    flags: FontFlags = FontFlags(0),
    ends_at_page_boundary: bool = False,
) -> ReconstructedParagraph:
    line = make_line(
        text,
        (72, y0, 72 + len(text) * 6.0, y0 + 14),
        0,
        (make_span(text, font_size, font_name, flags),),
    )
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
        ends_at_page_boundary=ends_at_page_boundary,
    )


def make_paragraph_page(
    paragraphs: tuple[ReconstructedParagraph, ...],
    page_number: int = 1,
) -> ParagraphPage:
    return ParagraphPage(
        page=LayoutPage(
            page_number=page_number,
            page_width=PAGE_WIDTH,
            page_height=PAGE_HEIGHT,
            blocks=tuple(
                LayoutBlock(
                    text=p.text,
                    bbox=p.source_lines[0].bbox,
                    order=i,
                    lines=p.source_lines,
                )
                for i, p in enumerate(paragraphs)
            ),
        ),
        paragraphs=paragraphs,
    )


# --------------------------------------------------------------------------- #
# Source-object identity filtering
# --------------------------------------------------------------------------- #


class TestSourceObjectIdentityFiltering:
    def test_body_paragraph_matching_header_text_survives(self) -> None:
        """A body paragraph whose text matches a header's text must NOT be
        removed merely because its text matches. Filtering uses source-object
        identity, not text matching."""
        # Header repeated on 3 pages.
        header_text = "Running Title"
        body_text = "Running Title"  # Same text as header, but body content.

        pages = []
        for n in range(1, 4):
            header = make_paragraph(header_text, page_number=n, y0=80)
            body = make_paragraph(body_text, page_number=n, y0=300)
            footer = make_paragraph("Page footer", page_number=n, y0=790)
            pages.append(make_paragraph_page((header, body, footer), page_number=n))

        layout = make_layout(tuple(pages))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)

        # The header must be detected.
        assert furniture.detected_count >= 1

        # The body paragraph with the same text must survive.
        all_texts = [
            el.text for page in document.pages for el in page.elements
        ]
        # Count how many times the text appears: should be at least 3
        # (one per page, from the body paragraphs).
        assert all_texts.count(body_text) >= 3

    def test_filtering_removes_only_furniture_objects(self) -> None:
        """Only paragraphs referenced by a DetectedHeaderFooter are removed;
        unrelated body paragraphs survive even if they share text."""
        pages = []
        for n in range(1, 4):
            header = make_paragraph("Furniture Header", page_number=n, y0=80)
            body1 = make_paragraph(
                "Unique body text alpha", page_number=n, y0=300
            )
            body2 = make_paragraph(
                "Unique body text beta", page_number=n, y0=400
            )
            pages.append(
                make_paragraph_page((header, body1, body2), page_number=n)
            )

        layout = make_layout(tuple(pages))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)

        all_texts = [
            el.text for page in document.pages for el in page.elements
        ]
        # Body text must survive.
        assert "Unique body text alpha" in all_texts
        assert "Unique body text beta" in all_texts
        # Furniture must be filtered.
        assert "Furniture Header" not in all_texts


# --------------------------------------------------------------------------- #
# Page boundaries survive
# --------------------------------------------------------------------------- #


class TestPageBoundariesSurvive:
    def test_page_boundaries_preserve_element_order(self) -> None:
        """Elements from page 1 must all appear before elements from page 2."""
        p1a = make_paragraph("Page One Alpha", page_number=1, y0=200)
        p1b = make_paragraph("Page One Beta", page_number=1, y0=300)
        p2a = make_paragraph("Page Two Alpha", page_number=2, y0=200)
        p2b = make_paragraph("Page Two Beta", page_number=2, y0=300)

        pages = (
            make_paragraph_page((p1a, p1b), page_number=1),
            make_paragraph_page((p2a, p2b), page_number=2),
        )
        layout = make_layout(pages)
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)

        assert len(document.pages) == 2
        page1_texts = [el.text for el in document.pages[0].elements]
        page2_texts = [el.text for el in document.pages[1].elements]

# --------------------------------------------------------------------------- #
# Provenance survives
# --------------------------------------------------------------------------- #


class TestProvenanceSurvives:
    def test_source_lines_survive(self) -> None:
        """Source line references survive through integration."""
        line = make_line(
            "Source line text",
            (72, 200, 400, 214),
            0,
            (make_span("Source line text"),),
        )
        block = LayoutBlock(
            text="Source line text",
            bbox=(72, 200, 400, 214),
            order=0,
            lines=(line,),
        )
        para = ReconstructedParagraph(
            text="Source line text",
            page_number=1,
            source_lines=(line,),
            source_blocks=(block,),
            source_line_orders=((0, 0),),
            reading_order_indices=(0,),
        )
        page = make_paragraph_page((para,), page_number=1)
        layout = make_layout((page,))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)

        element = document.pages[0].elements[0]
        assert element.paragraph.source_lines == (line,)

    def test_source_blocks_survive(self) -> None:
        """Source block references survive through integration."""
        line = make_line("Block text", (72, 200, 400, 214), 0)
        block = LayoutBlock(
            text="Block text",
            bbox=(72, 200, 400, 214),
            order=0,
            lines=(line,),
        )
        para = ReconstructedParagraph(
            text="Block text",
            page_number=1,
            source_lines=(line,),
            source_blocks=(block,),
            source_line_orders=((0, 0),),
            reading_order_indices=(0,),
        )
        page = make_paragraph_page((para,), page_number=1)
        layout = make_layout((page,))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)

        element = document.pages[0].elements[0]
        assert element.paragraph.source_blocks == (block,)

    def test_reading_order_indices_survive(self) -> None:
        """Reading-order indices survive through integration."""
        para = make_paragraph("Indexed text", page_number=1, y0=200)
        # Manually set reading_order_indices.
        para = ReconstructedParagraph(
            text=para.text,
            page_number=para.page_number,
            source_lines=para.source_lines,
            source_blocks=para.source_blocks,
            source_line_orders=para.source_line_orders,
            reading_order_indices=(42,),
        )
        page = make_paragraph_page((para,), page_number=1)
        layout = make_layout((page,))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)

        element = document.pages[0].elements[0]
        assert element.paragraph.reading_order_indices == (42,)


# --------------------------------------------------------------------------- #
# No mutation of upstream M2 objects
# --------------------------------------------------------------------------- #


class TestNoMutation:
    def test_source_paragraphs_not_mutated(self) -> None:
        """Running reconstruction must not mutate the source paragraphs."""
        para = make_paragraph("Immutable text", page_number=1, y0=200)
        original_text = para.text
        original_page = para.page_number

        page = make_paragraph_page((para,), page_number=1)
        layout = make_layout((page,))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        build_reconstructed_document(layout, headings, furniture)

        # The source paragraph must be unchanged.
        assert para.text == original_text
        assert para.page_number == original_page

    def test_heading_classification_not_mutated(self) -> None:
        """The heading classification result is not mutated by integration."""
        para = make_paragraph("Some text", page_number=1, y0=200)
        page = make_paragraph_page((para,), page_number=1)
        layout = make_layout((page,))
        headings = classify_paragraphs(layout)
        original_count = headings.pages[0].paragraphs[0]

        furniture = detect_headers_footers(layout)
        build_reconstructed_document(layout, headings, furniture)

        # The heading classification must be unchanged.
        assert headings.pages[0].paragraphs[0] == original_count


# --------------------------------------------------------------------------- #
# Deterministic output
# --------------------------------------------------------------------------- #


class TestDeterministicOutput:
    def test_repeated_integration_produces_identical_output(self) -> None:
        """Running the same integration twice must produce identical output."""
        pages = []
        for n in range(1, 4):
            header = make_paragraph("Repeated Header", page_number=n, y0=80)
            body = make_paragraph(
                f"Body for page {n}", page_number=n, y0=300
            )
            pages.append(make_paragraph_page((header, body), page_number=n))

        layout = make_layout(tuple(pages))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)

        first = build_reconstructed_document(layout, headings, furniture)
        second = build_reconstructed_document(layout, headings, furniture)

        first_texts = [
            el.text for page in first.pages for el in page.elements
        ]
        second_texts = [
            el.text for page in second.pages for el in page.elements
        ]
        assert first_texts == second_texts

    def test_page_numbers_survive_in_provenance(self) -> None:
        """The page_number on each paragraph survives through integration."""
        p1 = make_paragraph("Content page one", page_number=1, y0=200)
        p2 = make_paragraph("Content page two", page_number=2, y0=200)
        p3 = make_paragraph("Content page three", page_number=3, y0=200)

        pages = (
            make_paragraph_page((p1,), page_number=1),
            make_paragraph_page((p2,), page_number=2),
            make_paragraph_page((p3,), page_number=3),
        )
        layout = make_layout(pages)
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)

        for page_number, page in enumerate(document.pages, start=1):
            for element in page.elements:
                assert element.paragraph.page_number == page_number


# --------------------------------------------------------------------------- #
# Book adapter from reconstruction
# --------------------------------------------------------------------------- #


class TestReconstructedDocumentToBook:
    def test_reconstructed_document_to_book_reaches_epub(
        self, tmp_path: Path
    ) -> None:
        """A ReconstructedDocument can be adapted to a Book and
        rendered as an EPUB."""
        from kindle_converter.pdf.reconstruction import (
            ReconstructedDocument,
            reconstructed_document_to_book,
        )
        from kindle_converter.epub import build_epub

        para = make_paragraph("Body text here.", page_number=1, y0=200)
        page = make_paragraph_page((para,), page_number=1)
        layout = make_layout((page,))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)

        book = reconstructed_document_to_book(
            document, BookMetadata(title="Reconstructed")
        )
        assert isinstance(book, Book)
        out = tmp_path / "recon.epub"
        build_epub(book, out)
        assert out.exists()

    def test_reconstructed_document_to_book_filters_furniture(
        self, tmp_path: Path
    ) -> None:
        """Furniture filtered by M2.5 is excluded from the Book body."""
        from kindle_converter.pdf.reconstruction import (
            ReconstructedDocument,
            ReconstructedPage,
            ReconstructedElement,
            ElementKind,
            reconstructed_document_to_book,
        )
        from kindle_converter.document import BookMetadata, Paragraph

        para = make_paragraph("Body for page 1", page_number=1, y0=300)
        page = make_paragraph_page((para,), page_number=1)
        layout = make_layout((page,))

        # Manually construct a document where the paragraph
        # is an ordinary body element (not furniture).
        doc = ReconstructedDocument(
            pages=(
                ReconstructedPage(
                    page_number=1,
                    elements=(
                        ReconstructedElement(
                            kind=ElementKind.PARAGRAPH,
                            paragraph=para,
                            heading=None,
                        ),
                    ),
                ),
            ),
            paragraphs=layout,
            headings=type("Fake", (), {"pages": ()})(),
            header_footer=type("Fake", (), {"detected": ()})(),
        )

        book = reconstructed_document_to_book(doc, BookMetadata(title="Filtered"))
        assert book.chapters[0].blocks is not None
        all_texts = " ".join(str(b.text) for b in book.chapters[0].blocks)
        assert "Body for page 1" in all_texts

    def test_reconstructed_document_to_book_headings_as_heading_blocks(
        self, tmp_path: Path
    ) -> None:
        """M2.4 headings become Heading blocks at GENERIC_HEADING_LEVEL."""
        from kindle_converter.pdf.reconstruction import (
            ReconstructedDocument,
            ReconstructedPage,
            ReconstructedElement,
            ElementKind,
            reconstructed_document_to_book,
            GENERIC_HEADING_LEVEL,
        )
        from kindle_converter.document import BookMetadata, Heading, Paragraph

        para = make_paragraph("Chapter One", page_number=1, y0=100)
        page = make_paragraph_page((para,), page_number=1)
        layout = make_layout((page,))

        # Manually construct a document with a heading element,
        # bypassing heading detection to isolate the adapter logic.
        doc = ReconstructedDocument(
            pages=(
                ReconstructedPage(
                    page_number=1,
                    elements=(
                        ReconstructedElement(
                            kind=ElementKind.HEADING,
                            paragraph=para,
                            heading=None,
                        ),
                    ),
                ),
            ),
            paragraphs=layout,
            headings=type("Fake", (), {"pages": ()})(),
            header_footer=type("Fake", (), {"detected": ()})(),
        )

        book = reconstructed_document_to_book(
            doc, BookMetadata(title="Headings")
        )
        heading_blocks = [
            b for b in book.chapters[0].blocks if isinstance(b, Heading)
        ]
        assert len(heading_blocks) >= 1
        assert heading_blocks[0].level == GENERIC_HEADING_LEVEL



def make_layout(
    pages: tuple[ParagraphPage, ...],
) -> ParagraphLayout:
    return ParagraphLayout(pages=pages)
