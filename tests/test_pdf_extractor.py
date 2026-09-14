"""Tests for the first-pass PDF text extractor (Milestone 1.3).

All PDFs are generated on the fly with PyMuPDF; nothing here requires
internet access or external fixture files.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter.document import Book, Chapter, PageBreak, Paragraph
from kindle_converter.pdf import (
    EmptyPDFError,
    MixedPDFError,
    NoContentError,
    PDFReadError,
    ScannedPDFError,
    extract_book,
)

# --------------------------------------------------------------------------- #
# Generation helpers
# --------------------------------------------------------------------------- #

PAGE_WIDTH = 595
PAGE_HEIGHT = 842

#: A body-text line long enough to pass the analyzer's meaningful-text
#: threshold (longest non-whitespace line >= 24 chars) and to read as real
#: prose rather than an isolated short line.
TEXT_LINE = (
    "It was a bright cold day in April and the clocks were striking "
    "thirteen; Winston Smith, his chin nuzzled into his breast in an "
    "effort to escape the vile wind, slipped quickly through the "
    "glass doors of Victory Mansions."
)

#: A shorter but still meaningful body paragraph for one-line tests. All
#: words are joined into a single logical line; the analyzer only needs the
#: longest line to reach 24 characters.
SHORT_PARAGRAPH = " ".join(
    ["The quick brown fox", "jumps over the lazy dog", "and runs quickly home"]
)

#: Short line used to model page numbers and terse footers.
PAGE_NUMBER = "42"

ZERO_PAGE_PDF = b"""\
%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [] /Count 0 >>
endobj
trailer
<< /Root 1 0 R /Size 2 >>
%%EOF
"""


def _new_page(doc: pymupdf.Document) -> pymupdf.Page:
    return doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)


def _fill_lines(
    page: pymupdf.Page, lines: list[tuple[str, float]], *, margin: float = 72.0
) -> None:
    """Insert each ``(text, font_size)`` line, stacking from ``margin``."""
    y = margin
    for text, font_size in lines:
        page.insert_text((margin, y), text, fontsize=font_size)
        y += font_size * 1.4
    # Skip a clear blank line so the next filled block is a new paragraph
    # (and far from the analyzer's top band).
    y += font_size * 2.2
    return y


def make_text_pdf(path, *, pages: int = 2, title: str = "") -> str:
    """Create a text PDF whose pages hold wrapped body paragraphs."""
    doc = pymupdf.open()
    if title:
        doc.set_metadata({"title": title})
    second = f"Far away, {TEXT_LINE}"
    for _ in range(pages):
        page = _new_page(doc)
        page.insert_textbox(
            pymupdf.Rect(72, 100, 500, 700),
            f"{TEXT_LINE}\n\n{second}",
            fontname="helv",
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()
    return str(path)


def make_scanned_pdf(path, *, pages: int = 2) -> str:
    """Create a PDF whose pages are full-page images with no text."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = _new_page(doc)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300))
        page.insert_image(pymupdf.Rect(50, 100, 545, 780), pixmap=pixmap)
    doc.save(str(path))
    doc.close()
    return str(path)


def make_mixed_pdf(path, *, text_pages: int, image_pages: int) -> str:
    """Create a PDF with the given number of text and image-only pages."""
    doc = pymupdf.open()
    for _ in range(text_pages):
        page = _new_page(doc)
        _fill_lines(page, [(TEXT_LINE, 11)])
        _fill_lines(page, [(TEXT_LINE, 11)], margin=150.0)
    for _ in range(image_pages):
        page = _new_page(doc)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300))
        page.insert_image(pymupdf.Rect(50, 100, 545, 780), pixmap=pixmap)
    doc.save(str(path))
    doc.close()
    return str(path)


def make_text_with_images_pdf(path, *, pages: int = 2) -> str:
    """Create a text PDF whose pages also embed a small image."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = _new_page(doc)
        page.insert_textbox(
            pymupdf.Rect(72, 100, 500, 400),
            TEXT_LINE,
            fontname="helv",
            fontsize=12,
        )
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 200))
        page.insert_image(pymupdf.Rect(72, 450, 400, 700), pixmap=pixmap)
    doc.save(str(path))
    doc.close()
    return str(path)


def page_blocks(book: Book) -> list[list[Paragraph]]:
    """Group a book's block list into per-page lists at the PageBreaks."""
    pages: list[list[Paragraph]] = [[]]
    for block in book.chapters[0].blocks:
        if isinstance(block, PageBreak):
            pages.append([])
        else:
            pages[-1].append(block)  # type: ignore[arg-type]
    return pages


