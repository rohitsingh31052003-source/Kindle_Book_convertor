"""Tests for conservative deterministic chapter detection (Milestone 2.9).

These tests construct M2.7 ReconstructedDocument objects directly
(from ReconstructedParagraph/DetectedHeading/ReconstructedElement)
to exercise chapter detection without depending on PDF rendering.
Synthetic PDFs are used for integration cases.

M2.9 consumes the M2.7 integrated representation; it does not re-extract,
re-sort, or mutate any upstream objects.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter.pdf import (
    ElementKind,
    FontFlags,
    LayoutBlock,
    LayoutPage,
    ParagraphLayout,
    ParagraphPage,
    ReconstructedParagraph,
    TextLine,
    TextSpan,
    ChapterDetectionResult,
    ChapterNumberType,
    DetectedChapter,
    DetectedHeading,
    detect_chapters,
    detect_chapters_simple,
    reconstruct_layout,
    classify_paragraphs,
    detect_headers_footers,
    build_reconstructed_document,
    HeadingLayout,
    HeaderFooterLayout,
)
from kindle_converter.pdf.chapters import (
    FRONT_MATTER_TERMS,
    ChapterNumberType,
)
from kindle_converter.document import BookMetadata

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0


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
        width = len(text) * font_size * 0.5
        bbox = (0.0, 0.0, width, font_size)
    return TextSpan(text=text, bbox=bbox, font_name=font_name, font_size=font_size, font_flags=flags)


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
    lines: tuple[TextLine, ...] | None = None,
    blocks: tuple[LayoutBlock, ...] | None = None,
    reading_order_indices: tuple[int, ...] | None = None,
    ends_at_page_boundary: bool = False,
) -> ReconstructedParagraph:
    if lines is None:
        lines = (make_line(text, (72, 100, 72 + len(text) * 5, 116)),)
    if blocks is None:
        blocks = tuple(
            LayoutBlock(text=line.text, bbox=line.bbox, order=0, lines=(line,))
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


def make_detected_heading(
    paragraph: ReconstructedParagraph,
    score: float = 3.0,
    reasons: tuple[str, ...] = ("font_size_above_body_baseline", "bold_typography"),
) -> DetectedHeading:
    return DetectedHeading(paragraph=paragraph, score=score, reasons=reasons)


def body_paragraph(text: str, y0: float = 200.0, page_number: int = 1) -> ReconstructedParagraph:
    line = make_line(text, (72, y0, 72 + len(text) * 6.0, y0 + 14), 0)
    return make_paragraph(text, page_number=page_number, lines=(line,),
        blocks=(LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,)),))


def heading_paragraph(text: str, y0: float = 120.0, page_number: int = 1,
                      font_size: float = 24.0, font_name: str = "Helvetica-Bold",
                      flags: FontFlags = FontFlags.BOLD) -> ReconstructedParagraph:
    line = make_line(text, (72, y0, 72 + len(text) * font_size * 0.5, y0 + font_size), 0,
        (make_span(text, font_size=font_size, font_name=font_name, flags=flags),))
    return make_paragraph(text, page_number=page_number, lines=(line,),
        blocks=(LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,)),))


def make_reconstructed_element(
    text: str,
    is_heading: bool,
    paragraph: ReconstructedParagraph,
    heading: DetectedHeading | None = None,
) -> "ReconstructedElement":
    from kindle_converter.pdf.reconstruction import ElementKind, ReconstructedElement
    return ReconstructedElement(
        kind=ElementKind.HEADING if is_heading else ElementKind.PARAGRAPH,
        paragraph=paragraph,
        heading=heading,
    )


def make_reconstructed_page(
    elements: tuple,
    page_number: int = 1,
) -> "ReconstructedPage":
    from kindle_converter.pdf.reconstruction import ReconstructedPage
    return ReconstructedPage(page_number=page_number, elements=elements)


def make_reconstructed_document(
    pages: tuple,
    paragraphs: ParagraphLayout | None = None,
    headings: HeadingLayout | None = None,
    header_footer: HeaderFooterLayout | None = None,
    chapters: "ChapterDetectionResult | None" = None,
) -> "ReconstructedDocument":
    from kindle_converter.pdf.reconstruction import ReconstructedDocument
    return ReconstructedDocument(
        pages=pages,
        paragraphs=paragraphs,
        headings=headings,
        header_footer=header_footer,
        chapters=chapters,
    )


def make_elements_from_headings(
    heading_texts: list[tuple[str, int]],
    body_texts: list[str] | None = None,
    page_number: int = 1,
) -> tuple:
    from kindle_converter.pdf.reconstruction import ElementKind, ReconstructedElement
    
    elements: list = []
    body_idx = 0
    
    for i, (text, pg) in enumerate(heading_texts):
        if body_texts and body_idx < len(body_texts):
            bp = body_paragraph(body_texts[body_idx], page_number=pg)
            elements.append(ReconstructedElement(
                kind=ElementKind.PARAGRAPH, paragraph=bp, heading=None
            ))
            body_idx += 1
        
        para = heading_paragraph(text, page_number=pg)
        heading = make_detected_heading(para)
        elements.append(ReconstructedElement(
            kind=ElementKind.HEADING, paragraph=para, heading=heading
        ))
    
    if body_texts:
        while body_idx < len(body_texts):
            bp = body_paragraph(body_texts[body_idx], page_number=page_number)
            elements.append(ReconstructedElement(
                kind=ElementKind.PARAGRAPH, paragraph=bp, heading=None
            ))
            body_idx += 1
    
    return tuple(elements)


# --------------------------------------------------------------------------- #
# A. Basic explicit chapters
# --------------------------------------------------------------------------- #


class TestBasicExplicitChapters:
    def test_chapter_1_detected(self) -> None:
        elements = make_elements_from_headings([("Chapter 1", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 1
        assert result.chapters[0].text == "Chapter 1"
        assert result.chapters[0].number == "1"
        assert result.chapters[0].number_type == ChapterNumberType.ARABIC

    def test_chapter_2_3_detected(self) -> None:
        elements = make_elements_from_headings([("Chapter 1", 1), ("Chapter 2", 1), ("Chapter 3", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 3
        assert [c.text for c in result.chapters] == ["Chapter 1", "Chapter 2", "Chapter 3"]

    def test_uppercase_chapter_detected(self) -> None:
        elements = make_elements_from_headings([("CHAPTER 1", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 1
        assert result.chapters[0].text == "CHAPTER 1"

    def test_chapter_one_detected(self) -> None:
        elements = make_elements_from_headings([("Chapter One", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 1
        assert result.chapters[0].number == "One"
        assert result.chapters[0].number_type == ChapterNumberType.WORD

    def test_part_i_detected(self) -> None:
        elements = make_elements_from_headings([("Part I", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 1
        assert result.chapters[0].text == "Part I"
        assert result.chapters[0].number_type == ChapterNumberType.ROMAN

    def test_chapter_with_title_suffix(self) -> None:
        elements = make_elements_from_headings([("Chapter 1: The Beginning", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 1
        assert result.chapters[0].text == "Chapter 1: The Beginning"

    def test_chapter_detection_preserves_page_number(self) -> None:
        elements = make_elements_from_headings([("Chapter 1", 3)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 3),))
        result = detect_chapters(doc)
        assert result.chapters[0].page_number == 3

    def test_chapter_detection_preserves_paragraph_provenance(self) -> None:
        para = heading_paragraph("Chapter 1", page_number=1)
        heading = make_detected_heading(para)
        elem = make_reconstructed_element("Chapter 1", True, para, heading)
        doc = make_reconstructed_document((make_reconstructed_page((elem,), 1),))
        result = detect_chapters(doc)
        assert result.chapters[0].paragraph is para
        assert result.chapters[0].paragraph.text == "Chapter 1"


# --------------------------------------------------------------------------- #
# B. Numbering
# --------------------------------------------------------------------------- #


class TestNumbering:
    def test_arabic_numbering(self) -> None:
        elements = make_elements_from_headings([("Chapter 1", 1), ("Chapter 2", 1), ("Chapter 3", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 3
        assert result.chapters[0].number == "1"
        assert result.chapters[1].number == "2"
        assert result.chapters[2].number == "3"

    def test_roman_numeral_numbering(self) -> None:
        elements = make_elements_from_headings([("Part I", 1), ("Part II", 1), ("Part III", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 3
        assert result.chapters[0].number_type == ChapterNumberType.ROMAN

    def test_written_number_chapters(self) -> None:
        elements = make_elements_from_headings([("Chapter One", 1), ("Chapter Two", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 2
        assert result.chapters[0].number_type == ChapterNumberType.WORD

    def test_single_chapter_is_detected(self) -> None:
        elements = make_elements_from_headings([("Chapter 1", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 1

    def test_chapter_numbering_order_is_deterministic(self) -> None:
        elements = make_elements_from_headings([("Chapter 3", 1), ("Chapter 1", 1), ("Chapter 2", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 3
        assert [c.order for c in result.chapters] == [1, 2, 3]


# --------------------------------------------------------------------------- #
# C. Title-only chapters
# --------------------------------------------------------------------------- #


class TestTitleOnlyChapters:
    def test_repeated_title_only_headings_detected(self) -> None:
        def make_title(text: str) -> ReconstructedParagraph:
            line = make_line(text, (72, 120, 72 + len(text) * 12, 144), 0,
                (make_span(text, font_size=20, font_name="Helvetica-Bold", flags=FontFlags.BOLD),))
            return make_paragraph(text, page_number=1, lines=(line,),
                blocks=(LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,)),))
        
        h1 = make_title("The Great Adventure")
        body = body_paragraph("Some body text between chapters.", page_number=1)
        h2 = make_title("The Lost City")
        e1 = make_reconstructed_element("The Great Adventure", True, h1, make_detected_heading(h1))
        e_body = make_reconstructed_element("Some body text between chapters.", False, body)
        e2 = make_reconstructed_element("The Lost City", True, h2, make_detected_heading(h2))
        doc = make_reconstructed_document((make_reconstructed_page((e1, e_body, e2), 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 2

    def test_title_only_chapters_preserve_order(self) -> None:
        def make_title(text: str) -> ReconstructedParagraph:
            line = make_line(text, (72, 120, 72 + len(text) * 12, 144), 0,
                (make_span(text, font_size=20, font_name="Helvetica-Bold", flags=FontFlags.BOLD),))
            return make_paragraph(text, page_number=1, lines=(line,),
                blocks=(LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,)),))
        
        h1 = make_title("First Title")
        body = body_paragraph("Some body text between chapters.", page_number=1)
        h2 = make_title("Second Title")
        e1 = make_reconstructed_element("First Title", True, h1, make_detected_heading(h1))
        e_body = make_reconstructed_element("Some body text between chapters.", False, body)
        e2 = make_reconstructed_element("Second Title", True, h2, make_detected_heading(h2))
        doc = make_reconstructed_document((make_reconstructed_page((e1, e_body, e2), 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 2
        assert result.chapters[0].text == "First Title"
        assert result.chapters[1].text == "Second Title"


# --------------------------------------------------------------------------- #
# D. Non-chapter headings (front/back matter)
# --------------------------------------------------------------------------- #


class TestNonChapterHeadings:
    @pytest.mark.parametrize("term", [
        "Contents",
        "Table of Contents",
        "Preface",
        "Foreword",
        "Introduction",
        "Acknowledgements",
        "Bibliography",
        "References",
        "Appendix",
        "Index",
        "About the Author",
    ])
    def test_front_matter_not_chapter(self, term: str) -> None:
        elements = make_elements_from_headings([(term, 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 0

    def test_contents_with_number_not_chapter(self) -> None:
        elements = make_elements_from_headings([("Contents", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 0


# --------------------------------------------------------------------------- #
# E. Ordinary headings
# --------------------------------------------------------------------------- #


class TestOrdinaryHeadings:
    def test_section_heading_not_chapter(self) -> None:
        def make_section(text: str) -> ReconstructedParagraph:
            line = make_line(text, (72, 150, 72 + len(text) * 8, 166), 0,
                (make_span(text, font_size=14, font_name="Helvetica"),))
            return make_paragraph(text, page_number=1, lines=(line,),
                blocks=(LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,)),))
        
        s1 = make_section("Section 1")
        s2 = make_section("Section 2")
        e1 = make_reconstructed_element("Section 1", True, s1, make_detected_heading(s1, score=2.0))
        e2 = make_reconstructed_element("Section 2", True, s2, make_detected_heading(s2, score=2.0))
        doc = make_reconstructed_document((make_reconstructed_page((e1, e2), 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 0


# --------------------------------------------------------------------------- #
# F. False positives
# --------------------------------------------------------------------------- #


class TestFalsePositives:
    def test_arbitrary_number_not_chapter(self) -> None:
        para = make_paragraph("5", page_number=1)
        heading = make_detected_heading(para, score=2.0)
        elem = make_reconstructed_element("5", True, para, heading)
        doc = make_reconstructed_document((make_reconstructed_page((elem,), 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 0

    def test_date_not_chapter(self) -> None:
        para = make_paragraph("2024", page_number=1)
        heading = make_detected_heading(para, score=2.0)
        elem = make_reconstructed_element("2024", True, para, heading)
        doc = make_reconstructed_document((make_reconstructed_page((elem,), 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 0

    def test_short_ordinary_heading_not_chapter(self) -> None:
        para = make_paragraph("It was.", page_number=1)
        heading = make_detected_heading(para, score=2.0)
        elem = make_reconstructed_element("It was.", True, para, heading)
        doc = make_reconstructed_document((make_reconstructed_page((elem,), 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 0

    def test_page_number_not_chapter(self) -> None:
        para = make_paragraph("1", page_number=1)
        heading = make_detected_heading(para, score=2.0)
        elem = make_reconstructed_element("1", True, para, heading)
        doc = make_reconstructed_document((make_reconstructed_page((elem,), 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 0


# --------------------------------------------------------------------------- #
# G. Multiple chapters
# --------------------------------------------------------------------------- #


class TestMultipleChapters:
    def test_chapter_order_deterministic(self) -> None:
        elements = make_elements_from_headings([
            ("Chapter 1", 1), ("Chapter 2", 1), ("Chapter 3", 1)
        ])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 3
        assert [c.order for c in result.chapters] == [1, 2, 3]
        assert [c.text for c in result.chapters] == ["Chapter 1", "Chapter 2", "Chapter 3"]

    def test_chapter_provenance_preserved(self) -> None:
        para1 = heading_paragraph("Chapter 1", page_number=1)
        para2 = heading_paragraph("Chapter 2", page_number=2)
        h1 = make_detected_heading(para1)
        h2 = make_detected_heading(para2)
        e1 = make_reconstructed_element("Chapter 1", True, para1, h1)
        e2 = make_reconstructed_element("Chapter 2", True, para2, h2)
        doc = make_reconstructed_document((
            make_reconstructed_page((e1,), 1),
            make_reconstructed_page((e2,), 2),
        ))
        result = detect_chapters(doc)
        assert result.chapter_count == 2
        assert result.chapters[0].paragraph is para1
        assert result.chapters[1].paragraph is para2
        assert result.chapters[0].page_number == 1
        assert result.chapters[1].page_number == 2

    def test_mixed_chapter_and_body(self) -> None:
        elements = make_elements_from_headings([
            ("Chapter 1", 1), ("Chapter 2", 1)
        ], body_texts=["Some body text."])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 2


# --------------------------------------------------------------------------- #
# H. Empty document
# --------------------------------------------------------------------------- #


class TestEmptyDocument:
    def test_empty_document_returns_empty_result(self) -> None:
        from kindle_converter.pdf.reconstruction import ReconstructedDocument
        doc = ReconstructedDocument(pages=())
        result = detect_chapters(doc)
        assert result.chapter_count == 0
        assert len(result.candidates) == 0

    def test_document_with_no_headings(self) -> None:
        para = body_paragraph("Some body text.", page_number=1)
        elem = make_reconstructed_element("Some body text.", False, para)
        doc = make_reconstructed_document((make_reconstructed_page((elem,), 1),))
        result = detect_chapters(doc)
        assert result.chapter_count == 0


# --------------------------------------------------------------------------- #
# I. Invalid input
# --------------------------------------------------------------------------- #


class TestInvalidInput:
    def test_non_reconstructed_document_raises_typeerror(self) -> None:
        with pytest.raises(TypeError):
            detect_chapters("not a document")  # type: ignore[arg-type]

    def test_none_raises_typeerror(self) -> None:
        with pytest.raises(TypeError):
            detect_chapters(None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# J. Determinism
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_same_input_produces_identical_output(self) -> None:
        elements = make_elements_from_headings([("Chapter 1", 1), ("Chapter 2", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result1 = detect_chapters(doc)
        result2 = detect_chapters(doc)
        assert result1 == result2
        assert result1.chapter_count == result2.chapter_count
        for c1, c2 in zip(result1.chapters, result2.chapters):
            assert c1.text == c2.text
            assert c1.number == c2.number
            assert c1.page_number == c2.page_number

    def test_detect_chapters_simple_is_deterministic(self) -> None:
        elements = make_elements_from_headings([("Chapter 1", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        simple1 = detect_chapters_simple(doc)
        simple2 = detect_chapters_simple(doc)
        assert simple1 == simple2


# --------------------------------------------------------------------------- #
# K. Immutability
# --------------------------------------------------------------------------- #


class TestImmutability:
    def test_source_document_not_mutated(self) -> None:
        para = heading_paragraph("Chapter 1", page_number=1)
        heading = make_detected_heading(para)
        elem = make_reconstructed_element("Chapter 1", True, para, heading)
        page = make_reconstructed_page((elem,), 1)
        doc = make_reconstructed_document((page,))
        
        original_elements = doc.elements
        original_pages = doc.pages
        
        result = detect_chapters(doc)
        
        assert list(doc.elements) == list(original_elements)
        assert doc.pages is original_pages
        assert doc.pages[0] is page
        assert doc.pages[0].elements[0].paragraph is para

    def test_source_paragraphs_unchanged(self) -> None:
        para = heading_paragraph("Chapter 1", page_number=1)
        original_text = para.text
        heading = make_detected_heading(para)
        elem = make_reconstructed_element("Chapter 1", True, para, heading)
        doc = make_reconstructed_document((make_reconstructed_page((elem,), 1),))
        detect_chapters(doc)
        assert para.text == original_text


# --------------------------------------------------------------------------- #
# L. Integration
# --------------------------------------------------------------------------- #


class TestIntegration:
    def test_detect_chapters_with_reconstruct_layout(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 120), "Chapter 1", fontsize=20, fontname="hebo")
        page.insert_text((72, 200), "Body text here.", fontsize=12, fontname="helv")
        page.insert_text((72, 120), "Chapter 2", fontsize=20, fontname="hebo")
        doc.save(str(tmp_path / "chapters.pdf"))
        doc.close()

        from kindle_converter.pdf import extract_page_layout
        layout = extract_page_layout(str(tmp_path / "chapters.pdf"))
        document, _, _, _ = reconstruct_layout(layout)
        
        assert document.chapters is not None
        assert document.chapters.chapter_count >= 1

    def test_build_reconstructed_document_chapters_field(self) -> None:
        from kindle_converter.pdf.reconstruction import ReconstructedDocument
        doc = ReconstructedDocument(pages=())
        assert doc.chapters is None

    def test_chapter_result_contains_candidates(self) -> None:
        elements = make_elements_from_headings([("Chapter 1", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert isinstance(result, ChapterDetectionResult)
        assert len(result.candidates) >= 1

    def test_chapter_result_future_toc_compatibility(self) -> None:
        elements = make_elements_from_headings([("Chapter 1", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        chapter = result.chapters[0]
        assert isinstance(chapter.text, str)
        assert isinstance(chapter.page_number, int)
        assert isinstance(chapter.order, int)
        assert isinstance(chapter.paragraph, ReconstructedParagraph)
        assert isinstance(chapter.heading, DetectedHeading)
        assert isinstance(chapter.number, (str, type(None)))


# --------------------------------------------------------------------------- #
# M. Future compatibility
# --------------------------------------------------------------------------- #


class TestFutureCompatibility:
    def test_chapters_field_on_reconstructed_document(self) -> None:
        from kindle_converter.pdf.reconstruction import ReconstructedDocument
        doc = ReconstructedDocument(pages=())
        assert doc.chapters is None

    def test_detect_chapters_returns_chapter_detection_result(self) -> None:
        elements = make_elements_from_headings([("Chapter 1", 1)])
        doc = make_reconstructed_document((make_reconstructed_page(elements, 1),))
        result = detect_chapters(doc)
        assert isinstance(result, ChapterDetectionResult)
        assert hasattr(result, 'chapters')
        assert hasattr(result, 'candidates')
        assert hasattr(result, 'chapter_count')

    def test_detected_chapter_has_all_required_fields(self) -> None:
        para = heading_paragraph("Chapter 1", page_number=1)
        heading = make_detected_heading(para)
        elem = make_reconstructed_element("Chapter 1", True, para, heading)
        doc = make_reconstructed_document((make_reconstructed_page((elem,), 1),))
        result = detect_chapters(doc)
        chapter = result.chapters[0]
        assert hasattr(chapter, 'order')
        assert hasattr(chapter, 'text')
        assert hasattr(chapter, 'number')
        assert hasattr(chapter, 'number_type')
        assert hasattr(chapter, 'page_number')
        assert hasattr(chapter, 'paragraph')
        assert hasattr(chapter, 'heading')
        assert hasattr(chapter, 'reasons')


# --------------------------------------------------------------------------- #
# Integration with synthetic PDF end-to-end
# --------------------------------------------------------------------------- #


class TestEndToEnd:
    def test_synthetic_pdf_chapter_detection(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 120), "Chapter 1", fontsize=20, fontname="hebo")
        page.insert_text((72, 200), "Body text.", fontsize=12, fontname="helv")
        page.insert_text((72, 120), "Chapter 2", fontsize=20, fontname="hebo")
        doc.save(str(tmp_path / "book.pdf"))
        doc.close()

        from kindle_converter.pdf import extract_page_layout
        layout = extract_page_layout(str(tmp_path / "book.pdf"))
        document, _, _, _ = reconstruct_layout(layout)
        
        assert document.chapters is not None
        assert document.chapters.chapter_count >= 1

    def test_epub_generation_unaffected(self, tmp_path) -> None:
        from kindle_converter import convert_pdf_to_epub
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        text = "Chapter 1\n\nBody text for the book here.\n\nMore body content to fill the page and ensure text extraction works properly.\n\nAdditional paragraph with more content.\n\nFinal paragraph of chapter one."
        page.insert_textbox(pymupdf.Rect(72, 200, 500, 700), text, fontname="helv", fontsize=12)
        path = str(tmp_path / "plain.pdf")
        doc.save(path)
        doc.close()
        out = tmp_path / "plain.epub"
        convert_pdf_to_epub(path, out)
        assert out.exists()