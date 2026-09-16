"""End-to-end PDF -> Book -> EPUB pipeline tests (Milestones 1.5 + 4.1).

These tests verify the *composition* of the existing stages -- analyze ->
route/OCR -> reconstruct -> build -- through the public
:func:`convert_pdf_to_epub` entry point. PDF classification, extraction
details, OCR, and EPUB rendering are already covered by their own milestone
test modules; this module only proves the pipeline wires them together
correctly for TEXT, SCANNED, and MIXED documents (scanned/mixed use an
injected fake OCR engine, so the deterministic suite needs no Tesseract).

All PDFs are generated on the fly with PyMuPDF; nothing here requires
internet access or external fixture files.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pymupdf
import pytest
from ebooklib import epub

from kindle_converter import convert_pdf_to_epub
from kindle_converter.epub import build_epub
from kindle_converter.pdf import (
    EmptyPDFError,
    NoContentError,
    PDFReadError,
    extract_book,
)

# --------------------------------------------------------------------------- #
# Generation helpers (mirror the other milestone test modules)
# --------------------------------------------------------------------------- #

PAGE_WIDTH = 595
PAGE_HEIGHT = 842

#: A body-text line long enough to pass the analyzer's meaningful-text
#: threshold (longest non-whitespace line >= 24 chars).
TEXT_LINE = (
    "It was a bright cold day in April and the clocks were striking "
    "thirteen; Winston Smith, his chin nuzzled into his breast in an "
    "effort to escape the vile wind, slipped quickly through the "
    "glass doors of Victory Mansions."
)

#: Short, unique per-page markers that do not wrap in the test textbox.
MARKERS = ("MARKER ONE", "MARKER TWO", "MARKER THREE")

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


def make_text_pdf(path, *, pages: int = 2, title: str = "") -> Path:
    """Create a text PDF whose pages each carry unique body text."""
    doc = pymupdf.open()
    if title:
        doc.set_metadata({"title": title})
    for index in range(pages):
        page = _new_page(doc)
        page.insert_textbox(
            pymupdf.Rect(72, 100, 500, 700),
            f"{TEXT_LINE}\n\n{MARKERS[index]}",
            fontname="helv",
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()
    return Path(path)


def make_scanned_pdf(path, *, pages: int = 2) -> Path:
    """Create a PDF whose pages are full-page images with no text."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = _new_page(doc)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300))
        page.insert_image(pymupdf.Rect(50, 100, 545, 780), pixmap=pixmap)
    doc.save(str(path))
    doc.close()
    return Path(path)


def make_mixed_pdf(path, *, text_pages: int, image_pages: int) -> Path:
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
    return Path(path)


def make_blank_pdf(path, *, pages: int = 3) -> Path:
    """Create a PDF whose pages carry neither text nor images."""
    doc = pymupdf.open()
    for _ in range(pages):
        _new_page(doc)
    doc.save(str(path))
    doc.close()
    return Path(path)


def read_back(path: Path) -> epub.EpubBook:
    """Re-open the EPUB with EbookLib (validates package structure)."""
    return epub.read_epub(str(path))


class FakeOCREngine:
    """Deterministic ``OCREngine`` returning one predefined text per call.

    The pipeline never creates a hidden engine, so OCR-aware conversion can
    be tested without Tesseract: the last text is reused if more pages are
    OCR'd than texts were supplied.
    """

    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.calls: list[pymupdf.Pixmap] = []

    def recognize(self, image: pymupdf.Pixmap) -> str:
        self.calls.append(image)
        index = min(len(self.calls) - 1, len(self.texts) - 1)
        return self.texts[index]


def chapter_document(path: Path, index: int = 0) -> str:
    """The rendered XHTML of chapter ``index`` as a string."""
    with zipfile.ZipFile(path) as archive:
        return archive.read(f"EPUB/chapter-{index:02d}.xhtml").decode("utf-8")


def chapter_bodies(path: Path) -> list[str]:
    """Every chapter XHTML document in the EPUB, in spine order."""
    with zipfile.ZipFile(path) as archive:
        names = sorted(
            n for n in archive.namelist() if n.startswith("EPUB/chapter-")
        )
        return [archive.read(name).decode("utf-8") for name in names]


