"""Deterministic first-pass PDF analysis.

This module inspects a PDF with PyMuPDF and decides whether it is primarily
text-based, primarily image-based (scanned), or a substantial mixture of the
two. It produces a :class:`PDFAnalysis` summary and does **not** extract a
document model: the later extraction/OCR stages consume this analysis.

Thresholds and constants are defined at the top of this module so the
classification logic is explicit and easy to tune against real PDFs.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import replace

import pymupdf

from .models import PDFAnalysis, PDFType, PageAnalysis

# --------------------------------------------------------------------------- #
# Constants (explicit, documented classification thresholds)
# --------------------------------------------------------------------------- #

#: A page must have at least this many characters in its longest text line to
#: count as having "meaningful text". PDFs frequently carry small amounts of
#: incidental text -- page numbers, running headers/footers, or OCR artifacts
#: -- which should not, on their own, make a scanned page look text-based.
#: 24 characters is roughly one short line of prose.
MEANINGFUL_TEXT_CHAR_THRESHOLD = 24

#: The fraction of the page height (from the top and from the bottom) that is
#: treated as the running header/footer band. A page whose text lines all lie
#: inside those bands carries no body text -- only headers, footers, or page
#: numbers -- and so is "not meaningful" regardless of line length.
HEADER_FOOTER_BAND_FRACTION = 0.15

#: A document whose meaningful-text-page fraction is at least this value is
#: classified as ``PDFType.TEXT`` (text on essentially all/most pages).
TEXT_DOCUMENT_THRESHOLD = 0.8

#: A document whose meaningful-text-page fraction is at most this value is
#: classified as ``PDFType.SCANNED``. Fractions in between are ``MIXED``.
SCANNED_DOCUMENT_THRESHOLD = 0.2

#: The fraction of the page area that must be covered by images for the
#: page to be considered image-dominated. A page whose images cover at
#: least this much of the page rectangle is classified as SCANNED (no
#: meaningful text) or MIXED (meaningful text present). Images smaller
#: than this fraction -- for example a decorative illustration on a
#: text page -- do not change the page's classification.
#: Measured as total image bbox area divided by page area.
IMAGE_AREA_RATIO_THRESHOLD = 0.5

# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class PDFAnalysisError(Exception):
    """Base class for errors raised while analyzing a PDF."""


class PDFReadError(PDFAnalysisError):
    """Raised when a PDF cannot be opened or decoded.

    This deliberately surfaces the underlying problem instead of silently
    treating an unreadable file as an empty or text-free document.
    """


class EmptyPDFError(PDFAnalysisError):
    """Raised when the PDF contains no pages."""


class NoContentError(PDFAnalysisError):
    """Raised when the PDF has pages but no text and no images anywhere.

    ``analyze_pdf`` refuses to invent a classification for a document that
    contains literally nothing readable.
    """


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

PathLike = str | os.PathLike[str]


def analyze_pdf(source: PathLike | pymupdf.Document) -> PDFAnalysis:
    """Analyze a PDF and return a structured summary.

    Parameters
    ----------
    source:
        Either a path to a PDF file or an already-open PyMuPDF ``Document``.
        With a path, the file is opened for the duration of the analysis and
        closed before returning. With a ``Document`` the caller keeps
        ownership and responsibility for closing it; the document is never
        mutated.

    Returns
    -------
    PDFAnalysis
        Page counts, text density, a deterministic ``document_type``, and
        the per-page breakdown. ``document_type`` is computed after all
        pages have been analyzed.

    Raises
    ------
    PDFReadError
        If ``source`` is not a readable PDF (missing or malformed file).
    EmptyPDFError
        If the PDF opens but has zero pages.
    NoContentError
        If every page is both text-free and image-free.

    Examples
    --------
    >>> analysis = analyze_pdf("novel.pdf")
    >>> analysis.document_type
    <PDFType.TEXT: 'text'>
    """
    doc = _open_document(source)
    try:
        if doc.page_count == 0:
            raise EmptyPDFError("PDF has no pages")

        rows, valid = _analyze_all_pages(doc)
        if valid == 0:
            raise NoContentError("PDF has no text or images on any page")
        return build_analysis(rows)
    finally:
        _close_if_owned(source, doc)


def build_analysis(pages: Iterable[PageAnalysis]) -> PDFAnalysis:
    """Summarize per-page analysis rows into a :class:`PDFAnalysis`.

    Parameters
    ----------
    pages:
        Per-page rows in page order (typically one per PDF page, produced by
        the page-level analysis). Row ``i`` is assumed to describe PDF page
        ``i``.

    Returns
    -------
    PDFAnalysis
        The summary plus a deterministic ``document_type`` derived from the
        fraction of rows flagged ``has_meaningful_text``. Only the raw text
        of the first row is kept.
    """
    rows = list(pages)
    if not rows:
        raise ValueError("build_analysis requires at least one page")

    page_count = len(rows)
    text_pages = sum(1 for r in rows if r.has_meaningful_text)
    image_pages = sum(1 for r in rows if r.has_image)
    text_density = sum(r.char_count for r in rows) / page_count
    document_type = classify(text_pages / page_count)

    pages_out = [
        row if index == 0 else replace(row, text="")
        for index, row in enumerate(rows)
    ]
    return PDFAnalysis(
        page_count=page_count,
        text_page_count=text_pages,
        image_page_count=image_pages,
        text_density=text_density,
        document_type=document_type,
        pages=pages_out,
    )


def classify(text_page_fraction: float) -> PDFType:
    """Classify a document from the fraction of pages with meaningful text.

    ``text_page_fraction`` must be in ``[0.0, 1.0]``. The mapping is
    deterministic:

    * ``fraction >= TEXT_DOCUMENT_THRESHOLD`` (0.8)  -> ``PDFType.TEXT``
    * ``fraction > SCANNED_DOCUMENT_THRESHOLD`` (0.2) -> ``PDFType.MIXED``
    * otherwise                                        -> ``PDFType.SCANNED``

    A single text page in an otherwise scanned book (roughly 5-20 % of the
    pages) still classifies as SCANNED; a book with a handful of scanned
    plate pages (roughly 20-80 %) classifies as MIXED; a book with
    meaningful text on nearly everything classifies as TEXT.
    """
    if not 0.0 <= text_page_fraction <= 1.0:
        raise ValueError(
            f"text_page_fraction must be in [0.0, 1.0], got {text_page_fraction}"
        )
    if text_page_fraction >= TEXT_DOCUMENT_THRESHOLD:
        return PDFType.TEXT
    if text_page_fraction > SCANNED_DOCUMENT_THRESHOLD:
        return PDFType.MIXED
    return PDFType.SCANNED


# --------------------------------------------------------------------------- #
# Document opening
# --------------------------------------------------------------------------- #


def _open_document(source: PathLike | pymupdf.Document) -> pymupdf.Document:
    """Open ``source`` as a PyMuPDF document.

    ``pymupdf.Document`` inputs are returned as-is (the caller owns them).
    Any other input is treated as a path; failures surface as
    :class:`PDFReadError`.
    """
    if isinstance(source, pymupdf.Document):
        return source
    try:
        return pymupdf.open(str(source))
    except BaseException as exc:  # PyMuPDF raises several exception types
        raise PDFReadError(f"Failed to open {source!r} as a PDF") from exc


def _close_if_owned(
    source: PathLike | pymupdf.Document, doc: pymupdf.Document
) -> None:
    """Close ``doc`` when ``analyze_pdf`` opened it itself."""
    if not isinstance(source, pymupdf.Document):
        try:
            doc.close()
        except BaseException:  # pragma: no cover - best-effort cleanup
            pass


# --------------------------------------------------------------------------- #
# Per-page analysis
# --------------------------------------------------------------------------- #


def _analyze_all_pages(
    doc: pymupdf.Document,
) -> tuple[list[PageAnalysis], int]:
    """Analyze every page of ``doc``.

    Returns ``(rows, valid)`` where ``valid`` counts pages that carry at
    least one image or at least one non-whitespace text character. A page
    that renders entirely blank contributes nothing to the classification.

    Per-page failures (malformed page trees) are surfaced as
    :class:`PDFReadError`.
    """
    rows: list[PageAnalysis] = []
    valid = 0
    for index in range(doc.page_count):
        page = doc.load_page(index)
        try:
            row = _analyze_page(page, index=index)
        except PDFAnalysisError:
            raise
        except Exception as exc:
            raise PDFReadError(
                f"Failed to analyze page {index + 1} of the PDF"
            ) from exc
        if row.has_image or row.char_count > 0:
            valid += 1
        rows.append(row)
    return rows, valid


def _analyze_page(page: pymupdf.Page, *, index: int) -> PageAnalysis:
    """Analyze a single page into a :class:`PageAnalysis` row.

    The proxy used for "page contains body text" is the length of the page's
    longest text line. Why this metric for a first-pass layer:

    * Real prose is a block of lines; the *shortest* line is cut short by
      the right margin while the longest line approximates the natural text
      column width.
    * A page number, running header, or footer is a single short line at the
      page edge. Requiring a *longest* line to reach
      ``MEANINGFUL_TEXT_CHAR_THRESHOLD`` means a scanned page that merely
      has "Page 12" burned in, or a short footer, does not qualify.
    * A thumbnail-sized caption does not qualify either; only a line that
      spans a meaningful width of the page does.

    A second, purely geometric guard catches pages whose *only* text sits in
    the top or bottom ``HEADER_FOOTER_BAND_FRACTION`` of the page (headers,
    footers, folios): those pages get ``has_meaningful_text=False`` even if
    the line would otherwise be long enough.
    """
    lines = _text_lines(page)
    longest = max((_non_ws_count(text) for text, _bbox in lines), default=0)
    raw = _page_text(page)

    meaningful = (
        longest >= MEANINGFUL_TEXT_CHAR_THRESHOLD
        and not _all_lines_in_bands(page, lines)
    )
    text_block_count = _count_text_blocks(page)
    image_count, image_area_ratio = _image_coverage(page)
    classification = _classify_page(meaningful, image_area_ratio)
    return PageAnalysis(
        page_number=index + 1,
        has_image=_has_image(page),
        has_meaningful_text=meaningful,
        char_count=longest,
        text=raw if index == 0 else "",
        text_block_count=text_block_count,
        image_count=image_count,
        image_area_ratio=image_area_ratio,
        classification=classification,
    )


# --------------------------------------------------------------------------- #
# Page classification helpers
# --------------------------------------------------------------------------- #


def _classify_page(
    has_meaningful_text: bool, image_area_ratio: float
) -> PDFType:
    """Classify a single page from meaningful-text and image coverage.

    The classification is deterministic and OCR-independent:

    * **TEXT** -- the page has meaningful native text and images do
      not cover at least ``IMAGE_AREA_RATIO_THRESHOLD`` of the page.
      A page with real prose plus a small illustration stays TEXT.

    * **SCANNED** -- the page has no meaningful native text and is
      image-dominated (images cover at least
      ``IMAGE_AREA_RATIO_THRESHOLD`` of the page), or the page has
      no meaningful text regardless of image coverage. An empty page,
      a page with only whitespace, or a page with only trivial
      fragments (below the meaningful-text threshold) all classify as
      SCANNED. Empty pages are explicitly classified SCANNED rather
      than left unclassified: they carry no extractable text and no
      meaningful content, so they belong in the non-text category.

    * **MIXED** -- the page has meaningful native text *and* images
      cover at least ``IMAGE_AREA_RATIO_THRESHOLD`` of the page.

    Parameters
    ----------
    has_meaningful_text:
        Whether the page contains meaningful native text
        (per the existing meaningful-text heuristic).
    image_area_ratio:
        Fraction of the page rectangle covered by images (0.0-1.0).

    Returns
    -------
    PDFType
        The page-level classification.
    """
    if has_meaningful_text and image_area_ratio < IMAGE_AREA_RATIO_THRESHOLD:
        return PDFType.TEXT
    if has_meaningful_text and image_area_ratio >= IMAGE_AREA_RATIO_THRESHOLD:
        return PDFType.MIXED
    return PDFType.SCANNED


def classify_pages(pages: Iterable[PageAnalysis]) -> PDFType:
    """Derive a document-level classification from per-page classifications.

    This is the document-level classification derived deterministically
    from page-level classifications, as required by M3.1. It complements
    :func:`classify` (which derives from the fraction of pages with
    meaningful text) and is available for later milestones that need
    page-classification-based routing.

    The aggregation rules:

    * All pages TEXT  -> ``PDFType.TEXT``
    * All pages SCANNED -> ``PDFType.SCANNED``
    * Any MIXED pages, or a mix of TEXT and SCANNED -> ``PDFType.MIXED``

    Parameters
    ----------
    pages:
        Per-page analysis rows in page order. Each row's
        ``classification`` field is used.

    Returns
    -------
    PDFType
        The document-level classification derived from page
        classifications.

    Raises
    ------
    ValueError
        If ``pages`` is empty.
    """
    rows = list(pages)
    if not rows:
        raise ValueError("classify_pages requires at least one page")
    classifications = {r.classification for r in rows}
    if classifications == {PDFType.TEXT}:
        return PDFType.TEXT
    if classifications == {PDFType.SCANNED}:
        return PDFType.SCANNED
    return PDFType.MIXED


def _count_text_blocks(page: pymupdf.Page) -> int:
    """Return the number of text blocks (type 0) on ``page``."""
    return sum(
        1 for block in page.get_text("dict")["blocks"] if block.get("type") == 0
    )


def _image_coverage(
    page: pymupdf.Page,
) -> tuple[int, float]:
    """Return ``(image_count, image_area_ratio)`` for ``page``.

    ``image_count`` is the number of image placements from
    ``page.get_image_info()``.

    ``image_area_ratio`` is the total area of all image bounding boxes
    divided by the page area. Overlapping image rects are counted
    separately (their areas sum), which is a conservative upper bound
    on actual covered area. A page with no images returns ``(0, 0.0)``.
    Pages with a non-positive height return ``(0, 0.0)`` to avoid
    division by zero.
    """
    infos = page.get_image_info()
    if not infos:
        return 0, 0.0
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return len(infos), 0.0
    total_image_area = 0.0
    for info in infos:
        bbox = info.get("bbox")
        if bbox and len(bbox) == 4:
            x0, y0, x1, y1 = (float(v) for v in bbox)
            total_image_area += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    ratio = total_image_area / page_area
    # Clamp to [0.0, 1.0] to guard against overlapping rects or
    # floating-point edge cases.
    ratio = min(1.0, max(0.0, ratio))
    return len(infos), ratio


# --------------------------------------------------------------------------- #
# Text extraction helpers
# --------------------------------------------------------------------------- #


def _text_lines(page: pymupdf.Page) -> list[tuple[str, tuple[float, ...]]]:
    """Return ``(text, bbox)`` for every visible text line on ``page``.

    Lines come from PyMuPDF's ``"dict"`` layout, which already groups spans
    into physical lines. Consecutive duplicate span texts within a line are
    collapsed (some PDFs draw text twice to fake a bold weight).
    """
    result: list[tuple[str, tuple[float, ...]]] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = _line_text(line)
            if text.strip():
                result.append((text, tuple(line["bbox"])))
    return result


def _line_text(line: dict) -> str:
    """Join the spans of a PyMuPDF text line, collapsing duplicate spans."""
    parts: list[str] = []
    previous = ""
    for span in line.get("spans", []):
        text = span.get("text", "")
        if text == previous:
            continue
        parts.append(text)
        previous = text
    return "".join(parts)


def _page_text(page: pymupdf.Page) -> str:
    """Return the raw extracted text of ``page``, trimmed."""
    return page.get_text("text").strip()


def _non_ws_count(text: str) -> int:
    """Return the number of non-whitespace characters in ``text``."""
    return sum(1 for ch in text if not ch.isspace())


def _has_image(page: pymupdf.Page) -> bool:
    """Return whether ``page`` embeds at least one image."""
    return len(page.get_image_info()) > 0


def _all_lines_in_bands(
    page: pymupdf.Page, lines: list[tuple[str, tuple[float, ...]]]
) -> bool:
    """Return True when every text line sits in the header/footer bands.

    A page whose lines all fall within the top or bottom
    ``HEADER_FOOTER_BAND_FRACTION`` of the page has no body text. Pages with
    no lines, or with an unknown (non-positive) height, are treated as not
    in the bands so the length threshold alone decides.
    """
    if not lines:
        return False
    height = page.rect.height
    if height <= 0:
        return False
    top_limit = height * HEADER_FOOTER_BAND_FRACTION
    bottom_limit = height * (1 - HEADER_FOOTER_BAND_FRACTION)
    for _text, bbox in lines:
        y_center = (bbox[1] + bbox[3]) / 2
        if top_limit < y_center < bottom_limit:
            return False
    return True