"""Mixed text/image page processing / OCR routing (Milestone 3.5).

This module is the **document-level/page-level routing layer** that decides,
deterministically, how each PDF page should be processed. It wires the
existing M3.1-M3.4 capabilities together:

    PDF
     │
     ▼
    page analysis / classification           (M3.1, reused unchanged)
     │
     ├── TEXT ────► native text extraction   (M2.1 layout, reused)
     │
     ├── SCANNED ─► render → OCR → cleanup    (M3.2 → M3.3 → M3.4, reused)
     │
     └── MIXED ───► native text + render → OCR → cleanup (both preserved)
     │
     ▼
    per-page extracted representation        (this module's result)
     │
     ▼
    future structural reconstruction          (later milestones)

M3.5 is **routing and integration, not structural reconstruction**. Nothing
here reconstructs paragraphs, detects headings, derives reading order,
merges OCR spans with native spans, identifies captions/tables, deduplicates
text, or builds document-model/EPUB elements. Each page yields an immutable
:class:`PageProcessingResult` that keeps native text and OCR text
explicitly distinguishable so a later stage can decide how to combine them.

Routing rules (deterministic, classification-driven)
-----------------------------------------------------

* **TEXT** -- native text extraction only. The page is never rendered and the
  OCR engine is never invoked. ``native_text`` is authoritative.
* **SCANNED** -- the page is rendered (``render_page``), OCR'd
  (``ocr_page`` with the injected engine), and cleaned (``clean_ocr_result``).
  ``native_text`` is not required and stays ``None``.
* **MIXED** -- native text is preserved exactly as extracted (``TEXT``
  path), and the full page is rendered → OCR'd → cleaned. Both sources are
  kept **separately** in the result; they are never concatenated or merged.

Mixed-page strategy (the central M3.5 decision)
-----------------------------------------------

A ``MIXED`` page carries both meaningful native text and image-heavy content
(image coverage >= ``IMAGE_AREA_RATIO_THRESHOLD``). M3.5 cannot yet
determine image-only regions -- that requires geometric alignment between
native spans and OCR output, which is a later structural-reconstruction
concern. The least destructive deterministic strategy that fits the existing
architecture is:

1. Native text of the page is preserved verbatim as ``native_text``.
2. The **whole page** is rendered and OCR'd; the recognized text passes
   through the M3.4 cleanup layer and is stored as ``ocr_text``.
3. ``native_text`` and ``ocr_text`` remain distinct fields -- no destructive
   concatenation, no fuzzy de-duplication, no word-level merge.

Because the full-page OCR inevitably re-recognizes the page's native text
too, ``ocr_text`` may overlap ``native_text``. That overlap is intentional
and is **not** resolved here: removing it requires semantic/geometric
deduplication, which M3.5 explicitly defers to structural reconstruction.

Dependency injection
--------------------

OCR engines are injected exactly like M3.3 (:class:`OCREngine`); no hidden
global engine is ever created, so routing is testable without Tesseract.
Rendering is injected through :class:`PageRenderer` (defaulting to
:func:`render_page`), so tests can count or replace rendering calls.
Native text comes from the existing M2.1 layout path
(:func:`kindle_converter.pdf.layout.extract_page_layout` +
:func:`deduplicate_layout`), which is the same first-pass native representation
the M1/M2 extraction pipeline consumes; structural reconstruction is never run.

Ownership / resource handling
-----------------------------

``source`` follows the package convention: a path (``str``/``os.PathLike``)
or an already-open PyMuPDF ``Document``. With a path the document is opened
once, shared by layout extraction and any rendering, and closed before
returning. With a ``Document`` the caller keeps ownership: it is never
closed or mutated here. Only inferred strings are returned; temporary
rendered rasters are released after each page's OCR.

Page identity is **1-based** and preserved in every result
(``PageAnalysis.page_number`` -> ``PageProcessingResult.page_number``).
Page order is never changed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import pymupdf

from .analyzer import EmptyPDFError, PDFReadError
from .layout import PageLayout, extract_page_layout
from .models import PDFAnalysis, PDFType, PageAnalysis
from .ocr import OCREngine, ocr_page
from .ocr_cleanup import clean_ocr_result
from .reconstruction import deduplicate_layout
from .renderer import (
    DEFAULT_RENDER_DPI,
    MAX_RENDER_DPI,
    MIN_RENDER_DPI,
    RenderedPage,
    render_page,
)

PathLike = str | os.PathLike[str]
Source = PathLike | pymupdf.Document


# --------------------------------------------------------------------------- #
# Dependency-injection protocols / types
# --------------------------------------------------------------------------- #


class PageRenderer(Protocol):
    """Protocol for the rendering step of the routing layer.

    Mirrors :func:`~kindle_converter.pdf.renderer.render_page`'s signature
    ``(source, page_number, dpi=...) -> RenderedPage`` so callers can inject
    a fake/replacement renderer in deterministic tests. The default is the
    real M3.2 :func:`render_page`.
    """

    def __call__(
        self,
        source: Source,
        page_number: int,
        dpi: int | float = DEFAULT_RENDER_DPI,
    ) -> RenderedPage:
        """Render ``page_number`` of ``source`` at ``dpi`` into a raster."""
        ...


# --------------------------------------------------------------------------- #
# Public result type
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PageProcessingResult:
    """The processed representation of one PDF page (immutable value object).

    ``page_number`` is the **1-based** physical PDF page index, carried
    unchanged from the source :class:`~kindle_converter.pdf.models.PageAnalysis`.

    ``classification`` is the M3.1 page-level classification that drove the
    routing (``PDFType.TEXT`` / ``SCANNED`` / ``MIXED``).

    ``native_text`` holds the page's native PDF text, extracted through the
    existing M2.1 layout path, when that path was used; it is ``None`` when
    the route did not use native extraction (e.g. a SCANNED page). An empty
    string means the native path ran but produced no text.

    ``ocr_text`` holds the OCR-derived text after the M3.4 cleanup pass when
    OCR was performed (``SCANNED`` and ``MIXED`` pages); it is ``None`` when
    no OCR was run (``TEXT`` pages). An empty string means OCR ran but
    produced no text -- never silently reinterpreted as a native-text page.

    Native and OCR text are intentionally kept as **distinct fields**; this
    module never concatenates or merges them.
    """

    page_number: int
    classification: PDFType
    native_text: str | None = None
    ocr_text: str | None = None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def process_page(
    source: Source,
    page_analysis: PageAnalysis,
    *,
    engine: OCREngine,
    renderer: PageRenderer | None = None,
    dpi: int | float = DEFAULT_RENDER_DPI,
) -> PageProcessingResult:
    """Process a single PDF page according to its M3.1 classification.

    Parameters
    ----------
    source:
        Either a path to a PDF file or an already-open PyMuPDF ``Document``.
        With a path, the file is opened for the duration of the call and
        closed before returning. With a ``Document`` the caller keeps
        ownership: the document is never mutated and never closed here.
    page_analysis:
        The M3.1 :class:`~kindle_converter.pdf.models.PageAnalysis` for the
        page (its ``page_number`` and ``classification`` drive the routing).
    engine:
        An :class:`~kindle_converter.pdf.ocr.OCREngine` used for every page
        that needs OCR. Injected so callers/tests never depend on a real
        OCR executable.
    renderer:
        Optional :class:`PageRenderer`; defaults to the M3.2
        :func:`render_page`. Injectable for deterministic rendering tests.
    dpi:
        Rendering resolution used for OCR pages. Must be within
        ``[MIN_RENDER_DPI, MAX_RENDER_DPI]``. Ignored when the page does not
        need OCR (still validated eagerly, like the M3.2 renderer contract).

    Returns
    -------
    PageProcessingResult
        The immutable per-page result: 1-based page number, classification,
        native text (when that path ran), and OCR text (when OCR ran).

    Raises
    ------
    TypeError
        If ``page_analysis`` is not a ``PageAnalysis``, ``engine`` is not an
        ``OCREngine``, or ``renderer`` is not callable.
    ValueError
        If the page number is not a valid 1-based index for the document, or
        ``dpi`` is out of range.
    EmptyPDFError
        If the PDF opens but has zero pages.
    PDFReadError
        If ``source`` is not a readable PDF.
    PDFRenderingError
        If an OCR page cannot be rasterized (from the M3.2 renderer).
    OCRError (including ``OCREngineUnavailableError``)
        If OCR fails on the page, with the affected page number in the
        message (from M3.3; never swallowed).

    Examples
    --------
    >>> analysis = analyze_pdf("scan.pdf")
    >>> process_page("scan.pdf", analysis.pages[0], engine=TesseractEngine())
    PageProcessingResult(page_number=1, classification=<PDFType.SCANNED...>, ...)
    """
    _validate_page_analysis(page_analysis)
    renderer = _resolve_renderer(renderer)
    _validate_engine(engine)
    dpi = _validate_dpi(dpi)

    doc = _open_document(source)
    try:
        if doc.page_count == 0:
            raise EmptyPDFError("PDF has no pages")
        page_number = _validate_page_number(page_analysis.page_number, doc)
        page_layout = _extract_layout(doc)
        native_text = (
            None
            if page_analysis.classification is PDFType.SCANNED
            else _page_native_text(page_layout, page_number)
        )
        return _route_page(
            page_number=page_number,
            classification=page_analysis.classification,
            native_text=native_text,
            doc=doc,
            engine=engine,
            renderer=renderer,
            dpi=dpi,
        )
    finally:
        _close_if_owned(source, doc)


def process_pages(
    source: Source,
    analysis: PDFAnalysis,
    *,
    engine: OCREngine,
    renderer: PageRenderer | None = None,
    dpi: int | float = DEFAULT_RENDER_DPI,
    layout: PageLayout | None = None,
) -> list[PageProcessingResult]:
    """Process every page of a PDF, in page order, per-page routing.

    The document is opened **once** and shared by native layout extraction
    and any rendering; pages are processed sequentially in their 1-based PDF
    order and their results are returned in that same order. This is the
    efficient multi-page variant of :func:`process_page`.

    Parameters
    ----------
    source:
        Either a path to a PDF file or an already-open PyMuPDF ``Document``.
    analysis:
        The M3.1 :class:`~kindle_converter.pdf.models.PDFAnalysis` for
        ``source`` (page count and per-page classifications drive routing).
    engine:
        An :class:`~kindle_converter.pdf.ocr.OCREngine` for OCR pages.
    renderer:
        Optional :class:`PageRenderer` (defaults to :func:`render_page`).
    dpi:
        Rendering resolution for OCR pages (validated eagerly).
    layout:
        Optional pre-extracted :class:`~kindle_converter.pdf.layout.PageLayout`
        (typically from ``deduplicate_layout(extract_page_layout(doc))``,
        the same first-pass native representation the M1/M2 pipeline
        consumes). When ``None`` the layout is extracted once inside. This
        lets callers avoid a redundant extraction pass.

    Returns
    -------
    list[PageProcessingResult]
        One immutable result per PDF page, in page order (page 1..N).

    Raises
    ------
    TypeError
        If ``analysis`` is not a ``PDFAnalysis``, ``engine`` is not an
        ``OCREngine``, ``renderer`` is not callable, or ``layout`` is not a
        ``PageLayout``.
    ValueError
        If ``analysis`` has no pages, its ``page_count`` does not match its
        ``pages`` list, ``layout`` has a mismatched page count, the source
        document's page count disagrees with ``analysis``, or ``dpi`` is out
        of range.
    EmptyPDFError
        If the PDF opens but has zero pages.
    PDFReadError
        If ``source`` is not a readable PDF.
    PDFRenderingError
        If an OCR page cannot be rasterized (from the M3.2 renderer).
    OCRError (including ``OCREngineUnavailableError``)
        If OCR fails on any page, with that page's number in the message;
        processing stops at the failure (never silently skipped).

    Examples
    --------
    >>> analysis = analyze_pdf("mixed.pdf")
    >>> results = process_pages("mixed.pdf", analysis, engine=TesseractEngine())
    >>> results[0].classification
    <PDFType.TEXT: 'text'>
    """
    _validate_analysis(analysis)
    renderer = _resolve_renderer(renderer)
    _validate_engine(engine)
    dpi = _validate_dpi(dpi)

    doc = _open_document(source)
    try:
        if doc.page_count == 0:
            raise EmptyPDFError("PDF has no pages")
        if doc.page_count != analysis.page_count:
            raise ValueError(
                f"analysis describes {analysis.page_count} pages but the "
                f"document has {doc.page_count}"
            )
        page_layout = (
            _extract_layout(doc) if layout is None else _validate_layout(layout)
        )
        if len(page_layout.pages) != analysis.page_count:
            raise ValueError(
                f"layout has {len(page_layout.pages)} pages but analysis "
                f"describes {analysis.page_count}"
            )
        results: list[PageProcessingResult] = []
        for page_analysis in analysis.pages:
            page_number = _validate_page_number(
                page_analysis.page_number, doc
            )
            native_text: str | None = (
                None
                if page_analysis.classification is PDFType.SCANNED
                else _page_native_text(page_layout, page_number)
            )
            results.append(
                _route_page(
                    page_number=page_number,
                    classification=page_analysis.classification,
                    native_text=native_text,
                    doc=doc,
                    engine=engine,
                    renderer=renderer,
                    dpi=dpi,
                )
            )
        return results
    finally:
        _close_if_owned(source, doc)


# --------------------------------------------------------------------------- #
# Per-page routing primitive
# --------------------------------------------------------------------------- #


def _route_page(
    *,
    page_number: int,
    classification: PDFType,
    native_text: str | None,
    doc: pymupdf.Document,
    engine: OCREngine,
    renderer: PageRenderer,
    dpi: int | float,
) -> PageProcessingResult:
    """Route one page through the classification-driven pipeline.

    This is where the routing decision lives:

    * ``PDFType.TEXT`` -> native text only (no render, no OCR).
    * ``PDFType.SCANNED`` -> render -> OCR -> cleanup; native text stays
      ``None`` because the route does not require it.
    * ``PDFType.MIXED`` -> native text is preserved **and** the page is
      rendered -> OCR'd -> cleaned; the two sources stay distinct.

    The M3.2/M3.3/M3.4 APIs are reused verbatim: ``renderer(...)`` produces
    a :class:`RenderedPage`, :func:`ocr_page` recognizes it through the
    injected engine, and :func:`clean_ocr_result` applies the M3.4 cleanup.
    Failures are never caught: PDF/rendering/OCR errors propagate with their
    original types and page context (added by the existing layers).
    """
    if classification is PDFType.TEXT:
        return PageProcessingResult(
            page_number=page_number,
            classification=classification,
            native_text=native_text,
        )
    if classification not in (PDFType.SCANNED, PDFType.MIXED):
        raise ValueError(
            f"unsupported page classification {classification!r}"
        )
    rendered = renderer(doc, page_number, dpi=dpi)
    ocr_result = ocr_page(rendered, engine)
    cleaned = clean_ocr_result(ocr_result)
    return PageProcessingResult(
        page_number=page_number,
        classification=classification,
        native_text=native_text,
        ocr_text=cleaned.text,
    )


# --------------------------------------------------------------------------- #
# Native extraction (reuses the M2.1 layout path, no structural reconstruction)
# --------------------------------------------------------------------------- #


def _extract_layout(doc: pymupdf.Document) -> PageLayout:
    """First-pass native layout for ``doc`` (exactly the M1/M2 entry point).

    ``extract_page_layout`` + :func:`deduplicate_layout` is the same
    deduplicated layout the existing extraction pipeline
    (:func:`kindle_converter.pdf.extractor.extract_book`) consumes. This is
    the native-text path M3.5 preserves; no reading order, paragraph,
    heading, or header/footer reconstruction is run here.
    """
    return deduplicate_layout(extract_page_layout(doc))


def _page_native_text(page_layout: PageLayout, page_number: int) -> str:
    """Return the native text of ``page_number`` from a layout.

    The caller must have validated ``page_number`` against the layout's page
    range; ``page_number`` is 1-based and indexes
    ``page_layout.pages[page_number - 1]``.
    """
    return page_layout.pages[page_number - 1].text


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _validate_analysis(analysis: object) -> PDFAnalysis:
    """Return ``analysis`` as a validated :class:`PDFAnalysis`.

    ``page_count`` must be a positive count matching ``len(pages)`` so page
    routing and result counting are unambiguous.
    """
    if not isinstance(analysis, PDFAnalysis):
        raise TypeError(
            "analysis must be a PDFAnalysis (produced by analyze_pdf), got "
            f"{type(analysis).__name__ if analysis is not None else 'None'}"
        )
    if analysis.page_count == 0:
        raise ValueError("analysis requires at least one page")
    if len(analysis.pages) != analysis.page_count:
        raise ValueError(
            f"analysis.page_count ({analysis.page_count}) does not match "
            f"len(analysis.pages) ({len(analysis.pages)})"
        )
    return analysis


def _validate_page_analysis(page_analysis: object) -> PageAnalysis:
    """Return ``page_analysis`` as a validated :class:`PageAnalysis`."""
    if not isinstance(page_analysis, PageAnalysis):
        raise TypeError(
            "page_analysis must be a PageAnalysis (produced by "
            "analyze_pdf/PDFAnalysis.pages), got "
            f"{type(page_analysis).__name__ if page_analysis is not None else 'None'}"
        )
    page_number = page_analysis.page_number
    if isinstance(page_number, bool) or not isinstance(page_number, int):
        raise ValueError(
            f"page_number must be an int, got "
            f"{type(page_number).__name__}"
        )
    if page_number < 1:
        raise ValueError(f"page_number must be >= 1, got {page_number}")
    return page_analysis


def _validate_page_number(page_number: object, doc: pymupdf.Document) -> int:
    """Return ``page_number`` validated as a 1-based index of ``doc``."""
    if isinstance(page_number, bool) or not isinstance(page_number, int):
        raise ValueError(
            f"page_number must be an int, got "
            f"{type(page_number).__name__}"
        )
    if page_number < 1 or page_number > doc.page_count:
        raise ValueError(
            f"page_number must be in [1, {doc.page_count}], got {page_number}"
        )
    return page_number


def _validate_engine(engine: object) -> None:
    """Validate that ``engine`` provides a callable ``recognize(image)``.

    Mirrors M3.3's engine validation so a missing/invalid engine fails
    eagerly and deterministically even before any page needs OCR.
    """
    recognize = getattr(engine, "recognize", None) if engine is not None else None
    if not callable(recognize):
        raise TypeError(
            "engine must be an OCREngine providing recognize(image) -> str, "
            f"got {type(engine).__name__ if engine is not None else 'None'}"
        )


def _resolve_renderer(renderer: object) -> PageRenderer:
    """Return the renderer to use (defaults to the M3.2 ``render_page``)."""
    if renderer is None:
        return render_page
    if not callable(renderer):
        raise TypeError(
            f"renderer must be callable, got "
            f"{type(renderer).__name__ if renderer is not None else 'None'}"
        )
    return renderer


def _validate_layout(layout: object) -> PageLayout:
    """Return ``layout`` as a validated :class:`PageLayout`."""
    if not isinstance(layout, PageLayout):
        raise TypeError(
            "layout must be a PageLayout (produced by "
            "extract_page_layout), got "
            f"{type(layout).__name__ if layout is not None else 'None'}"
        )
    return layout


def _validate_dpi(dpi: object) -> int | float:
    """Validate ``dpi`` against the M3.2 renderer's accepted range.

    Mirror of the renderer's public contract (``MIN_RENDER_DPI`` /
    ``MAX_RENDER_DPI``) so invalid values fail eagerly and deterministically
    even for documents that never need rendering.
    """
    if isinstance(dpi, bool) or not isinstance(dpi, (int, float)):
        raise ValueError(f"dpi must be a number, got {type(dpi).__name__}")
    value = float(dpi)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"dpi must be a finite number, got {dpi!r}")
    if not MIN_RENDER_DPI <= value <= MAX_RENDER_DPI:
        raise ValueError(
            f"dpi must be between {MIN_RENDER_DPI} and "
            f"{MAX_RENDER_DPI}, got {dpi}"
        )
    return dpi


# --------------------------------------------------------------------------- #
# Document opening (mirrors the analyzer/layout/renderer ownership contract)
# --------------------------------------------------------------------------- #


def _open_document(source: Source) -> pymupdf.Document:
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


def _close_if_owned(source: Source, doc: pymupdf.Document) -> None:
    """Close ``doc`` when the public API opened it itself."""
    if not isinstance(source, pymupdf.Document):
        try:
            doc.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass