"""OCR-aware structural reconstruction (Milestone 3.6).

This module is the M3.5 -> M3.6 boundary. It consumes the per-page routing
results (:class:`~kindle_converter.pdf.processing.PageProcessingResult`) and
integrates them into the M2 structural reconstruction so OCR-derived text is
no longer stranded as isolated strings: it becomes body paragraphs in the
same immutable :class:`~kindle_converter.pdf.reconstruction.ReconstructedDocument`
the M2 pipeline produces.

Integration strategy
--------------------
The M2 stack is reused unchanged for native text:

    PageLayout -> reading order -> paragraphs -> headings -> header/footer
               -> integrated ReconstructedDocument -> chapters

This module is therefore **not** a new detection algorithm. It combines:

* **native paragraphs** -- the existing M2.3 reconstruction of the
  deduplicated M2.1 layout, byte-identical to the historical text-PDF
  pipeline (:func:`reconstruct_layout`), and
* **OCR paragraphs** -- new paragraphs reconstructed from each page's
  cleaned OCR text (``PageProcessingResult.ocr_text``) with
  :func:`~kindle_converter.pdf.paragraphs.reconstruct_ocr_paragraphs`,
  which reuses the same conservative single-space/dehyphenation joining
  rule as M2.3.

Per-page composition
--------------------
Within a page the element order is deterministic: **native paragraphs
first, then OCR paragraphs**. This follows the M3.5 routing contract:

* ``TEXT``    -> native only (OCR is never run for these pages, so there is
  nothing to add).
* ``SCANNED`` -> OCR only (native text is ``None`` for these pages).
* ``MIXED``   -> native text is preserved **and** the page's OCR text is
  kept separately *after* it. The M3.5 policy that native and OCR text are
  never concatenated or merged is preserved: the two sources appear as
  distinct paragraphs tagged ``TextSource.NATIVE`` and ``TextSource.OCR``,
  and this module never de-duplicates or cross-contaminates them. Full-page
  OCR on a MIXED page may re-recognize native text; that overlap is kept
  and resolved by no fuzzy de-duplication (a later milestone).

Provenance honesty
------------------
OCR paragraphs carry ``TextSource.OCR`` and **no geometry**: their source
lines/blocks, line-order indices, and reading-order indices are all empty.
No bbox, font, or reading-order metadata is fabricated for OCR text. As a
consequence OCR paragraphs are never heading/furniture candidates: heading
(M2.4) and header/footer (M2.5) classification run only over the native
paragraph layout, and chapter detection (M2.9) only ever promotes native
headings. OCR text contributes body paragraphs only.

The ``layout`` argument supplies the native geometry; when it is omitted,
native contribution is skipped (an OCR-only reconstruction), and
geometry-free page shells keep the page count and order honest.

Determinism: given the same inputs, the same integrated document is always
produced. No randomization, timestamps, external services, OCR, NLP, or
machine learning is introduced here -- OCR input is simply consumed as the
M3.5 layer already produced it.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..document import Book, BookMetadata
from .headings import HeadingLayout
from .header_footer import HeaderFooterLayout
from .images import ImageExtractionResult
from .layout import LayoutPage, PageLayout
from .models import PDFType
from .paragraphs import (
    ParagraphLayout,
    ParagraphPage,
    ReconstructedParagraph,
    reconstruct_ocr_paragraphs,
)
from .processing import PageProcessingResult, process_pages
from .reconstruction import (
    ReconstructedDocument,
    build_reconstructed_document,
    reconstruct_layout,
    reconstructed_document_to_book,
)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def reconstruct_processed_pages(
    results: Sequence[PageProcessingResult],
    *,
    layout: PageLayout | None = None,
    images: ImageExtractionResult | None = None,
) -> ReconstructedDocument:
    """Reconstruct one document model from M3.5 per-page routing results.

    Runs the full M2 stack over the (deduplicated) native ``layout`` --
    byte-identical to :func:`reconstruct_layout` for text-only documents --
    and appends, per page, the paragraphs reconstructed from that page's
    cleaned OCR text, in page order (native paragraphs first, then OCR
    paragraphs). OCR paragraphs are always body paragraphs (``NATIVE``
    heading/header-footer classification never applies to OCR text), so a
    pure-readable structural body sequence is produced for TEXT, SCANNED,
    and MIXED documents alike.

    Parameters
    ----------
    results:
        The M3.5 results for every page, in 1-based page order 1..N (exactly
        the output of :func:`process_pages`). Page numbers must be the
        contiguous sequence ``1, 2, ..., N``.
    layout:
        Optional native :class:`PageLayout` (typically the deduplicated
        M2.1 layout, as produced by
        ``deduplicate_layout(extract_page_layout(doc))``). When provided,
        every ``NATIVE`` page contributes its full M2 reconstruction.
        When ``None``, native contribution is skipped and the result is an
        OCR-only reconstruction: only ``OCR`` paragraphs are contributed
        and pages without OCR text stay empty. The pipeline caller always
        supplies the layout so text PDFs remain byte-identical to M2.
    images:
        Optional M2.13 :class:`ImageExtractionResult`, attached per page by
        the integration layer exactly as
        :func:`~kindle_converter.pdf.reconstruction.reconstruct_layout`
        attaches it. ``None`` (default) keeps text-only behavior.

    Returns
    -------
    ReconstructedDocument
        The integrated document: page boundaries/order preserved, native
        elements in M2.3 reading order followed by OCR paragraphs, M2.9
        chapter detection attached (over native headings only).

    Raises
    ------
    TypeError
        If ``results`` is not a sequence of ``PageProcessingResult``, or
        ``layout`` is not a ``PageLayout``.
    ValueError
        If ``results`` is empty, page numbers are not the contiguous
        ``1..N`` sequence in order, or ``layout``'s page count does not
        match ``len(results)``.
    """
    results = _validate_results(results)
    layout = _validate_layout(layout, len(results))

    native_document: ReconstructedDocument | None = None
    native_paragraph_layout: ParagraphLayout | None = None
    native_headings: HeadingLayout | None = None
    native_furniture: HeaderFooterLayout | None = None
    if layout is not None:
        (
            native_document,
            native_paragraph_layout,
            native_headings,
            native_furniture,
        ) = reconstruct_layout(layout, images=images)

    combined_paragraphs = _combine_paragraph_pages(
        results, native_paragraph_layout
    )
    headings = (
        native_headings
        if native_headings is not None
        else HeadingLayout(pages=())
    )
    furniture = (
        native_furniture
        if native_furniture is not None
        else HeaderFooterLayout(pages=(), detected=())
    )

    document = build_reconstructed_document(
        combined_paragraphs, headings, furniture, images=images
    )
    # M2.9: Chapter detection on the integrated representation (shared with
    # the native path; OCR paragraphs are body text and never headings).
    from .chapters import detect_chapters

    chapters = detect_chapters(document)
    return ReconstructedDocument(
        pages=document.pages,
        paragraphs=document.paragraphs,
        headings=document.headings,
        header_footer=document.header_footer,
        chapters=chapters,
    )


def processed_pages_to_book(
    results: Sequence[PageProcessingResult],
    metadata: BookMetadata,
    *,
    layout: PageLayout | None = None,
    images: ImageExtractionResult | None = None,
) -> Book:
    """Adapt M3.5 per-page routing results into a document-model ``Book``.

    A thin wrapper over :func:`reconstruct_processed_pages` +
    :func:`~kindle_converter.pdf.reconstruction.reconstructed_document_to_book`:
    identical provenance and ordering rules, mapped to the format-independent
    document model (one chapter, ``PageBreak`` per page boundary, headings
    at the generic level, OCR paragraphs as body paragraphs).

    Parameters
    ----------
    results:
        The M3.5 per-page routing results in page order (see
        :func:`reconstruct_processed_pages`).
    metadata:
        The document-model :class:`BookMetadata` (typically from M2.12
        ``extract_pdf_metadata``/``resolve_metadata``).
    layout, images:
        Passed through to :func:`reconstruct_processed_pages`.

    Returns
    -------
    Book
        A document-model book whose body includes OCR-derived paragraphs
        for SCANNED/MIXED pages alongside native reconstruction.
    """
    document = reconstruct_processed_pages(results, layout=layout, images=images)
    return reconstructed_document_to_book(document, metadata)


# --------------------------------------------------------------------------- #
# Internal composition helpers
# --------------------------------------------------------------------------- #


def _combine_paragraph_pages(
    results: Sequence[PageProcessingResult],
    native_paragraph_layout: ParagraphLayout | None,
) -> ParagraphLayout:
    """Merge native + OCR paragraphs into one per-page layout.

    Per page the composition follows the M3.5 routing contract documented in
    the module docstring: ``TEXT`` pages contribute native paragraphs only,
    ``SCANNED`` pages contribute OCR paragraphs only (their native text is
    ``None`` by the M3.5 layer, so nothing is reconstructed even when the
    native layout happens to carry stray blocks), and ``MIXED`` pages keep
    native paragraphs first with the OCR paragraphs after them. The page
    reference stays the native geometry page when one exists so page
    boundaries and geometry are never lost.
    """

    native_pages: dict[int, ParagraphPage] = {}
    if native_paragraph_layout is not None:
        for page in native_paragraph_layout.pages:
            native_pages[page.page_number] = page

    combined: list[ParagraphPage] = []
    for result in results:
        page_number = result.page_number
        ocr_paragraphs = _ocr_paragraphs_for_page(result)
        native_page = native_pages.get(page_number)
        paragraphs = ocr_paragraphs
        if (
            result.classification is not PDFType.SCANNED
            and native_page is not None
        ):
            paragraphs = native_page.paragraphs + paragraphs
        combined.append(
            ParagraphPage(
                page=(
                    native_page.page
                    if native_page is not None
                    else _shell_page(page_number)
                ),
                paragraphs=paragraphs,
            )
        )
    return ParagraphLayout(pages=tuple(combined))


def _ocr_paragraphs_for_page(
    result: PageProcessingResult,
) -> tuple[ReconstructedParagraph, ...]:
    """Reconstruct OCR paragraphs for one result (empty when no OCR)."""
    if result.ocr_text is None:
        return ()
    return reconstruct_ocr_paragraphs(result.ocr_text, result.page_number)


def _shell_page(page_number: int) -> LayoutPage:
    """A geometry-free page shell for pages with no native layout.

    Width/height ``0.0`` marks "unknown geometry"; only the page number is
    meaningful. Used when ``layout`` is omitted and a page contributes OCR
    (or nothing at all) but must still exist to preserve page count/order.
    """
    return LayoutPage(
        page_number=page_number,
        page_width=0.0,
        page_height=0.0,
        blocks=(),
    )


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _validate_results(
    results: object,
) -> Sequence[PageProcessingResult]:
    """Return ``results`` validated as 1..N contiguous page routing results."""
    if not isinstance(results, Sequence):
        raise TypeError(
            "results must be a sequence of PageProcessingResult (from "
            f"process_pages), got "
            f"{type(results).__name__ if results is not None else 'None'}"
        )
    if len(results) == 0:
        raise ValueError("results requires at least one page")
    for index, result in enumerate(results):
        if not isinstance(result, PageProcessingResult):
            raise TypeError(
                "results must contain only PageProcessingResult values, got "
                f"{type(result).__name__ if result is not None else 'None'} "
                f"at index {index}"
            )
        expected = index + 1
        if result.page_number != expected:
            raise ValueError(
                "results must be the contiguous 1..N page sequence in "
                f"order; expected page {expected} at index {index}, got "
                f"{result.page_number}"
            )
    return results


def _validate_layout(layout: object, page_count: int) -> PageLayout | None:
    """Return ``layout`` validated (or ``None`` for OCR-only mode)."""
    if layout is None:
        return None
    if not isinstance(layout, PageLayout):
        raise TypeError(
            f"layout must be a PageLayout (from extract_page_layout), got "
            f"{type(layout).__name__}"
        )
    if len(layout.pages) != page_count:
        raise ValueError(
            f"layout has {len(layout.pages)} pages but results describes "
            f"{page_count}"
        )
    return layout