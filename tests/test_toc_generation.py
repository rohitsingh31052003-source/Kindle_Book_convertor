"""Tests for chapter-based table of contents generation (Milestone 2.11).

These tests verify that :func:`~kindle_converter.pdf.toc.generate_toc`
consumes M2.9 chapter-detection output (:class:`ChapterDetectionResult`) and
turns it into a small, immutable, deterministic :class:`TableOfContents`
without rediscovering chapters.

Most tests construct :class:`DetectedChapter` / :class:`ChapterDetectionResult`
fixtures directly so the TOC layer's own behavior (title preservation,
ordering, provenance, duplicates, empty results) is exercised precisely and
deterministically. Integration tests build a :class:`ReconstructedDocument`
and run the real M2.9 ``detect_chapters`` to prove the output flows into
M2.11 without adapters that duplicate detection logic.
"""

from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING

import pytest
from dataclasses import FrozenInstanceError

from kindle_converter.document import BookMetadata
from kindle_converter.epub import build_epub
from kindle_converter.pdf import (
    ChapterDetectionResult,
    ChapterNumberType,
    DetectedChapter,
    DetectedHeading,
    FontFlags,
    LayoutBlock,
    ReconstructedParagraph,
    TableOfContents,
    TOC_CHAPTER_LEVEL,
    TOCEntry,
    TextLine,
    TextSpan,
    detect_chapters,
    generate_toc,
)

if TYPE_CHECKING:
    from kindle_converter.pdf.reconstruction import ReconstructedDocument


# --------------------------------------------------------------------------- #
# Construction helpers
# --------------------------------------------------------------------------- #


def make_span(
    text: str,
    font_size: float = 24.0,
    font_name: str = "Helvetica-Bold",
    flags: FontFlags = FontFlags.BOLD,
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
    text: str, page_number: int = 1
) -> ReconstructedParagraph:
    line = make_line(text, (72, 100, 72 + len(text) * 12.0, 124))
    return ReconstructedParagraph(
        text=text,
        page_number=page_number,
        source_lines=(line,),
        source_blocks=(
            LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,)),
        ),
        source_line_orders=((0, 0),),
        reading_order_indices=(0,),
    )


def make_heading(paragraph: ReconstructedParagraph) -> DetectedHeading:
    return DetectedHeading(
        paragraph=paragraph, score=3.0, reasons=("toc_test_fixture",)
    )


def make_chapter(
    text: str,
    order: int,
    page_number: int = 1,
    number: str | None = None,
    number_type: ChapterNumberType | None = None,
) -> DetectedChapter:
    paragraph = make_paragraph(text, page_number=page_number)
    return DetectedChapter(
        order=order,
        text=text,
        number=number,
        number_type=number_type,
        page_number=page_number,
        paragraph=paragraph,
        heading=make_heading(paragraph),
        reasons=("contextual",),
    )


def make_toc(chapters: tuple[DetectedChapter, ...]) -> ChapterDetectionResult:
    """Build a ChapterDetectionResult from DetectedChapter fixtures."""
    return ChapterDetectionResult(chapters=chapters, candidates=())



# --------------------------------------------------------------------------- #
# A. Basic chapter TOC
# --------------------------------------------------------------------------- #


class TestBasicChapterTOC:
    def test_three_chapters_in_order(self) -> None:
        chapters = (
            make_chapter("Chapter 1", order=1, page_number=5),
            make_chapter("Chapter 2", order=2, page_number=12),
            make_chapter("Chapter 3", order=3, page_number=20),
        )
        toc = generate_toc(make_toc(chapters))
        assert isinstance(toc, TableOfContents)
        assert toc.entry_count == 3
        assert [entry.title for entry in toc.entries] == [
            "Chapter 1",
            "Chapter 2",
            "Chapter 3",
        ]
        assert [entry.order for entry in toc.entries] == [1, 2, 3]


# --------------------------------------------------------------------------- #
# B. Chapter title preservation
# --------------------------------------------------------------------------- #