# --------------------------------------------------------------------------- #
# Successful end-to-end conversion
# --------------------------------------------------------------------------- #


class TestEndToEnd:
    def test_converts_text_pdf_to_epub(self, tmp_path) -> None:
        pdf_path = make_text_pdf(
            tmp_path / "book.pdf", pages=1, title="Pipeline Novel"
        )
        out = tmp_path / "book.epub"

        result = convert_pdf_to_epub(pdf_path, out)

        assert result is None
        assert out.is_file()
        assert out.read_bytes()[:4] == b"PK\x03\x04"

    def test_epub_is_valid_and_readable(self, tmp_path) -> None:
        pdf_path = make_text_pdf(
            tmp_path / "book.pdf", pages=1, title="Pipeline Novel"
        )
        out = tmp_path / "book.epub"
        convert_pdf_to_epub(pdf_path, out)

        re_read = read_back(out)
        assert len(re_read.toc) == 1  # the extractor always emits one chapter
        document = chapter_document(out)
        # The extracted passage survives in the generated XHTML.
        assert "Winston Smith" in document
        # The XHTML is well-formed enough to parse as XML.
        ET.fromstring(document)

    def test_title_metadata_survives(self, tmp_path) -> None:
        pdf_path = make_text_pdf(
            tmp_path / "book.pdf", pages=1, title="The Pipeline Novel"
        )
        out = tmp_path / "book.epub"
        convert_pdf_to_epub(pdf_path, out)

        re_read = read_back(out)
        assert re_read.title == "The Pipeline Novel"
        with zipfile.ZipFile(out) as archive:
            opf = archive.read("EPUB/content.opf").decode("utf-8")
        assert "<dc:title>The Pipeline Novel</dc:title>" in opf


# --------------------------------------------------------------------------- #
# Multiple pages
# --------------------------------------------------------------------------- #


class TestMultiplePages:
    def test_multiple_pages_are_converted(self, tmp_path) -> None:
        pdf_path = make_text_pdf(tmp_path / "multi.pdf", pages=3)
        out = tmp_path / "book.epub"

        convert_pdf_to_epub(pdf_path, out)

        bodies = "".join(chapter_bodies(out))
        assert "MARKER ONE" in bodies
        assert "MARKER TWO" in bodies
        assert "MARKER THREE" in bodies

    def test_page_boundaries_are_preserved(self, tmp_path) -> None:
        pdf_path = make_text_pdf(tmp_path / "multi.pdf", pages=3)
        out = tmp_path / "book.epub"

        convert_pdf_to_epub(pdf_path, out)

        # Two page boundaries for a three-page book (one fewer than pages).
        document = chapter_document(out)
        assert document.count('class="page-break"') == 2
        positions = [
            document.index("MARKER ONE"),
            document.index('class="page-break"'),
            document.index("MARKER TWO"),
        ]
        assert positions == sorted(positions)


# --------------------------------------------------------------------------- #
# Scanned / mixed input routing (M4.1: no longer rejected)
# --------------------------------------------------------------------------- #


class TestScannedAndMixedInputs:
    def test_scanned_pdf_converts_through_ocr_book_pipeline(
        self, tmp_path
    ) -> None:
        """SCANNED: PDF -> OCR-aware Book -> EPUB, with an injected engine."""
        pdf_path = make_scanned_pdf(tmp_path / "scanned.pdf")
        out = tmp_path / "book.epub"
        engine = FakeOCREngine(
            ["Scanned page one text.", "Scanned page two text."]
        )

        convert_pdf_to_epub(pdf_path, out, engine=engine)

        assert out.is_file()
        assert len(engine.calls) == 2  # one OCR call per scanned page
        bodies = "".join(chapter_bodies(out))
        assert "Scanned page one text." in bodies
        assert "Scanned page two text." in bodies

    def test_mixed_pdf_converts_through_unified_pipeline(self, tmp_path) -> None:
        """MIXED: native text and OCR text both reach the EPUB body."""
        pdf_path = make_mixed_pdf(
            tmp_path / "mixed.pdf", text_pages=3, image_pages=2
        )
        out = tmp_path / "book.epub"
        engine = FakeOCREngine(["Ocr text of an image-only page."] * 2)

        convert_pdf_to_epub(pdf_path, out, engine=engine)

        assert out.is_file()
        assert len(engine.calls) == 2  # only the image-only pages need OCR
        bodies = "".join(chapter_bodies(out))
        assert "Winston Smith" in bodies  # native pages survive untouched
        assert "Ocr text of an image-only page." in bodies

    def test_empty_pdf_raises(self, tmp_path) -> None:
        pdf_path = tmp_path / "empty.pdf"
        pdf_path.write_bytes(ZERO_PAGE_PDF)
        out = tmp_path / "book.epub"

        with pytest.raises(EmptyPDFError):
            convert_pdf_to_epub(pdf_path, out)

        assert not out.exists()

    def test_blank_pdf_raises_no_content(self, tmp_path) -> None:
        pdf_path = make_blank_pdf(tmp_path / "blank.pdf")
        out = tmp_path / "book.epub"

        with pytest.raises(NoContentError):
            convert_pdf_to_epub(pdf_path, out)

        assert not out.exists()


