"""First-pass PDF text extraction (Milestone 1.3).

This module converts a *text-based* PDF into the format-independent
:mod:`kindle_converter.document` book model. It is a deliberately simple
first extraction layer:

* It extracts readable text with PyMuPDF, preserving reading order as
  reliably as PyMuPDF's "dict" layout provides it.
* It applies only conservative, deterministic normalization.
* It performs **no** semantic reconstruction (no chapter detection, no
  heading detection, no header/footer stripping) -- that is a later
  structural stage.

Pipeline: PDF -> :func:`analyze_pdf` (the analyzer) -> text extraction
-> :class:`kindle_converter.document.models.Book`.

The extractor knows nothing about EPUB/AZW3 generation: the document
model is the only bridge to the output side.
"""

from __future__ import annotations

import os
import re

import pymupdf

from ..document import (
    Book,
    BookMetadata,
    Chapter,
    DocumentBlock,
    PageBreak,
    Paragraph,
)
from .analyzer import (
    EmptyPDFError,
    NoContentError,
    PDFReadError,
    analyze_pdf,
)
from .models import PDFType

PathLike = str | os.PathLike[str]

#: The vertical gap between two consecutive lines (measured from the bottom
#: edge of the upper line to the top edge of the lower line) must exceed this
#: multiple of the page's median line height before the lower line is treated
#: as starting a new paragraph. In practice a single blank line yields a gap
#: of roughly one line height, while consecutive wrapped lines overlap
#: slightly (negative gap). 0.5 cleanly separates those two cases.
PARAGRAPH_GAP_FACTOR = 0.5

#: A line whose left edge lies at least this many points to the right of the
#: preceding line's left edge starts a new paragraph. Wrapped continuation
#: lines always share the left margin; an indented line is the classic
#: signal of a new paragraph in typeset documents.
PARAGRAPH_INDENT_PT = 12.0

#: The largest number of characters that makes a line "indisputably
#: isolated": a line of this length or shorter becomes its own paragraph
#: even when it is tightly packed with the surrounding lines, so that a
#: lone page number or a terse footer is not glued onto the body text.
SHORT_LINE_THRESHOLD = 8

RE_SPACE = re.compile(r"\s+")
RE_LEADING_SPACES = re.compile(r"^ {1,4}")

# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class PDFExtractionError(Exception):
    """Base class for errors raised while extracting a document model."""


class ScannedPDFError(PDFExtractionError):
    """Raised when asked to extract text from a SCANNED (image-only) PDF.

    OCR is not implemented yet, so a scanned document cannot be converted;
    this exception is raised instead of silently producing an empty or
    partial book.
    """


class MixedPDFError(PDFExtractionError):
    """Raised when asked to extract text from a MIXED PDF.

    ``extract_book`` deliberately refuses to silently pretend a mixed
    document is a fully text-based one. Handling mixed documents (for
    example by OCR-ing the image pages) is a later milestone.
    """


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def extract_book(source: PathLike | pymupdf.Document) -> Book:
    """Extract a :class:`~kindle_converter.document.models.Book` from a PDF.

    Parameters
    ----------
    source:
        Either a path to a PDF file or an already-open PyMuPDF ``Document``.
        With a path, the file is opened and closed within this call. With a
        ``Document`` the caller keeps ownership: the document is never
        mutated and never closed here.

    Returns
    -------
    Book
        A document-model book. ``metadata`` is populated from the PDF
        metadata dictionary: ``title``, ``author``, ``creator`` (as
        ``publisher``), ``subject`` (as ``identifier``), and ``language``
        when present. ``keywords`` are not mapped. Unless the analyzer finds
        reliable chapter boundaries, all content goes into a single chapter
        titled with the PDF title (or empty).

    Raises
    ------
    PDFReadError
        If ``source`` is not a readable PDF (missing file, corrupt file).
    EmptyPDFError
        If the PDF opens but has zero pages (the analyzer already rejects
        this case before extraction runs).
    NoContentError
        If every page is both text-free and image-free (blank document).
    ScannedPDFError
        If the document is classified ``PDFType.SCANNED``; OCR is not yet
        implemented.
    MixedPDFError
        If the document is classified ``PDFType.MIXED``; the extractor
        refuses hidden partial conversion.

    Examples
    --------
    >>> book = extract_book("novel.pdf")
    >>> book.chapters[0].blocks[0]
    Paragraph(text='...')
    """
    doc = _open_document(source)
    try:
        analysis = analyze_pdf(doc)
        if analysis.document_type is PDFType.SCANNED:
            raise ScannedPDFError(
                "The PDF is SCANNED (image-only), but OCR is not implemented "
                "yet; cannot extract text from it."
            )
        if analysis.document_type is PDFType.MIXED:
            raise MixedPDFError(
                "The PDF is MIXED (part text, part images); refusing to "
                "silently convert only part of it. Mixed handling is a later "
                "milestone."
            )
        return _build_book(doc)
    finally:
        _close_if_owned(source, doc)


# --------------------------------------------------------------------------- #
# Document opening (mirrors the analyzer's ownership contract)
# --------------------------------------------------------------------------- #


