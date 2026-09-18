"""End-to-end PDF -> Book -> EPUB conversion pipeline (Milestones 1.5 + 4.1).

This module is the thin orchestration boundary that wires the existing
milestone components together. Since M4.1 there is exactly **one** output
path for every kind of PDF, and it always goes through the format-independent
document model::

    PDF -> convert_pdf_to_book -> Book -> build_epub -> EPUB

``convert_pdf_to_book`` (M3.6) accepts every classification: TEXT pages use
the native M2 reconstruction, SCANNED pages are rendered, OCR'd, and cleaned
(M3.2-M3.4), and MIXED pages keep native and OCR text separate (M3.5).
``convert_pdf_to_epub`` (M4.1) therefore converts text, scanned, and mixed
PDFs alike; it is plain composition and contains **no** PDF parsing, text
extraction, OCR, or EPUB generation logic of its own. Each stage delegates to
the existing public API:

* :func:`kindle_converter.pdf.analyze_pdf` -- first-pass classification,
* :func:`kindle_converter.pdf.processing.process_pages` -- per-page routing
  (native text and/or OCR),
* :func:`kindle_converter.pdf.structural.reconstruct_processed_pages` --
  OCR-aware structural reconstruction into the document model,
* :func:`kindle_converter.epub.build_epub` -- EPUB rendering.

The EPUB layer only ever sees a ``Book``: no PDF, OCR, routing, or
reconstruction type crosses that boundary, and no PDF/OCR-specific logic
lives in the EPUB modules.

M4.4 adds an optional, keyword-only ``cover`` to both entry points: a
filesystem path or an in-memory
:class:`~kindle_converter.document.models.Image`, resolved and validated
explicitly through :func:`kindle_converter.document.load_cover` before any
PDF work runs. Covers are never auto-detected; without one, the book (and
its EPUB) is exactly as before.
"""

from __future__ import annotations

import os

import pymupdf