def all_text(book: Book) -> str:
    """Concatenate every paragraph of the book (excluding PageBreaks)."""
    parts: list[str] = []
    for block in book.chapters[0].blocks:
        if isinstance(block, Paragraph):
            parts.append(block.text)
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Basic extraction
# --------------------------------------------------------------------------- #


class TestBasicExtraction:
    def test_single_page_text_pdf(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "one.pdf", pages=1)
        book = extract_book(path)
        assert isinstance(book, Book)
        assert len(book.chapters) == 1
        assert any(
            isinstance(block, Paragraph) for block in book.chapters[0].blocks
        )

    def test_multi_page_text_pdf_has_one_chapter(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "multi.pdf", pages=3)
        book = extract_book(path)
        assert len(book.chapters) == 1
        assert isinstance(book.chapters[0], Chapter)

    def test_multiple_paragraphs_are_distinct(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "paras.pdf", pages=1)
        book = extract_book(path)
        paragraphs = [
            b for b in book.chapters[0].blocks if isinstance(b, Paragraph)
        ]
        assert len(paragraphs) == 2
        # PyMuPDF may split a long unbroken line mid-word, so compare word
        # content rather than the exact full line string.
        words = set(TEXT_LINE.split())
        assert all(words.issubset(set(p.text.split())) for p in paragraphs)
        assert paragraphs[0].text != paragraphs[1].text

    def test_content_is_preserved_in_reading_order(self, tmp_path) -> None:
        doc = pymupdf.open()
        # Two pages, one paragraph each; the page order must drive reading
        # order regardless of the order the pages were written.
        marker1 = "FIRST PAGE STARTS HERE WITH UNIQUE CONTENT FOR READING ORDER"
        marker2 = "SECOND PAGE STARTS HERE WITH UNIQUE CONTENT FOR READING ORDER"
        page1 = _new_page(doc)
        page1.insert_text((72, 200), marker1, fontsize=12)
        page2 = _new_page(doc)
        page2.insert_text((72, 200), marker2, fontsize=12)
        doc.save(str(tmp_path / "order.pdf"))
        doc.close()

        book = extract_book(tmp_path / "order.pdf")
        text = all_text(book)
        assert text.index("FIRST PAGE STARTS HERE") < text.index(
            "SECOND PAGE STARTS HERE"
        )
        # Both markers must actually be present.
        assert "SECOND PAGE STARTS HERE" in text

    def test_lines_follow_dict_order_not_global_sort(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        # M2.7 integrated reading-order reconstruction (M2.2/M2.6) now
        # governs in-page order: text is emitted top-to-bottom rather than
        # in raw PyMuPDF "dict" content-stream order. The content-stream
        # order is preserved only as ``source_order`` provenance.
        lower = "THE LOWER LINE IS DRAWN FIRST IN THE CONTENT STREAM"
        upper = "THE UPPER LINE IS DRAWN SECOND IN THE CONTENT STREAM"
        page.insert_text((72, 700), lower, fontsize=12)
        page.insert_text((72, 100), upper, fontsize=12)
        doc.save(str(tmp_path / "dictorder.pdf"))
        doc.close()

        book = extract_book(tmp_path / "dictorder.pdf")
        text = all_text(book)
        assert lower in text and upper in text
        assert text.index(upper) < text.index(lower)

    def test_page_boundaries_are_preserved(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "pages.pdf", pages=3)
        book = extract_book(path)
        pagebreaks = [
            b for b in book.chapters[0].blocks if isinstance(b, PageBreak)
        ]
        assert len(pagebreaks) == 2  # one fewer than the number of pages

    def test_pagebreak_blocks_carry_no_text(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "pages.pdf", pages=2)
        book = extract_book(path)
        pagebreaks = [
            b for b in book.chapters[0].blocks if isinstance(b, PageBreak)
        ]
        # PageBreak is a slots-dataclass with no fields; only its type and
        # position matter.
        assert len(pagebreaks) == 1
        assert all(not hasattr(b, "text") for b in pagebreaks)

    def test_supplied_document_is_not_closed(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "open.pdf", pages=1)
        doc = pymupdf.open(path)
        try:
            book = extract_book(doc)
            assert len(book.chapters) == 1
            assert not doc.is_closed
        finally:
            doc.close()


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


class TestNormalization:
    def test_extra_whitespace_is_collapsed(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        # Runs of spaces and tabs in the source text.
        page.insert_text(
            (72, 200), f"{SHORT_PARAGRAPH}    extra\t\tspaces   here", fontsize=12
        )
        doc.save(str(tmp_path / "ws.pdf"))
        doc.close()

        book = extract_book(tmp_path / "ws.pdf")
        text = all_text(book)
        assert "  " not in text
        assert "\t" not in text
        assert text.index("extra") < text.index("spaces")

    def test_line_endings_are_normalized(self) -> None:
        from kindle_converter.pdf.extractor import _normalize_line

        assert _normalize_line("a\r\nb") == "a b"
        assert _normalize_line("a\rb") == "a b"
        assert _normalize_line("a\nb") == "a b"

    def test_page_number_stays_isolated_and_preserved(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        # Body text followed by a page number on the same page; both must
        # survive and the page number must not be glued to the body text.
        page.insert_text((72, 100), SHORT_PARAGRAPH, fontsize=12)
        page.insert_text((72, 700), PAGE_NUMBER, fontsize=12)
        doc.save(str(tmp_path / "folio.pdf"))
        doc.close()

        book = extract_book(tmp_path / "folio.pdf")
        paragraphs = [
            b for b in book.chapters[0].blocks if isinstance(b, Paragraph)
        ]
        assert any(p.text == PAGE_NUMBER for p in paragraphs)
        texts = [p.text for p in paragraphs]
        body = [t for t in texts if t != PAGE_NUMBER]
        assert all(PAGE_NUMBER not in t for t in body)

    def test_duplicate_text_is_not_doubled(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        # Some PDFs draw the same line twice at the same position to fake a
        # bold weight; the extractor must keep a single occurrence.
        duplicate = SHORT_PARAGRAPH
        page.insert_text((72, 200), duplicate, fontsize=12)
        page.insert_text((72, 200), duplicate, fontsize=12)
        doc.save(str(tmp_path / "bold.pdf"))
        doc.close()

        book = extract_book(tmp_path / "bold.pdf")
        text = all_text(book)
        assert text.count(duplicate) == 1


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #


class TestMetadata:
    def test_pdf_metadata_is_mapped_into_the_book(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        page.insert_text((72, 200), TEXT_LINE, fontsize=12)
        doc.set_metadata(
            {
                "title": "Nineteen Eighty-Four",
                "author": "George Orwell",
                "creator": "Penguin Books",
                "subject": "urn:isbn:9780141036144",
            }
        )
        doc.save(str(tmp_path / "meta.pdf"))
        doc.close()

        book = extract_book(tmp_path / "meta.pdf")
        assert book.metadata.title == "Nineteen Eighty-Four"
        assert book.metadata.author == "George Orwell"
        assert book.metadata.publisher == "Penguin Books"
        assert book.metadata.identifier == "urn:isbn:9780141036144"

    def test_missing_metadata_is_left_empty(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "empty.pdf")
        book = extract_book(path)
        assert book.metadata.is_empty is True

    def test_book_keeps_single_chapter_titled_from_pdf_title(
        self, tmp_path
    ) -> None:
        path = make_text_pdf(tmp_path / "titled.pdf", title="The Great Novel")
        book = extract_book(path)
        assert len(book.chapters) == 1
        assert book.chapters[0].title == "The Great Novel"


# --------------------------------------------------------------------------- #
# PDF types and error handling
# --------------------------------------------------------------------------- #


class TestPDFTypes:
    def test_scanned_pdf_raises_scanned_error(self, tmp_path) -> None:
        path = make_scanned_pdf(tmp_path / "scanned.pdf")
        with pytest.raises(ScannedPDFError):
            extract_book(path)

    def test_scanned_error_mentions_missing_ocr(self, tmp_path) -> None:
        path = make_scanned_pdf(tmp_path / "scanned.pdf", pages=1)
        with pytest.raises(ScannedPDFError, match="OCR"):
            extract_book(path)

    def test_mixed_pdf_raises_mixed_error(self, tmp_path) -> None:
        path = make_mixed_pdf(tmp_path / "mixed.pdf", text_pages=3, image_pages=2)
        with pytest.raises(MixedPDFError):
            extract_book(path)

    def test_text_pdf_with_images_extracts_text(self, tmp_path) -> None:
        path = make_text_with_images_pdf(tmp_path / "illustrated.pdf")
        book = extract_book(path)
        assert len(book.chapters) == 1
        assert any(
            isinstance(block, Paragraph) for block in book.chapters[0].blocks
        )


class TestErrorHandling:
    def test_missing_file_raises_read_error(self, tmp_path) -> None:
        with pytest.raises(PDFReadError):
            extract_book(tmp_path / "missing.pdf")

    def test_corrupt_file_raises_read_error(self, tmp_path) -> None:
        path = tmp_path / "corrupt.pdf"
        path.write_bytes(b"this is definitely not a pdf")
        with pytest.raises(PDFReadError):
            extract_book(path)

    def test_empty_pdf_raises_empty_error(self, tmp_path) -> None:
        path = tmp_path / "empty.pdf"
        path.write_bytes(ZERO_PAGE_PDF)
        with pytest.raises(EmptyPDFError):
            extract_book(path)

    def test_blank_pages_raise_no_content_error(self, tmp_path) -> None:
        doc = pymupdf.open()
        for _ in range(3):
            _new_page(doc)
        doc.save(str(tmp_path / "blank.pdf"))
        doc.close()
        with pytest.raises(NoContentError):
            extract_book(tmp_path / "blank.pdf")

    def test_invalid_argument_type_raises_read_error(self) -> None:
        with pytest.raises(PDFReadError):
            extract_book(12345)  # type: ignore[arg-type]

    def test_pdf_is_not_leaked_after_successful_extraction(
        self, tmp_path
    ) -> None:
        path = make_text_pdf(tmp_path / "leak.pdf", pages=1)
        book = extract_book(path)
        assert isinstance(book, Book)


# --------------------------------------------------------------------------- #
# Whitespace and reflow details
# --------------------------------------------------------------------------- #


class TestReflow:
    def test_wrapped_lines_join_with_a_single_space(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        # Two close lines of one paragraph; the page is narrow so the text
        # really wraps instead of fitting on a single line.
        page.insert_textbox(
            pymupdf.Rect(72, 100, 300, 400),
            TEXT_LINE,
            fontname="helv",
            fontsize=12,
        )
        doc.save(str(tmp_path / "wrap.pdf"))
        doc.close()

        book = extract_book(tmp_path / "wrap.pdf")
        paragraphs = [
            b for b in book.chapters[0].blocks if isinstance(b, Paragraph)
        ]
        assert len(paragraphs) == 1
        text = paragraphs[0].text
        assert "  " not in text
        assert "thirteen; Winston Smith" in text

    def test_spaced_lines_form_separate_paragraphs(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        # A text box with an explicit blank line between two paragraphs.
        page.insert_textbox(
            pymupdf.Rect(72, 100, 500, 700),
            f"{SHORT_PARAGRAPH}\n\n{SHORT_PARAGRAPH}",
            fontname="helv",
            fontsize=12,
        )
        doc.save(str(tmp_path / "spaced.pdf"))
        doc.close()

        book = extract_book(tmp_path / "spaced.pdf")
        paragraphs = [
            b for b in book.chapters[0].blocks if isinstance(b, Paragraph)
        ]
        assert [p.text for p in paragraphs] == [
            SHORT_PARAGRAPH,
            SHORT_PARAGRAPH,
        ]

    def test_indented_line_starts_new_paragraph(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        # A typical typeset page: three lines at the margin, then an indented
        # line - the signal of a new paragraph. The line must fit within the
        # page width from both the margin and the indent.
        body_line = "The quick brown fox jumps over the lazy dog and runs away"
        y = 200
        for _ in range(3):
            page.insert_text((72, y), body_line, fontsize=11)
            y += 16
        page.insert_text((120, y), body_line, fontsize=11)
        doc.save(str(tmp_path / "indent.pdf"))
        doc.close()

        book = extract_book(tmp_path / "indent.pdf")
        paragraphs = [
            b for b in book.chapters[0].blocks if isinstance(b, Paragraph)
        ]
        assert len(paragraphs) == 2
        body_words = set(body_line.split())
        assert all(body_words.issubset(set(p.text.split())) for p in paragraphs)


# --------------------------------------------------------------------------- #
# Return-shape sanity
# --------------------------------------------------------------------------- #


class TestResultShape:
    def test_blocks_are_only_paragraphs_and_pagebreaks(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "shape.pdf", pages=2)
        book = extract_book(path)
        for block in book.chapters[0].blocks:
            assert isinstance(block, (Paragraph, PageBreak))

    def test_extraction_is_deterministic(self, tmp_path) -> None:
        path = make_text_pdf(tmp_path / "deterministic.pdf", pages=2)
        first = extract_book(path)
        second = extract_book(path)
        assert first.chapters == second.chapters
        assert first.metadata == second.metadata