def _open_document(source: PathLike | pymupdf.Document) -> pymupdf.Document:
    """Open ``source`` as a PyMuPDF document.

    A ``pymupdf.Document`` is returned as-is (the caller owns it); anything
    else is treated as a path, and failures surface as :class:`PDFReadError`.
    """
    if isinstance(source, pymupdf.Document):
        return source
    try:
        return pymupdf.open(str(source))
    except Exception as exc:  # PyMuPDF raises several exception types
        raise PDFReadError(f"Failed to open {source!r} as a PDF") from exc


def _close_if_owned(
    source: PathLike | pymupdf.Document, doc: pymupdf.Document
) -> None:
    """Close ``doc`` when ``extract_book`` opened it itself."""
    if not isinstance(source, pymupdf.Document):
        try:
            doc.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


# --------------------------------------------------------------------------- #
# Book construction
# --------------------------------------------------------------------------- #


def _build_book(doc: pymupdf.Document) -> Book:
    """Build the document-model :class:`Book` from a text PDF."""
    metadata = _build_metadata(doc)
    pages = _extract_pages(doc)
    book = Book(metadata=metadata)
    chapter = Chapter(title=metadata.title)
    _append_blocks(chapter.blocks, pages)
    book.add_chapter(chapter)
    return book


def _build_metadata(doc: pymupdf.Document) -> BookMetadata:
    """Map the PyMuPDF metadata dictionary onto :class:`BookMetadata`.

    PDF "subject" maps to ``BookMetadata.identifier`` so the keyword does
    not silently disappear from the document model. ``language`` is not a
    standard PyMuPDF metadata key, so it is left empty unless present.
    """
    meta = doc.metadata or {}
    return BookMetadata(
        title=meta.get("title", ""),
        author=meta.get("author", ""),
        language=meta.get("language", ""),
        publisher=meta.get("creator", ""),
        identifier=meta.get("subject", ""),
    )


# --------------------------------------------------------------------------- #
# Page extraction
# --------------------------------------------------------------------------- #


def _extract_pages(doc: pymupdf.Document) -> list[list[Paragraph]]:
    """Return one list of :class:`Paragraph` blocks per PDF page.

    Pages that carry only repeated non-body noise (headers, footers, page
    numbers) may produce an empty list; the surrounding ``PageBreak``
    markers are still emitted by the caller so page boundaries survive.
    """
    pages: list[list[Paragraph]] = []
    for index in range(doc.page_count):
        page = doc.load_page(index)
        try:
            lines = _extract_lines(page)
        except Exception as exc:
            raise PDFReadError(
                f"Failed to extract text from page {index + 1} of the PDF"
            ) from exc
        pages.append(lines)
    return pages


def _extract_lines(page: pymupdf.Page) -> list[Paragraph]:
    """Extract, normalize, and paragraphize the text of one page.

    The raw lines (see :func:`_raw_lines`) are grouped into paragraphs with
    a deterministic geometric rule, documented on :func:`_same_paragraph`:

    * consecutive lines that sit close together and share a left margin are
      merged into one paragraph,
    * a line with a clear left indent starts a new paragraph,
    * a line that sits unusually high (roughly half a blank line or more
      above the previous one) also starts a new paragraph,
    * a very short line is never glued onto the surrounding text.

    Paragraph text is reflowed by joining the merged lines with a single
    space, which also normalizes the accidental missing-space artifacts that
    ``"dict"`` mode produces at line breaks.
    """
    raw = _raw_lines(page)
    if not raw:
        return []

    gap_threshold = PARAGRAPH_GAP_FACTOR * _median_line_height(raw)
    paragraphs: list[Paragraph] = []
    current: list[RawLine] = []
    for line in raw:
        if len(line.text) <= SHORT_LINE_THRESHOLD:
            # A very short line (page number, terse footer) is never glued
            # onto the surrounding text; it stands alone on both sides.
            if current:
                paragraphs.append(Paragraph(text=_reflow(current)))
                current = []
            paragraphs.append(Paragraph(text=_reflow([line])))
            continue
        if current and not _same_paragraph(current[-1], line, gap_threshold):
            paragraphs.append(Paragraph(text=_reflow(current)))
            current = []
        current.append(line)
    if current:
        paragraphs.append(Paragraph(text=_reflow(current)))

    for paragraph in paragraphs:
        paragraph.text = _collapse_spaces(paragraph.text)
    return paragraphs


# --------------------------------------------------------------------------- #
# Raw line collection (dict layout, order preserved as provided)
# --------------------------------------------------------------------------- #


class RawLine:
    """A single physical text line on a page."""

    __slots__ = ("text", "x0", "y0", "y1")

    def __init__(
        self, text: str, *, x0: float, y0: float, y1: float
    ) -> None:
        self.text = text
        self.x0 = x0
        self.y0 = y0
        self.y1 = y1


