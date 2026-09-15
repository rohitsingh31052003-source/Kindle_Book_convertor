"""PDF page rendering (Milestone 3.2).

This module renders PDF pages into raster images (PyMuPDF pixmaps) at an
explicit, deterministic DPI. It is the rendering primitive that the future
OCR stage (M3.3) will consume; it performs **no** OCR and **no** image
preprocessing (no grayscale conversion, thresholding, deskewing, cropping,
or any other enhancement): it is strictly ``PDF page -> raster image``.

The renderer follows the package's established ownership and error-handling
conventions (mirroring the ``analyzer`` / ``layout`` / ``images`` modules):

* ``source`` is either a path or an already-open PyMuPDF ``Document``. With a
  path, the file is opened and closed inside the call; with a ``Document``
  the caller keeps ownership, and the document is never closed or mutated.
* Unreadable PDFs surface as :class:`~kindle_converter.pdf.analyzer.PDFReadError`;
  documents with zero pages surface as
  :class:`~kindle_converter.pdf.analyzer.EmptyPDFError` when rendered as a
  whole (``render_pages``).
* Invalid arguments (DPI out of range, bad page number) raise
  :class:`ValueError`, matching the package's argument-validation style.
* A page that cannot be rasterized raises :class:`PDFRenderingError` with the
  affected (1-based) page number in the message.

Page numbers are **1-based** physical PDF page indices, matching the rest of
the ``kindle_converter.pdf`` package.

Page rotation is handled by PyMuPDF's page renderer: a page whose ``/Rotate``
entry is set is rendered in its *displayed* orientation (portrait/landscape
follow the rotated page, not the raw media box), so no custom rotation logic
is introduced here. The reported ``width``/``height`` are the actual rendered
pixmap dimensions, which are deterministic for a given page and DPI.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pymupdf

from .analyzer import EmptyPDFError, PDFReadError

PathLike = str | os.PathLike[str]
Source = PathLike | pymupdf.Document

# --------------------------------------------------------------------------- #
# Rendering resolution constants
# --------------------------------------------------------------------------- #

#: Default rendering resolution. 300 DPI is the conventional minimum for
#: reliable OCR of printed text, so it is the default for the rendering layer
#: that M3.3 will consume.
DEFAULT_RENDER_DPI = 300

#: Lowest accepted DPI: at 72 DPI one PDF point maps to one pixel, the
#: smallest resolution that preserves the page's geometric detail 1:1.
MIN_RENDER_DPI = 72

#: Highest accepted DPI. Above 600 DPI the raster grows quadratically while
#: OCR gain is negligible, so higher values are rejected instead of silently
#: producing enormous in-memory images.
MAX_RENDER_DPI = 600

# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class PDFRenderingError(Exception):
    """Raised when a PDF page cannot be rendered to a raster image."""


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """One PDF page rendered to a raster image (immutable value object).

    ``image`` is the PyMuPDF pixmap: an RGB raster of ``width`` x ``height``
    pixels with three color components per pixel and no alpha channel.
    ``width`` and ``height`` are properties that report the pixmap's own
    dimensions (the actual, deterministic output of the renderer).

    ``page_number`` is the 1-based physical PDF page index.
    """

    page_number: int
    dpi: int | float
    image: pymupdf.Pixmap

    @property
    def width(self) -> int:
        """Rendered width in pixels."""
        return self.image.width

    @property
    def height(self) -> int:
        """Rendered height in pixels."""
        return self.image.height


def render_page(
    source: Source,
    page_number: int,
    dpi: int | float = DEFAULT_RENDER_DPI,
) -> RenderedPage:
    """Render a single PDF page to a raster image.

    Parameters
    ----------
    source:
        Either a path to a PDF file or an already-open PyMuPDF ``Document``.
        With a path, the file is opened and closed within this call. With a
        ``Document`` the caller keeps ownership: the document is never
        mutated and never closed here.
    page_number:
        The 1-based physical PDF page index to render.
    dpi:
        Rendering resolution in dots per inch. Must be within
        ``[MIN_RENDER_DPI, MAX_RENDER_DPI]``.

    Returns
    -------
    RenderedPage
        The rasterized page: 1-based page number, the DPI used, and the RGB
        pixmap (whose ``width``/``height`` are the actual rendered pixel
        dimensions). Page rotation is applied by PyMuPDF, so the output
        matches the page's displayed orientation.

    Raises
    ------
    ValueError
        If ``dpi`` is not a finite number within the accepted range, or
        ``page_number`` is not a valid 1-based page index for the document.
    PDFReadError
        If ``source`` is not a readable PDF or the page cannot be loaded.
    PDFRenderingError
        If the page cannot be rasterized.

    Examples
    --------
    >>> page = render_page("book.pdf", page_number=3)
    >>> page.image.tobytes("png")  # PNG bytes for a future OCR stage
    b'...'
    """
    dpi = _validate_dpi(dpi)
    doc = _open_document(source)
    try:
        page = _load_page(doc, page_number)
        return _render_loaded_page(page, page_number=page_number, dpi=dpi)
    finally:
        _close_if_owned(source, doc)


def render_pages(
    source: Source,
    dpi: int | float = DEFAULT_RENDER_DPI,
) -> Iterator[RenderedPage]:
    """Render every page of a PDF, in page order, one page at a time.

    This is a convenience layer over :func:`render_page`. It returns a
    generator so the pages of a large PDF are never held in memory all at
    once: each :class:`RenderedPage` is yielded right after it is rendered.

    Parameters
    ----------
    source:
        Either a path to a PDF file or an already-open PyMuPDF ``Document``.
        When a path is given, the document stays open for the lifetime of the
        generator and is closed when iteration finishes (or the generator is
        closed/abandoned). With a ``Document`` the caller keeps ownership; it
        is never closed or mutated here.
    dpi:
        Rendering resolution in dots per inch (see :func:`render_page`).
        Invalid DPI values are rejected immediately, before iteration starts.

    Returns
    -------
    Iterator[RenderedPage]
        Each page rendered in order, ``page_number`` 1..N.

    Raises
    ------
    ValueError
        If ``dpi`` is not a finite number within the accepted range.
    PDFReadError
        If ``source`` is not a readable PDF or a page cannot be loaded.
    EmptyPDFError
        If the PDF opens but has zero pages (raised when iteration starts).
    PDFRenderingError
        If a page cannot be rasterized.

    Examples
    --------
    >>> for rendered in render_pages("scan.pdf"):
    ...     ocr(rendered)
    """
    _validate_dpi(dpi)
    return _iter_pages(source, dpi)


# --------------------------------------------------------------------------- #
# Argument validation
# --------------------------------------------------------------------------- #


def _validate_dpi(dpi: object) -> int | float:
    """Validate ``dpi`` and return a canonical ``int``/``float``.

    ``dpi`` must be a finite number (``int`` or ``float``, not ``bool``)
    within ``[MIN_RENDER_DPI, MAX_RENDER_DPI]``. Integral values are returned
    as ``int`` so the common case (``dpi=300``) stays an integer on the
    rendered result. Invalid values raise :class:`ValueError`.
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
    if value.is_integer():
        return int(value)
    return value