from .document import Book, Image, load_cover
from .epub import build_epub
from .pdf import PDFReadError, analyze_pdf, extract_pdf_images
from .pdf.layout import extract_page_layout
from .pdf.metadata import extract_pdf_metadata
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

    This exists to give the pipeline its own error boundary. The pipeline
    has no failures of its own: every failure is a domain error from an
    existing stage (:class:`PDFReadError`, :class:`EmptyPDFError`,
    :class:`NoContentError`, the M3.2 ``PDFRenderingError``, the M3.3
    ``OCRError`` (including ``OCREngineUnavailableError``),
    :class:`EPUBGenerationError`, or a filesystem ``OSError``) and those
    exceptions are deliberately propagated unchanged so callers can react
    to the underlying cause. Future pipeline-level errors -- those about
    orchestration rather than a single stage -- should subclass this.
    """


def convert_pdf_to_epub(
    source: Source,
    output: PathLike,
    *,
    engine: OCREngine | None = None,
    renderer: PageRenderer | None = None,
    dpi: int | float = DEFAULT_RENDER_DPI,
    cover: Image | PathLike | None = None,
) -> None:
    """Convert any PDF into a reflowable EPUB and write it to ``output``.

    Milestone 4.1 makes EPUB generation the production output path for every
    PDF classification, through the single unified pipeline::

        book = convert_pdf_to_book(source, engine=...)
        build_epub(book, output)

    TEXT, SCANNED, and MIXED documents all produce a
    :class:`~kindle_converter.document.models.Book` first; the EPUB layer
    renders that book and never inspects the PDF. Scanned and mixed
    documents are routed through the M3 OCR-aware processing path, so no OCR
    or reconstruction logic exists on the output side.

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
    engine:
        Optional :class:`~kindle_converter.pdf.ocr.OCREngine` used for every
        page that needs OCR. When ``None`` (default), Tesseract is used
        *lazily*: the built-in engine is only constructed for the first
        SCANNED/MIXED page that actually needs recognition, so text-only
        PDFs never touch the optional ``ocr`` dependencies.
    renderer:
        Optional :class:`~kindle_converter.pdf.processing.PageRenderer`;
        defaults to the M3.2 :func:`render_page`. Injectable for
        deterministic rendering tests.
    dpi:
        Rendering resolution for OCR pages (validated eagerly).
    cover:
        Optional cover for the generated EPUB (M4.4): a filesystem path to a
        supported image (JPEG, PNG, GIF, or SVG) or an already-loaded
        :class:`~kindle_converter.document.models.Image`. Validated through
        :func:`kindle_converter.document.load_cover` before the PDF is even
        opened, so a bad cover fails fast and no output is written. When
        ``None`` (default) the EPUB has no cover, exactly as before.

    Returns
    -------
    None

    Raises
    ------
    CoverError (subclasses)
        If ``cover`` is invalid: a missing or unreadable file, an unsupported
        format, or empty data.
    PDFReadError
        If ``source`` is not a readable PDF (missing or malformed file).
    EmptyPDFError
        If the PDF opens but has zero pages.
    NoContentError
        If every page is both text-free and image-free.
    TypeError
        If ``engine`` does not provide ``recognize(image)`` or ``renderer``
        is not callable (validated by the M3.5 routing layer before any page
        runs).
    OCRError (including ``OCREngineUnavailableError``)
        If OCR fails on a page that needs it (for example when the optional
        OCR dependencies or the Tesseract executable are missing).
    EPUBGenerationError
        If the extracted book cannot be rendered as an EPUB.
    OSError
        If the output file cannot be written (e.g. a missing parent
        directory).

    Examples
    --------
    >>> convert_pdf_to_epub("book.pdf", "book.epub")
    >>> convert_pdf_to_epub("scan.pdf", "scan.epub", engine=TesseractEngine())
    >>> convert_pdf_to_epub("book.pdf", "book.epub", cover="cover.jpg")
    """
    book = convert_pdf_to_book(
        source,
        _resolve_engine(engine),
        renderer=renderer,
        dpi=dpi,
        cover=cover,
    )
    build_epub(book, output)


def convert_pdf_to_book(
    source: Source,
    engine: OCREngine,
    *,
    renderer: PageRenderer | None = None,
    dpi: int | float = DEFAULT_RENDER_DPI,
    cover: Image | PathLike | None = None,
) -> Book:
    """Convert a PDF into a document-model ``Book``, OCR-aware (Milestone 3.6).

    This entry point handles **all** classifications, and is the source of
    every ``Book`` the EPUB layer renders (including from
    :func:`convert_pdf_to_epub`):

    * ``TEXT`` pages use native extraction and the full M2 structural
      reconstruction (byte-identical to :func:`extract_book`).
    * ``SCANNED`` pages are rendered, OCR'd, and cleaned; the OCR text is
      reconstructed into body paragraphs.
    * ``MIXED`` pages keep native text and OCR text separately: native
      paragraphs are emitted first (M2 reconstruction), then the page's OCR
      paragraphs, never concatenated or de-duplicated.

    The PDF is opened once and shared by analysis, native layout extraction,
    image extraction, and any rendering. OCR runs through the injected
    ``engine`` exactly like
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
    cover:
        Optional cover image for the returned ``Book`` (M4.4): a filesystem
        path to a supported image (JPEG, PNG, GIF, or SVG) or an already-loaded
        :class:`~kindle_converter.document.models.Image`. Resolved and
        validated through :func:`kindle_converter.document.load_cover` before
        the PDF is even opened, so a bad cover fails fast and no extraction
        work is wasted. When ``None`` (default) the returned ``Book`` has no
        cover, exactly as before.

    Returns
    -------
    Book
        A document-model book: one chapter, ``PageBreak`` per page boundary,
        native headings at the generic heading level, OCR-derived body
        paragraphs for scanned/mixed pages, the PDF's embedded images
        (M2.13) at their reconstructed document positions, and the validated
        ``cover`` (when one was supplied) on ``Book.cover``.

    Raises
    ------
    CoverError (subclasses)
        If ``cover`` is invalid: a missing or unreadable file, an unsupported
        format, or empty data.
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
    resolved_cover = load_cover(cover) if cover is not None else None
    doc = _open_document(source)
    try:
        analysis = analyze_pdf(doc)
        layout = deduplicate_layout(extract_page_layout(doc))
        # M2.13: embedded images ride along with the reconstructed text so the
        # unified Book keeps them for every classification (the EPUB layer
        # only renders the images it receives).
        images = extract_pdf_images(doc)
        results = process_pages(
            doc,
            analysis,
            engine=engine,
            renderer=renderer,
            dpi=dpi,
            layout=layout,
        )
        document = reconstruct_processed_pages(
            results, layout=layout, images=images
        )
        metadata = extract_pdf_metadata(doc)
        book = reconstructed_document_to_book(document, metadata)
        book.cover = resolved_cover
        return book
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


def _resolve_engine(engine: OCREngine | None) -> OCREngine:
    """Return the OCR engine to use, defaulting to a deferred Tesseract.

    The M3.5 routing layer validates the engine eagerly, so
    :func:`convert_pdf_to_epub` must hand *something* with a
    ``recognize(image)`` method to :func:`convert_pdf_to_book` even for a
    text-only PDF that never calls it. An injected engine is returned
    unchanged; ``None`` defers to Tesseract only for documents that actually
    need OCR.
    """
    if engine is not None:
        return engine
    return _DeferredTesseractEngine()


class _DeferredTesseractEngine:
    """``OCREngine`` that constructs :class:`TesseractEngine` on first use.

    The Tesseract Python wrappers (``pytesseract``/``Pillow``) are an
    optional dependency (the ``ocr`` extra) and the Tesseract executable is
    an external runtime requirement that this project never installs.
    Constructing the built-in engine eagerly would therefore make plain text
    conversion depend on the OCR stack; deferring the construction keeps
    ``TEXT -> EPUB`` working in a base installation while letting SCANNED and
    MIXED documents run real OCR when Tesseract is available. When it is not
    available, the first page that needs recognition raises the M3.3
    :class:`~kindle_converter.pdf.ocr.OCREngineUnavailableError` rather than
    silently producing an empty book.
    """

    __slots__ = ("_engine",)

    def __init__(self) -> None:
        self._engine: OCREngine | None = None

    def recognize(self, image: pymupdf.Pixmap) -> str:
        """Recognize ``image`` with Tesseract, building the engine once."""
        if self._engine is None:
            from .pdf.ocr import TesseractEngine

            self._engine = TesseractEngine()
        return self._engine.recognize(image)