"""End-to-end PDF -> EPUB conversion pipeline (Milestone 1.5).

This module is the thin orchestration boundary that wires the existing
milestone components together:

    PDF -> analyze_pdf -> extract_book -> build_epub -> EPUB

It contains **no** PDF parsing, text extraction, or EPUB generation logic
of its own; each stage delegates to the existing public API:

* :func:`kindle_converter.pdf.analyze_pdf` -- first-pass classification,
* :func:`kindle_converter.pdf.extract_book` -- document-model extraction,
* :func:`kindle_converter.epub.build_epub` -- EPUB rendering.

The pipeline's only added responsibility is an explicit supported-input
decision: it uses the analysis result to refuse scanned and mixed PDFs by
raising the extractor's own exceptions before extraction runs.

OCR, layout reconstruction, and chapter detection are later milestones and
deliberately not implemented here.
"""

from __future__ import annotations

import os

import pymupdf

from .epub import build_epub
from .pdf import (
    MixedPDFError,
    ScannedPDFError,
    analyze_pdf,
    extract_book,
)
from .pdf.models import PDFAnalysis, PDFType

PathLike = str | os.PathLike[str]
Source = str | os.PathLike[str] | pymupdf.Document


class PipelineError(Exception):
    """Base class for PDF-to-EPUB pipeline failures.

    This exists to give the pipeline its own error boundary. The current
    pipeline has no failures of its own: every failure is a domain error
    from an existing stage (:class:`PDFReadError`, :class:`EmptyPDFError`,
    :class:`NoContentError`, :class:`ScannedPDFError`,
    :class:`MixedPDFError`, :class:`EPUBGenerationError`, or a filesystem
    ``OSError``) and those exceptions are deliberately propagated
    unchanged so callers can react to the underlying cause. Future
    pipeline-level errors -- those about orchestration rather than a
    single stage -- should subclass this.
    """


def convert_pdf_to_epub(source: Source, output: PathLike) -> None:
    """Convert a text-based PDF into an EPUB and write it to ``output``.

    Parameters
    ----------
    source:
        Either a path to a PDF file (``str``, ``pathlib.Path``, or
        ``os.PathLike``) or an already-open PyMuPDF ``Document``. An open
        ``Document`` stays owned by the caller: it is never mutated and
        never closed by this function.
    output:
        A filesystem path (``str`` / ``pathlib.Path`` / ``os.PathLike``)
        where the ``.epub`` file is written. Parent directories must
        already exist; they are not created. An existing file is
        overwritten.

    Returns
    -------
    None

    Raises
    ------
    PDFReadError
        If ``source`` is not a readable PDF (missing or malformed file).
    EmptyPDFError
        If the PDF opens but has zero pages.
    NoContentError
        If every page is both text-free and image-free.
    ScannedPDFError
        If the document is classified ``PDFType.SCANNED``; OCR is not yet
        implemented.
    MixedPDFError
        If the document is classified ``PDFType.MIXED``; the pipeline
        refuses hidden partial conversion.
    EPUBGenerationError
        If the extracted book cannot be rendered as an EPUB.
    OSError
        If the output file cannot be written (e.g. a missing parent
        directory).

    Examples
    --------
    >>> convert_pdf_to_epub("book.pdf", "book.epub")
    """
    analysis = analyze_pdf(source)
    _require_supported(analysis)
    book = extract_book(source)
    build_epub(book, output)


def _require_supported(analysis: PDFAnalysis) -> None:
    """Reject currently unsupported PDFs, using the analysis result.

    The classification from :func:`analyze_pdf` drives the pipeline's
    supported-input decision explicitly rather than being re-derived: a
    ``SCANNED`` document cannot be converted without OCR, and a ``MIXED``
    document would otherwise be silently converted only in part. Both
    failures are raised with the exact exceptions the extraction stage
    already defines so callers can catch a single, existing exception
    class.
    """
    if analysis.document_type is PDFType.SCANNED:
        raise ScannedPDFError(
            "The PDF is SCANNED (image-only), but OCR is not implemented "
            "yet; cannot convert it to EPUB."
        )
    if analysis.document_type is PDFType.MIXED:
        raise MixedPDFError(
            "The PDF is MIXED (part text, part images); refusing to "
            "convert only part of it. Mixed handling is a later milestone."
        )