# --------------------------------------------------------------------------- #
# DPI -> PyMuPDF transform
# --------------------------------------------------------------------------- #


def _build_matrix(dpi: int | float) -> pymupdf.Matrix:
    """PyMuPDF scaling matrix for ``dpi`` (PDF points -> pixels).

    One PDF point is 1/72 inch, so the scale factor is ``dpi / 72`` along
    both axes: ``pixel = point * dpi / 72``. Using the same scale on both
    axes preserves the page aspect ratio and orientation.
    """
    scale = dpi / 72.0
    return pymupdf.Matrix(scale, scale)


# --------------------------------------------------------------------------- #
# Document opening (mirrors the analyzer/layout/images ownership contract)
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


# --------------------------------------------------------------------------- #
# Page loading
# --------------------------------------------------------------------------- #


def _load_page(doc: pymupdf.Document, page_number: int) -> pymupdf.Page:
    """Load the 1-based ``page_number`` of ``doc``.

    Invalid page numbers (non-int, out of range) raise :class:`ValueError`;
    a page that exists but cannot be decoded raises :class:`PDFReadError`.
    """
    if isinstance(page_number, bool) or not isinstance(page_number, int):
        raise ValueError(
            f"page_number must be an int, got {type(page_number).__name__}"
        )
    if page_number < 1 or page_number > doc.page_count:
        raise ValueError(
            f"page_number must be in [1, {doc.page_count}], got {page_number}"
        )
    try:
        return doc.load_page(page_number - 1)
    except Exception as exc:
        raise PDFReadError(
            f"Failed to load page {page_number} of the PDF"
        ) from exc


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _render_loaded_page(
    page: pymupdf.Page, *, page_number: int, dpi: int | float
) -> RenderedPage:
    """Rasterize an already-loaded page into a :class:`RenderedPage`.

    Rendering deliberately does not crop, rotate by hand, or preprocess: it
    uses PyMuPDF's stock full-page rendering, which applies the page's
    ``/Rotate`` entry and produces a deterministic RGB pixmap with no alpha
    channel. Failures surface as :class:`PDFRenderingError` with the affected
    page number in the message.
    """
    try:
        pixmap = page.get_pixmap(
            matrix=_build_matrix(dpi),
            colorspace=pymupdf.csRGB,
            alpha=False,
        )
    except Exception as exc:
        raise PDFRenderingError(
            f"Failed to render page {page_number} of the PDF"
        ) from exc
    return RenderedPage(page_number=page_number, dpi=dpi, image=pixmap)


def _iter_pages(source: Source, dpi: int | float) -> Iterator[RenderedPage]:
    """Generator backing :func:`render_pages`."""
    doc = _open_document(source)
    try:
        if doc.page_count == 0:
            raise EmptyPDFError("PDF has no pages")
        for index in range(doc.page_count):
            try:
                page = doc.load_page(index)
            except Exception as exc:
                raise PDFReadError(
                    f"Failed to load page {index + 1} of the PDF"
                ) from exc
            yield _render_loaded_page(
                page, page_number=index + 1, dpi=dpi
            )
    finally:
        _close_if_owned(source, doc)