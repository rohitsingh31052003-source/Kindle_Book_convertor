"""Tests for explicit page-number removal (Milestone 2.10).

These tests construct M2.7 ReconstructedDocument objects directly
(from ReconstructedParagraph/ReconstructedElement) with real page sizes on
the attached paragraph layout, and use one synthetic PDF for the
end-to-end M2.7/M2.9 integration case.

M2.10 consumes the M2.7 integrated representation; it never re-extracts,
re-sorts, re-runs paragraph/heading detection, or mutates its input. The
documented M2.5 gap under test: page numbers that M2.5 conservatively
*does not* classify as furniture (a two-page ``1, 2`` sequence, or a lone
explicit ``Page 7 of 20``) survive into the integrated body and are the
M2.10 layer's job to remove.
"""

from __future__ import annotations

import zipfile

import pymupdf
import pytest

from kindle_converter import convert_pdf_to_epub
from kindle_converter.document import BookMetadata
from kindle_converter.epub import build_epub
from kindle_converter.pdf import (
    DetectedHeading,
    ElementKind,
    FontFlags,
    LayoutBlock,
    LayoutPage,
    ParagraphLayout,
    ParagraphPage,
    ReconstructedParagraph,
    TextLine,
    TextSpan,
    PageNumberLocation,
    detect_chapters,
    extract_page_layout,
    reconstruct_layout,
    remove_page_numbers,
)
from kindle_converter.pdf.reconstruction import (
    ReconstructedDocument,
    ReconstructedElement,
    ReconstructedPage,
    reconstructed_document_to_book,
)

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0

# Footer band:   bottom_fraction >= 1 - 0.15 == 0.85  ->  y1 >= 715.7
# Header band:   top_fraction    <= 0.15             ->  y0 <= 126.3
FOOTER_Y0 = 800.0
HEADER_Y0 = 30.0
BODY_Y0 = 300.0

BODY_LINE = (
    "It was a bright cold day in April and the clocks were striking "
    "thirteen and the weather was cold across the country side here."
)
# --------------------------------------------------------------------------- #
# Construction helpers
# --------------------------------------------------------------------------- #


def make_span(
    text: str,
    font_size: float = 12.0,
    font_name: str = "Helvetica",
    flags: FontFlags = FontFlags(0),
    bbox: tuple[float, float, float, float] | None = None,
) -> TextSpan:
    if bbox is None:
        bbox = (0.0, 0.0, len(text) * font_size * 0.5, font_size)
    return TextSpan(
        text=text, bbox=bbox, font_name=font_name,
        font_size=font_size, font_flags=flags,
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
    y0: float = BODY_Y0,
    x0: float = 72.0,
    line_height: float = 14.0,
    font_size: float = 12.0,
    font_name: str = "Helvetica",
    flags: FontFlags = FontFlags(0),
) -> ReconstructedParagraph:
    line = make_line(
        text,
        (x0, y0, x0 + len(text) * 6.0, y0 + line_height),
        0,
        (make_span(text, font_size=font_size, font_name=font_name, flags=flags),),
    )
    return ReconstructedParagraph(
        text=text,
        page_number=page_number,
        source_lines=(line,),
        source_blocks=(LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,)),),
        source_line_orders=((0, 0),),
        reading_order_indices=(0,),
    )


def footer_paragraph(
    text: str, page_number: int = 1, y0: float = FOOTER_Y0
) -> ReconstructedParagraph:
    return make_paragraph(text, page_number=page_number, y0=y0)


def header_paragraph(
    text: str, page_number: int = 1, y0: float = HEADER_Y0
) -> ReconstructedParagraph:
    return make_paragraph(text, page_number=page_number, y0=y0)


def body_paragraph(
    text: str, page_number: int = 1, y0: float = BODY_Y0
) -> ReconstructedParagraph:
    return make_paragraph(text, page_number=page_number, y0=y0)


def heading_paragraph(
    text: str, page_number: int = 1, y0: float = 120.0,
    font_size: float = 24.0,
) -> ReconstructedParagraph:
    para = make_paragraph(
        text, page_number=page_number, y0=y0, line_height=font_size,
        font_size=font_size, font_name="Helvetica-Bold", flags=FontFlags.BOLD,
    )
    return ReconstructedParagraph(
        text=para.text, page_number=para.page_number,
        source_lines=para.source_lines, source_blocks=para.source_blocks,
        source_line_orders=para.source_line_orders,
        reading_order_indices=para.reading_order_indices,
    )