class TestTitlePreservation:
    @pytest.mark.parametrize(
        "text",
        [
            "Chapter 1",
            "CHAPTER 1",
            "Chapter 1: The Beginning",
            "Part I",
            "Chapter One",
            "CHAPTER I — The Beginning",
            "Chapter IV — Retreat & Regroup",
            "A Motley Crew",
        ],
    )
    def test_exact_title_preserved(self, text: str) -> None:
        toc = generate_toc(
            make_toc((make_chapter(text, order=1, page_number=3),))
        )
        assert toc.entry_count == 1
        assert toc.entries[0].title == text


# --------------------------------------------------------------------------- #
# C. Ordering follows M2.9, never alphabetical / page-sorted
# --------------------------------------------------------------------------- #


class TestOrdering:
    def test_preserves_detected_order_not_alphabetical(self) -> None:
        # M2.9 established this exact order; TOC must not re-sort.
        chapters = (
            make_chapter("Zulu Dawn", order=2, page_number=40),
            make_chapter("Alpha Rising", order=1, page_number=10),
            make_chapter("Middle Crossing", order=3, page_number=90),
        )
        toc = generate_toc(make_toc(chapters))
        assert [e.title for e in toc.entries] == [
            "Zulu Dawn",
            "Alpha Rising",
            "Middle Crossing",
        ]
        assert [e.order for e in toc.entries] == [2, 1, 3]

    def test_preserves_detected_order_not_page_sorted(self) -> None:
        # Page numbers are deliberately non-monotonic; order still follows M2.9.
        chapters = (
            make_chapter("Chapter A", order=1, page_number=30),
            make_chapter("Chapter B", order=2, page_number=5),
            make_chapter("Chapter C", order=3, page_number=17),
        )
        toc = generate_toc(make_toc(chapters))
        assert [e.order for e in toc.entries] == [1, 2, 3]
        assert [e.page_number for e in toc.entries] == [30, 5, 17]


# --------------------------------------------------------------------------- #
# D. Page provenance
# --------------------------------------------------------------------------- #


class TestPageProvenance:
    def test_page_numbers_preserved(self) -> None:
        chapters = (
            make_chapter("Chapter 1", order=1, page_number=7),
            make_chapter("Chapter 2", order=2, page_number=11),
            make_chapter("Chapter 3", order=3, page_number=9),
        )
        toc = generate_toc(make_toc(chapters))
        assert [e.page_number for e in toc.entries] == [7, 11, 9]


# --------------------------------------------------------------------------- #
# E. Duplicate titles remain separate entries
# --------------------------------------------------------------------------- #


class TestDuplicateTitles:
    def test_identical_titles_stay_separate(self) -> None:
        chapters = (
            make_chapter("Home", order=1, page_number=3),
            make_chapter("Home", order=2, page_number=50),
        )
        toc = generate_toc(make_toc(chapters))
        assert toc.entry_count == 2
        assert [e.title for e in toc.entries] == ["Home", "Home"]
        assert [e.order for e in toc.entries] == [1, 2]
        # Uniqueness comes from chapter identity/order, not title text.
        assert toc.entries[0] is not toc.entries[1]


# --------------------------------------------------------------------------- #
# F/G. No-chapter and empty documents produce an empty TOC
# --------------------------------------------------------------------------- #


class TestNoChapters:
    def test_zero_chapters_yields_empty_toc(self) -> None:
        toc = generate_toc(ChapterDetectionResult(chapters=(), candidates=()))
        assert toc.entry_count == 0
        assert toc.entries == ()

    def test_empty_input_yields_empty_toc(self) -> None:
        toc = generate_toc(make_toc(()))
        assert toc.entry_count == 0
        assert toc.entries == ()
# --------------------------------------------------------------------------- #
# H. Front/back matter is not included unless M2.9 declares it a chapter
# --------------------------------------------------------------------------- #


class TestFrontBackMatter:
    @pytest.mark.parametrize(
        "term",
        [
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
        ],
    )
    def test_front_matter_headings_do_not_create_entries(self, term: str) -> None:
        paragraphs = [make_paragraph(term, page_number=i + 1) for i in range(2)]
        headings = tuple(make_heading(p) for p in paragraphs)
        # M2.9 reported these as candidates but not as chapters.
        result = ChapterDetectionResult(chapters=(), candidates=headings)
        toc = generate_toc(result)
        assert toc.entry_count == 0

    def test_real_m29_rejects_front_matter(self) -> None:
        # Run the real M2.9 detector so this is not a mocked expectation.
        doc = make_doc_from_headings(
            ["Introduction", "Contents", "Bibliography"]
        )
        result = detect_chapters(doc)
        assert result.chapter_count == 0
        assert generate_toc(result).entry_count == 0


