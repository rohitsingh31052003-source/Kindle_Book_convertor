"""End-to-end PDF -> EPUB / Book conversion pipeline (Milestones 1.5 + 3.6).

This module is the thin orchestration boundary that wires the existing
milestone components together:

    PDF -> analyze_pdf -> extract_book -> build_epub -> EPUB
    PDF -> analyze_pdf -> process_pages -> reconstruct_processed_pages
        -> reconstructed_document_to_book -> Book   (M3.6, OCR-aware)

It contains **no** PDF parsing, text extraction, or EPUB generation logic
of its own; each stage delegates to the existing public API:

* :func:`kindle_converter.pdf.analyze_pdf` -- first-pass classification,
* :func:`kindle_converter.pdf.extract_book` -- document-model extraction,
* :func:`kindle_converter.epub.build_epub` -- EPUB rendering.

The original EPUB pipeline's only added responsibility is an explicit
supported-input decision: it uses the analysis result to refuse scanned and
mixed PDFs by raising the extractor's own exceptions before extraction runs.

``convert_pdf_to_book`` (M3.6) additionally accepts scanned and mixed PDFs:
it routes every page through the M3.5 processing layer (native text for
TEXT pages, OCR for SCANNED pages, both kept separate for MIXED pages) and
feeds the results into an OCR-aware structural reconstruction that reuses
the M2 stack for native text and adds OCR-derived body paragraphs.
"""

from __future__ import annotations

import os

import pymupdf

from .document import Book
from .epub import build_epub
from .pdf import (
    MixedPDFError,
    PDFReadError,
    ScannedPDFError,
    analyze_pdf,
    extract_book,
)
from .pdf.layout import extract_page_layout
from .pdf.metadata import extract_pdf_metadata
from .pdf.models import PDFAnalysis, PDFType
from .pdf.ocr import OCREngine
from .pdf.processing import PageRenderer, process_pages
from .pdf.reconstruction import (
    deduplicate_layout,
    reconstructed_document_to_book,
)
from .pdf.renderer import DEFAULT_RENDER_DPI
from .pdf.structural import reconstruct_processed_pages

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


def convert_pdf_to_book(
    source: Source,
    engine: OCREngine,
    *,
    renderer: PageRenderer | None = None,
    dpi: int | float = DEFAULT_RENDER_DPI,
) -> Book:
    """Convert a PDF into a document-model ``Book``, OCR-aware (Milestone 3.6).

    Unlike :func:`convert_pdf_to_epub` (which refuses scanned and mixed
    PDFs until M4), this entry point handles **all** classifications:

    * ``TEXT`` pages use native extraction and the full M2 structural
      reconstruction (byte-identical to :func:`extract_book`).
    * ``SCANNED`` pages are rendered, OCR'd, and cleaned; the OCR text is
      reconstructed into body paragraphs.
    * ``MIXED`` pages keep native text and OCR text separately: native
      paragraphs are emitted first (M2 reconstruction), then the page's OCR
      paragraphs, never concatenated or de-duplicated.

    The PDF is opened once and shared by analysis, native layout extraction,
    and any rendering. OCR runs through the injected ``engine`` exactly like
    :func:`~kindle_converter.pdf.process_pages`; no hidden engine is ever
    created, so this function is testable without Tesseract.

    Parameters
    ----------
    source:
        Either a path to a PDF file or an already-open PyMuPDF ``Document``.
        An open ``Document`` stays owned by the caller: it is never mutated
        and never closed by this function.
    engine:
        An :class:`~kindle_converter.pdf.ocr.OCREngine` used for every page
        that needs OCR.
    renderer:
        Optional :class:`~kindle_converter.pdf.processing.PageRenderer`;
        defaults to the M3.2 :func:`render_page`. Injectable for
        deterministic rendering tests.
    dpi:
        Rendering resolution for OCR pages (validated eagerly).

    Returns
    -------
    Book
        A document-model book: one chapter, ``PageBreak`` per page boundary,
        native headings at the generic heading level, and OCR-derived body
        paragraphs for scanned/mixed pages.

    Raises
    ------
    TypeError
        If ``engine`` is not an ``OCREngine``, ``renderer`` is not callable.
    ValueError
        If the PDF has zero pages or ``dpi`` is out of range.
    PDFReadError
        If ``source`` is not a readable PDF.
    EmptyPDFError
        If the PDF opens but has zero pages.
    PDFRenderingError
        If an OCR page cannot be rasterized (from the M3.2 renderer).
    OCRError (including ``OCREngineUnavailableError``)
        If OCR fails on any page, with that page's number in the message.

    Examples
    --------
    >>> book = convert_pdf_to_book("scan.pdf", engine=TesseractEngine())
    >>> book.chapters[0].blocks[0]
    Paragraph(text='...')  # OCR-derived body text
    """
    doc = _open_document(source)
    try:
        analysis = analyze_pdf(doc)
        layout = deduplicate_layout(extract_page_layout(doc))
        results = process_pages(
            doc,
            analysis,
            engine=engine,
            renderer=renderer,
            dpi=dpi,
            layout=layout,
        )
        document = reconstruct_processed_pages(results, layout=layout)
        metadata = extract_pdf_metadata(doc)
        return reconstructed_document_to_book(document, metadata)
    finally:
        _close_if_owned(source, doc)


def _open_document(source: Source) -> pymupdf.Document:
    """Open ``source`` as a PyMuPDF document (caller-owned).

    A ``pymupdf.Document`` is returned as-is (the caller owns it); anything
    else is treated as a path, and failures surface as :class:`PDFReadError`.
    """
    if isinstance(source, pymupdf.Document):
        return source
    try:
        return pymupdf.open(str(source))
    except Exception as exc:  # PyMuPDF raises several exception types
        raise PDFReadError(f"Failed to open {source!r} as a PDF") from exc


def _close_if_owned(source: Source, doc: pymupdf.Document) -> None:
    """Close ``doc`` when ``convert_pdf_to_book`` opened it itself."""
    if not isinstance(source, pymupdf.Document):
        try:
            doc.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


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