def element(paragraph: ReconstructedParagraph, is_heading: bool = False) -> ReconstructedElement:
    if is_heading:
        return ReconstructedElement(
            kind=ElementKind.HEADING,
            paragraph=paragraph,
            heading=DetectedHeading(
                paragraph=paragraph, score=3.0, reasons=("test_fixture",)
            ),
        )
    return ReconstructedElement(
        kind=ElementKind.PARAGRAPH, paragraph=paragraph, heading=None
    )
def make_page(
    elements: tuple[ReconstructedElement, ...], page_number: int = 1
) -> ReconstructedPage:
    return ReconstructedPage(page_number=page_number, elements=elements)


def make_layout_for(
    pages: tuple[ReconstructedPage, ...],
    page_size: tuple[float, float] = (PAGE_WIDTH, PAGE_HEIGHT),
    page_sizes: dict[int, tuple[float, float]] | None = None,
) -> ParagraphLayout:
    """A minimal paragraph layout carrying real per-page dimensions.

    ``_page_dimensions`` only reads ``page.page_width/page_height`` from the
    layout, so empty paragraph tuples are sufficient for the fixture.
    """
    entries: list[ParagraphPage] = []
    for page in pages:
        if page_sizes is not None and page.page_number in page_sizes:
            width, height = page_sizes[page.page_number]
        else:
            width, height = page_size
        entries.append(
            ParagraphPage(
                page=LayoutPage(
                    page_number=page.page_number,
                    page_width=width,
                    page_height=height,
                ),
                paragraphs=(),
            )
        )
    return ParagraphLayout(pages=tuple(entries))


def make_document(
    pages: tuple[ReconstructedPage, ...],
    page_size: tuple[float, float] = (PAGE_WIDTH, PAGE_HEIGHT),
    page_sizes: dict[int, tuple[float, float]] | None = None,
    with_layout: bool = True,
) -> ReconstructedDocument:
    return ReconstructedDocument(
        pages=pages,
        paragraphs=make_layout_for(pages, page_size=page_size, page_sizes=page_sizes)
        if with_layout
        else None,
    )


def page_with_footer(
    value: int,
    page_number: int,
    body_text: str | None = None,
    y0: float = FOOTER_Y0,
) -> ReconstructedPage:
    elements = []
    if body_text is not None:
        elements.append(element(body_paragraph(body_text, page_number=page_number)))
    elements.append(element(footer_paragraph(str(value), page_number=page_number, y0=y0)))
    return make_page(tuple(elements), page_number=page_number)


def page_with_footer_text(
    text: str, page_number: int, y0: float = FOOTER_Y0
) -> ReconstructedPage:
    return make_page(
        (element(footer_paragraph(text, page_number=page_number, y0=y0)),),
        page_number=page_number,
    )