# --------------------------------------------------------------------------- #
# I. V1 is chapter-based; ordinary section headings are not promoted
# --------------------------------------------------------------------------- #


class TestHeadingHierarchy:
    def test_v1_entries_are_all_top_level(self) -> None:
        chapters = (
            make_chapter("Chapter 1", order=1, page_number=2),
            make_chapter("Chapter 2", order=2, page_number=8),
        )
        toc = generate_toc(make_toc(chapters))
        assert all(e.level == TOC_CHAPTER_LEVEL == 1 for e in toc.entries)

    def test_section_headings_alone_yield_empty_toc(self) -> None:
        sections = tuple(
            make_chapter(f"Section {i}", order=i, page_number=i * 5)
            for i in (1, 2, 3)
        )
        # M2.9 did not promote these; model an empty-chapter result that still
        # carries the ordinary headings as candidates.
        result = ChapterDetectionResult(chapters=(), candidates=tuple(sections))
        assert generate_toc(result).entry_count == 0

    def test_real_m29_does_not_promote_simple_sections(self) -> None:
        doc = make_doc_from_headings(["Section 1", "Section 2"])
        assert detect_chapters(doc).chapter_count == 0
# --------------------------------------------------------------------------- #
# J. Numbering preservation
# --------------------------------------------------------------------------- #


class TestNumberingPreservation:
    @pytest.mark.parametrize(
        ("text", "number", "number_type"),
        [
            ("Chapter 1", "1", ChapterNumberType.ARABIC),
            ("Chapter II", "II", ChapterNumberType.ROMAN),
            ("Chapter One", "One", ChapterNumberType.WORD),
        ],
    )
    def test_numbering_as_supplied(
        self, text: str, number: str, number_type: ChapterNumberType
    ) -> None:
        toc = generate_toc(
            make_toc(
                (make_chapter(text, order=1, page_number=1, number=number,
                              number_type=number_type),)
            )
        )
        assert toc.entries[0].title == text
        assert toc.entries[0].chapter is not None
        assert toc.entries[0].chapter.number == number
        assert toc.entries[0].chapter.number_type == number_type


# --------------------------------------------------------------------------- #
# K. Punctuation / capitalization preservation
# --------------------------------------------------------------------------- #


class TestPunctuationCapitalization:
    @pytest.mark.parametrize(
        "text",
        [
            "CHAPTER I — The Beginning",
            "Chapter IV — Retreat & Regroup",
            "Part 12: A Question of Knitting!",
            "Appendix? No — Chapter 3",
        ],
    )
    def test_meaningful_formatting_preserved(self, text: str) -> None:
        toc = generate_toc(make_toc((make_chapter(text, order=1, page_number=2),)))
        assert toc.entries[0].title == text


# --------------------------------------------------------------------------- #
# L. Determinism
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_repeated_generation_identical(self) -> None:
        chapters = (
            make_chapter("Chapter 3", order=1, page_number=40),
            make_chapter("Chapter 1", order=2, page_number=5),
            make_chapter("Chapter 2", order=3, page_number=17),
        )
        result = make_toc(chapters)
        first = generate_toc(result)
        second = generate_toc(result)
        assert first == second
        assert first.entries == second.entries
        assert [e.title for e in first.entries] == [e.title for e in second.entries]

    def test_structurally_equal_but_distinct_inputs_yield_equal_toc(self) -> None:
        a = generate_toc(make_toc((make_chapter("Chapter 1", 1, page_number=3),)))
        b = generate_toc(make_toc((make_chapter("Chapter 1", 1, page_number=3),)))
        assert a == b
        assert a.entries[0].title == b.entries[0].title
# --------------------------------------------------------------------------- #
# M. Immutability
# --------------------------------------------------------------------------- #