def _raw_lines(page: pymupdf.Page) -> list[RawLine]:
    """Return the visible text lines of ``page`` in PyMuPDF's "dict" order.

    Lines are collected from the PyMuPDF ``"dict"`` layout, which groups
    text spans into physical lines and orders blocks in the PDF content
    stream. We deliberately do **not** re-sort these lines (for example by
    ``(y, x)`` position): that kind of layout reconstruction -- including
    multi-column handling -- belongs to a later milestone, and a naive
    global sort would interleave columns line by line. Reading order is
    therefore only as reliable as the order PyMuPDF's "dict" mode provides.
    """
    lines_out: list[RawLine] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:  # 0 == text; skip images
            continue
        for line in block.get("lines", []):
            x0, y0, _x1, y1 = line["bbox"]
            text = _normalize_line(_join_spans(line))
            if not text:
                continue
            candidate = RawLine(text, x0=x0, y0=y0, y1=y1)
            # Some PDFs draw text twice at the exact same position to fake a
            # bold weight. Only a byte-identical line that *overlaps* the
            # previous one is a duplicate; identical lines stacked at
            # different baselines (stanzas, table rows) are all kept.
            if lines_out and _overlaps(lines_out[-1], candidate):
                continue
            lines_out.append(candidate)
    return lines_out


def _join_spans(line: dict) -> str:
    """Join the spans of a PyMuPDF text line.

    The spans are concatenated without inserting separators: PyMuPDF's span
    bboxes carry the positional gaps, and most text spans preserve the
    inter-word spaces of the source encoding, so joining verbatim best
    preserves what the page actually says.
    """
    return "".join(span.get("text", "") for span in line.get("spans", []))


def _overlaps(upper: RawLine, lower: RawLine) -> bool:
    """Return whether two lines overlap vertically and are byte-identical.

    This is the conservative "drawn twice for emphasis" test: the two lines
    must carry the same text *and* their vertical extents must intersect.
    """
    if upper.text != lower.text:
        return False
    return upper.y0 < lower.y1 and lower.y0 < upper.y1


# --------------------------------------------------------------------------- #
# Paragraph grouping
# --------------------------------------------------------------------------- #


def _same_paragraph(
    upper: RawLine, lower: RawLine, gap_threshold: float
) -> bool:
    """Return whether ``lower`` continues the paragraph started by ``upper``.

    The rule is fully deterministic and intentionally conservative. A line
    is *not* a continuation (i.e. starts a new paragraph) when either:

    1. The vertical gap between the two lines exceeds ``gap_threshold``
       (``PARAGRAPH_GAP_FACTOR *`` the page's median line height) -- roughly
       half a blank line or more -- which separates distinct paragraphs.
    2. Its left edge sits at least ``PARAGRAPH_INDENT_PT`` points to the
       right of ``upper``'s left edge -- an indented first line.

    The caller isolates very short lines (page numbers, terse footers)
    before this rule runs. This is *not* semantic detection: no heading,
    chapter, header, or footer logic lives here.
    """
    if lower.y0 - upper.y1 > gap_threshold:
        return False
    if lower.x0 - upper.x0 >= PARAGRAPH_INDENT_PT:
        return False
    return True


def _median_line_height(lines: list[RawLine]) -> float:
    """Return the median height of ``lines`` (``1.0`` if empty)."""
    if not lines:
        return 1.0
    heights = sorted(line.y1 - line.y0 for line in lines)
    middle = len(heights) // 2
    if len(heights) % 2 == 1:
        return heights[middle]
    return (heights[middle - 1] + heights[middle]) / 2.0


def _reflow(lines: list[RawLine]) -> str:
    """Join the lines of one paragraph into a single normalized string.

    The ``"dict"`` layout drops the inter-word space at most line breaks, so
    continuation lines are joined with a single space. A paragraph-start
    indent of up to four characters is stripped. Leading and trailing
    whitespace is removed.
    """
    parts: list[str] = []
    for index, line in enumerate(lines):
        text = line.text
        if index == 0:
            text = RE_LEADING_SPACES.sub("", text)
        parts.append(text)
    return " ".join(parts).strip()


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def _normalize_line(line: str) -> str:
    """Normalize one logical text line.

    * line endings are normalized to ``\\n``,
    * tabs and non-breaking spaces become regular spaces,
    * repeated spaces are collapsed.
    """
    line = line.replace("\r\n", "\n").replace("\r", "\n")
    line = line.replace("\t", " ").replace("\xa0", " ")
    return _collapse_spaces(line).strip()


def _collapse_spaces(text: str) -> str:
    """Replace every run of whitespace with a single space.

    A document may contain meaningful vertical spacing (for example between
    paragraphs); that is preserved at the paragraph level, but inside a
    paragraph repeated whitespace is an artifact of PDF layout.
    """
    return RE_SPACE.sub(" ", text)


# --------------------------------------------------------------------------- #
# Block assembly
# --------------------------------------------------------------------------- #


def _append_blocks(
    blocks: list[DocumentBlock], pages: list[list[Paragraph]]
) -> None:
    """Append the per-page paragraphs and ``PageBreak`` markers to ``blocks``.

    Every page boundary becomes exactly one :class:`PageBreak` marker,
    placed as the first block of each page after the first.
    """
    for page_index, paragraphs in enumerate(pages):
        if page_index > 0:
            blocks.append(PageBreak())
        blocks.extend(paragraphs)