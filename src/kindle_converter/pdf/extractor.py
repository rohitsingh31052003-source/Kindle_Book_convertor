"""First-pass PDF text extraction (Milestones 1.3 + 2.7).

This module converts a *text-based* PDF into the format-independent
:mod:`kindle_converter.document` book model.

Pipeline: PDF -> :func:`analyze_pdf` (the analyzer) -> layout extraction
(:mod:`kindle_converter.pdf.layout`) -> M2 reconstruction
(:mod:`kindle_converter.pdf.reconstruction`: reading order, paragraphs,
headings, header/footer filtering) ->
:class:`kindle_converter.document.models.Book`.

The extractor knows nothing about EPUB/AZW3 generation: the document
model is the only bridge to the output side.
"""

from __future__ import annotations

import os
import re

import pymupdf

from ..document import Book, BookMetadata
from .analyzer import (
    EmptyPDFError,
    NoContentError,
    PDFReadError,
    analyze_pdf,
)
from .layout import extract_page_layout
from .models import PDFType
from .reconstruction import (
    deduplicate_layout,
    reconstruct_layout,
    reconstructed_document_to_book,
)

PathLike = str | os.PathLike[str]

RE_SPACE = re.compile(r"\s+")

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
    """Build the document-model :class:`Book` from a text PDF.

    The M2.7 integrated reconstruction (reading order, paragraphs,
    headings, header/footer filtering) produces the body content; page
    boundaries map to ``PageBreak`` markers exactly as in M1.
    """
    metadata = _build_metadata(doc)
    layout = deduplicate_layout(extract_page_layout(doc))
    document, _, _, _ = reconstruct_layout(layout)
    return reconstructed_document_to_book(document, metadata)


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