def make_two_page_gap_pdf(path: str) -> str:
    """A 2-page PDF whose bottom ``1, 2`` page-number sequence survives M2.5.

    M2.5 requires ``MIN_REPEATED_PAGES == 3`` distinct pages, so a two-page
    bare-number sequence is the documented M2.10 gap case.
    """
    doc = pymupdf.open()
    doc.set_metadata({"title": "Page Numbers Book"})
    for index in range(2):
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_textbox(
            pymupdf.Rect(72, 200, 500, 700),
            f"{BODY_LINE}\n\nBody paragraph {index + 1} continues here with "
            "enough text to be meaningful body content for the test to find.",
            fontname="helv", fontsize=12,
        )
        page.insert_text((500, 790), str(index + 1), fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    return str(path)
# --------------------------------------------------------------------------- #
# A. Basic bottom page numbers
# --------------------------------------------------------------------------- #


class TestBasicBottomPageNumbers:
    def test_sequence_1_to_4_removed(self) -> None:
        sources = [
            make_page(
                (
                    element(body_paragraph(f"Body content {word} here.", page_number=n)),
                    element(footer_paragraph(str(n), page_number=n)),
                ),
                page_number=n,
            )
            for n, word in enumerate(("one", "two", "three", "four"), start=1)
        ]
        source = make_document(tuple(sources))
        result = remove_page_numbers(source)

        assert result.removed_count == 4
        assert [d.text for d in result.removed] == ["1", "2", "3", "4"]
        assert [d.value for d in result.removed] == [1, 2, 3, 4]
        assert [d.page_number for d in result.removed] == [1, 2, 3, 4]

        remaining = [el.text for page in result.document.pages for el in page.elements]
        assert all("Body content" in text for text in remaining)
        assert all(text not in remaining for text in ("1", "2", "3", "4"))


# --------------------------------------------------------------------------- #
# B. Explicit page-number forms
# --------------------------------------------------------------------------- #


class TestExplicitForms:
    @pytest.mark.parametrize(
        "form",
        [
            "Page {n}",
            "page {n}",
            "Page {n} of 100",
            "{n} / 100",
            "- {n} -",
        ],
    )
    def test_explicit_forms_removed_at_footer(self, form: str) -> None:
        pages = tuple(
            page_with_footer_text(form.format(n=n), page_number=n) for n in (1, 2)
        )
        result = remove_page_numbers(make_document(pages))
        assert result.removed_count == 2
        assert [d.value for d in result.removed] == [1, 2]
        assert all(
            "explicit_page_number_form" in d.reasons for d in result.removed
        )
        assert all(d.confidence == 1.0 for d in result.removed)

    def test_lone_explicit_form_removed(self) -> None:
        # A single ``Page 7 of 20`` on one page: M2.5 needs repetition or a
        # multi-value sequence, so this survives M2.5. The explicit form plus
        # page-edge geometry is enough for M2.10.
        page = make_page(
            (
                element(body_paragraph("Closing prose of the chapter here.")),
                element(footer_paragraph("Page 7 of 20")),
            ),
            page_number=1,
        )
        result = remove_page_numbers(make_document((page,)))
        assert result.removed_count == 1
        removed = result.removed[0]
        assert removed.text == "Page 7 of 20"
        assert removed.value == 7
        assert removed.location is PageNumberLocation.FOOTER
        assert removed.confidence == 1.0

    def test_explicit_form_in_body_is_kept(self) -> None:
        # "Page 1" wording in body text (e.g. running prose) is not an artifact.
        body = element(
            body_paragraph("See Page 1 for the map and Page 2 for the index.")
        )
        result = remove_page_numbers(make_document((make_page((body,)),)))
        assert result.removed_count == 0
        assert result.candidates == ()
# --------------------------------------------------------------------------- #
# C. Position consistency
# --------------------------------------------------------------------------- #


class TestPositionConsistency:
    def test_consistent_positions_removed(self) -> None:
        pages = tuple(
            make_page(
                (element(footer_paragraph(str(n), page_number=n, y0=790.0)),),
                page_number=n,
            )
            for n in (1, 2, 3)
        )
        result = remove_page_numbers(make_document(pages))
        assert result.removed_count == 3
        assert all("positional_consistency" in d.reasons for d in result.removed)

    def test_inconsistent_positions_not_removed(self) -> None:
        # All three numbers sit in the footer band but at *different* vertical
        # positions (normalized spread > 0.03): the sequence evidence fails.
        y0s = {1: 790.0, 2: 760.0, 3: 730.0}
        pages = tuple(
            make_page(
                (element(footer_paragraph(str(n), page_number=n, y0=y0s[n])),),
                page_number=n,
            )
            for n in (1, 2, 3)
        )
        result = remove_page_numbers(make_document(pages))
        assert result.removed_count == 0
        assert len(result.candidates) == 3
        assert all(
            "insufficient_sequence_evidence" in c.reasons for c in result.candidates
        )
        # The original numbers are still in the output document.
        remaining = [el.text for page in result.document.pages for el in page.elements]
        assert remaining == ["1", "2", "3"]

    def test_consistent_positions_within_tolerance(self) -> None:
        # Slight page-to-page drift (normalized spread <= 0.03) is still
        # a consistent position.
        pages = tuple(
            make_page(
                (element(footer_paragraph(str(n), page_number=n, y0=800.0 + n)),),
                page_number=n,
            )
            for n in (1, 2, 3)
        )
        result = remove_page_numbers(make_document(pages))
        assert result.removed_count == 3


# --------------------------------------------------------------------------- #
# D. Sequence evidence
# --------------------------------------------------------------------------- #


class TestSequenceEvidence:
    def test_sequence_starting_at_non_one_removed(self) -> None:
        pages = tuple(page_with_footer(n, page_number=n) for n in (5, 6, 7))
        result = remove_page_numbers(make_document(pages))
        assert result.removed_count == 3
        assert [d.value for d in result.removed] == [5, 6, 7]

    def test_non_monotonic_values_not_removed(self) -> None:
        pages = tuple(
            page_with_footer(n, page_number=page_number)
            for page_number, n in enumerate((3, 1, 2), start=1)
        )
        result = remove_page_numbers(make_document(pages))
        assert result.removed_count == 0
        remaining = [el.text for page in result.document.pages for el in page.elements]
        assert remaining == ["3", "1", "2"]

    def test_single_isolated_bare_number_not_removed(self) -> None:
        page = make_page((element(footer_paragraph("7")),), page_number=1)
        result = remove_page_numbers(make_document((page,)))
        assert result.removed_count == 0
        assert len(result.candidates) == 1
        assert result.candidates[0].value == 7
        assert "insufficient_sequence_evidence" in result.candidates[0].reasons
# --------------------------------------------------------------------------- #
# E/F/G/H/I. Numeric body content is never removed
# --------------------------------------------------------------------------- #


class TestYearsPreserved:
    @pytest.mark.parametrize("year", ["1947", "2024", "1999"])
    def test_years_in_body_text_preserved(self, year: str) -> None:
        paragraphs = (
            element(body_paragraph(f"The event happened in {year} during the winter.")),
            element(body_paragraph("A second paragraph of ordinary prose.")),
        )
        result = remove_page_numbers(make_document((make_page(paragraphs),)))
        assert result.removed_count == 0
        assert result.candidates == ()
        remaining = [el.text for el in result.document.pages[0].elements]
        assert any(year in text for text in remaining)

    def test_year_at_page_edge_preserved(self) -> None:
        # Four-digit years are outside the bare ``\\d{1,3}`` pattern, so even
        # sitting at the page edge they are not page-number candidates.
        page = make_page(
            (
                element(footer_paragraph("2024")),
                element(body_paragraph("Copyright notice at the end of the book.")),
            ),
            page_number=1,
        )
        result = remove_page_numbers(make_document((page,)))
        assert result.removed_count == 0
        assert result.candidates == ()
        assert any(el.text == "2024" for el in result.document.pages[0].elements)


class TestDatesPreserved:
    def test_dates_preserved(self) -> None:
        paragraphs = (
            element(body_paragraph("Signed on June 5, 1947 in the old town hall.")),
            element(body_paragraph("The census of 5 June 2024 counted the houses.")),
        )
        result = remove_page_numbers(make_document((make_page(paragraphs),)))
        assert result.removed_count == 0
        remaining = [el.text for el in result.document.pages[0].elements]
        assert len(remaining) == 2


class TestMeasurementsPricesNumericProse:
    @pytest.mark.parametrize(
        "sentence",
        [
            "The car covered 100 miles in one hour of steady driving.",
            "The price was $19.95 after the discount was applied.",
            "The value of pi is approximately 3.14 to two decimal places.",
            "The wavelength measured 42 nanometers across the sample.",
        ],
    )
    def test_numeric_body_sentences_preserved(self, sentence: str) -> None:
        result = remove_page_numbers(
            make_document((make_page((element(body_paragraph(sentence)),)),))
        )
        assert result.removed_count == 0
        assert result.candidates == ()


class TestNumberedLists:
    def test_list_numbering_preserved(self) -> None:
        pages = tuple(
            make_page(
                (
                    element(body_paragraph("1. First item of the list.", page_number=n)),
                    element(body_paragraph("2. Second item of the list.", page_number=n)),
                ),
                page_number=n,
            )
            for n in (1, 2, 3)
        )
        result = remove_page_numbers(make_document(pages))
        assert result.removed_count == 0
        remaining = [el.text for page in result.document.pages for el in page.elements]
        assert any("1. First item" in text for text in remaining)
        assert any("2. Second item" in text for text in remaining)

    def test_bare_list_numbers_in_body_preserved(self) -> None:
        # Bare integers as list markers in the body (not at an edge) are not
        # even candidates: geometry already rules them out.
        pages = tuple(
            make_page(
                (
                    element(body_paragraph(str(n), page_number=n)),
                    element(body_paragraph("List item text here.", page_number=n)),
                ),
                page_number=n,
            )
            for n in (1, 2, 3)
        )
        result = remove_page_numbers(make_document(pages))
        assert result.removed_count == 0
        assert result.candidates == ()
# --------------------------------------------------------------------------- #
# J. Chapter headings and K. numeric headings
# --------------------------------------------------------------------------- #


class TestChapterAndNumericHeadings:
    def test_chapter_headings_remain_intact(self) -> None:
        pages = []
        for n in (1, 2, 3):
            pages.append(
                make_page(
                    (
                        element(heading_paragraph(f"Chapter {n}", page_number=n), is_heading=True),
                        element(body_paragraph("Prose of the chapter here.", page_number=n)),
                    ),
                    page_number=n,
                )
            )
        source = make_document(tuple(pages))
        result = remove_page_numbers(source)
        remaining = [el.text for page in result.document.pages for el in page.elements]
        for n in (1, 2, 3):
            assert f"Chapter {n}" in remaining
        # M2.9 chapter detection still works on the *removed* document.
        before = detect_chapters(source)
        after = detect_chapters(result.document)
        assert [c.text for c in after.chapters] == [c.text for c in before.chapters]
        assert [c.text for c in after.chapters] == ["Chapter 1", "Chapter 2", "Chapter 3"]

    def test_bare_number_heading_at_page_edge_preserved(self) -> None:
        # A numeric heading that *would* match the bare-number pattern is
        # preserved because headings are never page-number artifacts.
        page = make_page(
            (
                element(heading_paragraph("5", page_number=1, y0=800.0), is_heading=True),
                element(body_paragraph("Body prose here.", page_number=1)),
            ),
            page_number=1,
        )
        result = remove_page_numbers(make_document((page,)))
        assert result.removed_count == 0
        assert result.candidates == ()
        kinds = [el.kind for el in result.document.pages[0].elements]
        assert ElementKind.HEADING in kinds
        assert any(
            el.text == "5" for el in result.document.pages[0].elements
        )

    def test_numbered_section_headings_preserved(self) -> None:
        page = make_page(
            (
                element(heading_paragraph("1. Introduction", page_number=1), is_heading=True),
                element(heading_paragraph("2. Main Body", page_number=1), is_heading=True),
            ),
            page_number=1,
        )
        result = remove_page_numbers(make_document((page,)))
        remaining = [el.text for el in result.document.pages[0].elements]
        assert "1. Introduction" in remaining
        assert "2. Main Body" in remaining


# --------------------------------------------------------------------------- #
# L. Mixed content
# --------------------------------------------------------------------------- #


class TestMixedContent:
    def test_only_page_number_artifacts_removed(self) -> None:
        pages = []
        for n in (1, 2, 3):
            pages.append(
                make_page(
                    (
                        element(
                            body_paragraph(
                                "The storm began in 1947 and the road measured "
                                "100 miles before the snow fell.",
                                page_number=n,
                            )
                        ),
                        element(footer_paragraph(str(n), page_number=n)),
                    ),
                    page_number=n,
                )
            )
        result = remove_page_numbers(make_document(tuple(pages)))
        assert result.removed_count == 3
        assert [d.text for d in result.removed] == ["1", "2", "3"]
        remaining = [el.text for page in result.document.pages for el in page.elements]
        assert len(remaining) == 3
        assert all("1947" in text and "100 miles" in text for text in remaining)


# --------------------------------------------------------------------------- #
# M. Top-position (header) page numbers
# --------------------------------------------------------------------------- #


class TestHeaderPageNumbers:
    def test_header_sequence_removed(self) -> None:
        pages = tuple(
            make_page(
                (element(header_paragraph(str(n), page_number=n)),),
                page_number=n,
            )
            for n in (1, 2, 3)
        )
        result = remove_page_numbers(make_document(pages))
        assert result.removed_count == 3
        assert all(
            d.location is PageNumberLocation.HEADER for d in result.removed
        )

    def test_isolated_header_number_candidate_only(self) -> None:
        page = make_page(
            (element(header_paragraph("9", page_number=1)),), page_number=1
        )
        result = remove_page_numbers(make_document((page,)))
        assert result.removed_count == 0
        assert len(result.candidates) == 1
        assert result.candidates[0].location is PageNumberLocation.HEADER
# --------------------------------------------------------------------------- #
# N. Different page sizes
# --------------------------------------------------------------------------- #


class TestDifferentPageSizes:
    def test_non_a4_page_size(self) -> None:
        # US Letter: footer band is bottom 15% of 792 == y >= 673.2.
        pages = tuple(
            make_page(
                (element(footer_paragraph(str(n), page_number=n, y0=740.0)),),
                page_number=n,
            )
            for n in (1, 2)
        )
        result = remove_page_numbers(
            make_document(pages, page_size=(612.0, 792.0))
        )
        assert result.removed_count == 2

    def test_mixed_page_sizes_per_page(self) -> None:
        pages = (
            make_page(
                (element(footer_paragraph("1", page_number=1, y0=800.0)),),
                page_number=1,
            ),
            make_page(
                (element(footer_paragraph("2", page_number=2, y0=740.0)),),
                page_number=2,
            ),
        )
        result = remove_page_numbers(
            make_document(
                pages, page_sizes={1: (595.0, 842.0), 2: (612.0, 792.0)}
            )
        )
        assert result.removed_count == 2


# --------------------------------------------------------------------------- #
# O. Blank/empty document
# --------------------------------------------------------------------------- #


class TestEmptyDocument:
    def test_empty_document_gives_empty_result(self) -> None:
        source = make_document(())
        result = remove_page_numbers(source)
        assert result.document.pages == ()
        assert result.removed == ()
        assert result.candidates == ()
        assert result.removed_count == 0

    def test_document_without_layout_geometry_is_conservative(self) -> None:
        # No paragraph layout means no real page dimensions, so no geometry
        # can be trusted: nothing is guessed or removed.
        source = make_document(
            (make_page((element(footer_paragraph("7")),), page_number=1),),
            with_layout=False,
        )
        result = remove_page_numbers(source)
        assert result.removed_count == 0
        assert result.candidates == ()


# --------------------------------------------------------------------------- #
# P. Invalid input
# --------------------------------------------------------------------------- #


class TestInvalidInput:
    @pytest.mark.parametrize("bad", ["not a document", None, 42, object()])
    def test_non_reconstructed_document_rejected(self, bad: object) -> None:
        with pytest.raises(TypeError):
            remove_page_numbers(bad)  # type: ignore[arg-type]
# --------------------------------------------------------------------------- #
# Q/R/S. Determinism, immutability, provenance
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_repeated_invocation_identical(self) -> None:
        pages = tuple(page_with_footer(n, page_number=n) for n in (1, 2, 3))
        source = make_document(pages)
        first = remove_page_numbers(source)
        second = remove_page_numbers(source)

        assert first.removed_count == second.removed_count == 3
        assert first.document == second.document
        for left, right in zip(first.removed, second.removed):
            assert (
                left.text,
                left.page_number,
                left.value,
                left.location,
                left.confidence,
                left.reasons,
            ) == (
                right.text,
                right.page_number,
                right.value,
                right.location,
                right.confidence,
                right.reasons,
            )


class TestImmutability:
    def test_source_document_never_mutated(self) -> None:
        pages = tuple(page_with_footer(n, page_number=n) for n in (1, 2, 3))
        source = make_document(pages)
        original_element_tuples = tuple(page.elements for page in source.pages)
        original_texts = [
            (page.page_number, el.text) for page in source.pages for el in page.elements
        ]

        result = remove_page_numbers(source)

        # Source page element tuples are the exact same objects, untouched.
        assert source.pages[0].elements is original_element_tuples[0]
        assert [
            (page.page_number, el.text) for page in source.pages for el in page.elements
        ] == original_texts
        # Source still contains the page numbers.
        assert any(el.text == "1" for page in source.pages for el in page.elements)

        # The result is a *different* document object.
        assert result.document is not source
        # The new body no longer contains them.
        remaining = [
            el.text for page in result.document.pages for el in page.elements
        ]
        assert "1" not in remaining and "2" not in remaining and "3" not in remaining

    def test_upstream_objects_shared_by_reference(self) -> None:
        pages = tuple(page_with_footer(n, page_number=n) for n in (1, 2))
        source = make_document(pages)
        result = remove_page_numbers(source)
        assert result.document.paragraphs is source.paragraphs
        assert result.document.headings is source.headings
        assert result.document.header_footer is source.header_footer
        assert result.document.chapters is source.chapters


class TestProvenance:
    def test_removed_candidates_retain_source_paragraph(self) -> None:
        pages = tuple(page_with_footer(n, page_number=n) for n in (1, 2))
        source = make_document(pages)
        source_paragraph_ids = {
            id(el.paragraph) for page in source.pages for el in page.elements
        }
        result = remove_page_numbers(source)

        assert result.removed_count == 2
        for detected in result.removed:
            assert id(detected.source_paragraph) in source_paragraph_ids
            assert detected.source_paragraph.page_number == detected.page_number
            assert detected.source_paragraph.text == detected.text
            assert detected.page_number in (1, 2)

    def test_candidates_and_removed_are_ordered_deterministically(self) -> None:
        pages = (
            make_page(
                (element(footer_paragraph("2", page_number=1)),), page_number=1
            ),
            make_page(
                (element(footer_paragraph("1", page_number=2)),), page_number=2
            ),
        )
        result = remove_page_numbers(make_document(pages))
        # Values 2,1 are monotonic non-increasing; the candidate list is
        # always in document order: page 1 first, then page 2.
        assert [d.page_number for d in result.candidates] == [1, 2]
        assert [d.value for d in result.candidates] == [2, 1]
        assert result.removed_count == 2
# --------------------------------------------------------------------------- #
# T/U/V. Integration with the real M2.7/M2.9 pipeline and EPUB path
# --------------------------------------------------------------------------- #


class TestPipelineIntegration:
    def test_removes_m25_gap_from_reconstructed_document(self, tmp_path) -> None:
        pdf = make_two_page_gap_pdf(str(tmp_path / "gap.pdf"))
        layout = extract_page_layout(pdf)
        document, _, _, furniture = reconstruct_layout(layout)

        # Precondition: M2.5 does NOT flag the two-page sequence as furniture,
        # so both numbers survive into the integrated body.
        assert furniture.detected_count == 0
        body_texts = [el.text for page in document.pages for el in page.elements]
        assert "1" in body_texts and "2" in body_texts
        assert document.chapters is not None

        result = remove_page_numbers(document)
        assert result.removed_count == 2
        assert [d.value for d in result.removed] == [1, 2]
        assert all(d.location is PageNumberLocation.FOOTER for d in result.removed)

        remaining = [el.text for page in result.document.pages for el in page.elements]
        assert "1" not in remaining and "2" not in remaining
        assert any("bright cold day" in text for text in remaining)

        # M2.9 chapters are preserved by reference; chapter detection still
        # runs unchanged on the removed document.
        assert result.document.chapters is document.chapters
        assert detect_chapters(document).chapter_count == detect_chapters(
            result.document
        ).chapter_count

    def test_epub_path_after_removal(self, tmp_path) -> None:
        pdf = make_two_page_gap_pdf(str(tmp_path / "gap.pdf"))
        layout = extract_page_layout(pdf)
        document, _, _, _ = reconstruct_layout(layout)
        result = remove_page_numbers(document)

        book = reconstructed_document_to_book(
            result.document, BookMetadata(title="Page Numbers Book")
        )
        out = tmp_path / "removed.epub"
        build_epub(book, out)

        with zipfile.ZipFile(out) as archive:
            names = sorted(n for n in archive.namelist() if n.endswith(".xhtml"))
            assert names
            all_html = "\n".join(
                archive.read(name).decode("utf-8") for name in names
            )
        assert "<p>1</p>" not in all_html
        assert "<p>2</p>" not in all_html
        assert "bright cold day" in all_html

    def test_existing_pipeline_unchanged(self, tmp_path) -> None:
        pdf = make_two_page_gap_pdf(str(tmp_path / "gap.pdf"))
        out = tmp_path / "plain.epub"
        convert_pdf_to_epub(pdf, out)
        assert out.exists()
        with zipfile.ZipFile(out) as archive:
            names = sorted(n for n in archive.namelist() if n.endswith(".xhtml"))
        assert names