class TestImmutability:
    def test_toc_entries_are_frozen(self) -> None:
        toc = generate_toc(
            make_toc((make_chapter("Chapter 1", order=1, page_number=2),))
        )
        with pytest.raises(FrozenInstanceError):
            toc.entries[0].title = "rewritten"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            toc.entries = ()  # type: ignore[misc]

    def test_source_chapters_unchanged(self) -> None:
        chapters = (
            make_chapter("Chapter 1", order=1, page_number=2),
            make_chapter("Chapter 2", order=2, page_number=8),
        )
        result = make_toc(chapters)
        snapshot = [(c.text, c.order, c.page_number) for c in result.chapters]

        toc = generate_toc(result)

        assert [(c.text, c.order, c.page_number) for c in result.chapters] == snapshot
        # Entries reference the exact same (immutable) chapter objects.
        for entry, chapter in zip(toc.entries, result.chapters):
            assert entry.chapter is chapter

    def test_no_placeholder_entries_manufactured(self) -> None:
        toc = generate_toc(ChapterDetectionResult(chapters=(), candidates=()))
        assert toc.entries == ()
        assert not any(e.chapter is None for e in toc.entries)


# --------------------------------------------------------------------------- #
# N. Invalid input
# --------------------------------------------------------------------------- #


class TestInvalidInput:
    @pytest.mark.parametrize(
        "bad",
        [None, {}, [], "Chapter 1", object()],
    )
    def test_non_chapter_result_raises(self, bad) -> None:
        with pytest.raises(TypeError):
            generate_toc(bad)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# O. Public exports
# --------------------------------------------------------------------------- #


class TestPublicExports:
    def test_public_api_available_from_pdf_package(self) -> None:
        import kindle_converter.pdf as pdf

        assert callable(pdf.generate_toc)
        assert pdf.generate_toc is generate_toc
        assert pdf.TableOfContents is TableOfContents
        assert pdf.TOCEntry is TOCEntry
        assert pdf.TOC_CHAPTER_LEVEL == 1
# --------------------------------------------------------------------------- #
# P/Q/R. Integration with M2.9 and the EPUB path
# --------------------------------------------------------------------------- #


def make_doc_from_headings(
    heading_texts: list[str],
) -> "ReconstructedDocument":
    """A minimal ReconstructedDocument whose headings are real M2.4 headings."""
    from kindle_converter.pdf.reconstruction import (
        ElementKind,
        ReconstructedDocument,
        ReconstructedElement,
        ReconstructedPage,
    )

    elements: list[ReconstructedElement] = []
    for i, text in enumerate(heading_texts):
        para = make_paragraph(text, page_number=i + 1)
        elements.append(
            ReconstructedElement(
                kind=ElementKind.HEADING,
                paragraph=para,
                heading=make_heading(para),
            )
        )
    return ReconstructedDocument(
        pages=(ReconstructedPage(page_number=1, elements=tuple(elements)),)
    )


class TestIntegration:
    def test_m29_to_m211_without_adapter_logic(self) -> None:
        doc = make_doc_from_headings(
            ["Chapter 1", "Chapter 2", "Chapter 3"]
        )
        # Real M2.9 detection is the only detection; M2.11 only consumes it.
        result = detect_chapters(doc)
        assert result.chapter_count == 3

        toc = generate_toc(result)
        assert toc.entry_count == 3
        assert [e.title for e in toc.entries] == [
            c.text for c in result.chapters
        ]
        assert [e.order for e in toc.entries] == [
            c.order for c in result.chapters
        ]
        # Entries preserve the exact source chapter objects from M2.9.
        assert [e.chapter for e in toc.entries] == list(result.chapters)

    def test_end_to_end_document_to_toc_to_epub(self, tmp_path) -> None:
        # ReconstructedDocument -> ChapterDetectionResult -> TOC
        doc = make_doc_from_headings(
            ["Chapter 1", "Chapter 2", "Chapter 3"]
        )
        chapters = detect_chapters(doc)
        toc = generate_toc(chapters)
        assert toc.entry_count == 3

        # The existing EPUB path (reconstructed_document_to_book -> build_epub)
        # still works alongside the new TOC representation, unchanged.
        from kindle_converter.pdf.reconstruction import (
            reconstructed_document_to_book,
        )

        book = reconstructed_document_to_book(
            doc, BookMetadata(title="TOC Integration Book")
        )
        out = tmp_path / "toc.epub"
        build_epub(book, out)
        assert out.exists()
        with zipfile.ZipFile(out) as archive:
            assert any(n.endswith(".xhtml") for n in archive.namelist())