# --------------------------------------------------------------------------- #
# Invalid / missing input
# --------------------------------------------------------------------------- #


class TestInvalidInput:
    def test_missing_file_raises(self, tmp_path) -> None:
        out = tmp_path / "book.epub"
        with pytest.raises(PDFReadError):
            convert_pdf_to_epub(tmp_path / "missing.pdf", out)
        assert not out.exists()

    def test_corrupt_file_raises(self, tmp_path) -> None:
        pdf_path = tmp_path / "corrupt.pdf"
        pdf_path.write_bytes(b"this is definitely not a pdf")
        out = tmp_path / "book.epub"
        with pytest.raises(PDFReadError):
            convert_pdf_to_epub(pdf_path, out)
        assert not out.exists()

    def test_invalid_argument_type_raises(self, tmp_path) -> None:
        with pytest.raises(PDFReadError):
            convert_pdf_to_epub(12345, tmp_path / "book.epub")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Output handling
# --------------------------------------------------------------------------- #


class TestOutputHandling:
    def test_accepts_string_output(self, tmp_path) -> None:
        pdf_path = make_text_pdf(tmp_path / "book.pdf", pages=1)
        out = tmp_path / "book.epub"

        convert_pdf_to_epub(pdf_path, str(out))

        assert out.is_file()

    def test_existing_output_is_overwritten(self, tmp_path) -> None:
        pdf_path = make_text_pdf(tmp_path / "book.pdf", pages=1)
        out = tmp_path / "book.epub"
        out.write_bytes(b"not an epub")

        convert_pdf_to_epub(pdf_path, out)

        assert out.read_bytes()[:2] == b"PK"

    def test_missing_parent_directory_raises_oserror(self, tmp_path) -> None:
        pdf_path = make_text_pdf(tmp_path / "book.pdf", pages=1)
        with pytest.raises(OSError):
            convert_pdf_to_epub(
                pdf_path, tmp_path / "does" / "not" / "exist.epub"
            )


# --------------------------------------------------------------------------- #
# Open-document ownership
# --------------------------------------------------------------------------- #


class TestOpenDocumentOwnership:
    def test_caller_document_stays_open_after_conversion(self, tmp_path) -> None:
        pdf_path = make_text_pdf(tmp_path / "open.pdf", pages=1)
        out = tmp_path / "book.epub"
        doc = pymupdf.open(pdf_path)

        try:
            convert_pdf_to_epub(doc, out)
            assert out.is_file()
            assert not doc.is_closed
        finally:
            doc.close()


# --------------------------------------------------------------------------- #
# Composition sanity
# --------------------------------------------------------------------------- #


class TestComposition:
    def test_pipeline_matches_manual_stages(self, tmp_path) -> None:
        """The pipeline result equals running the stages by hand."""
        pdf_path = make_text_pdf(tmp_path / "book.pdf", pages=2)
        piped = tmp_path / "piped.epub"
        manual = tmp_path / "manual.epub"

        convert_pdf_to_epub(pdf_path, piped)
        book = extract_book(pdf_path)
        build_epub(book, manual)

        assert piped.read_bytes() == manual.read_bytes()