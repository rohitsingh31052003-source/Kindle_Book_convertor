"""Layout-aware, PDF-specific text representation (Milestone 2.1).

This module introduces the *intermediate representation* between PDF
extraction and the later reconstruction stages (reading-order
reconstruction, paragraph reconstruction, heading detection, header/footer
detection, multi-column reconstruction -- M2.2 onward).

It deliberately models the *physical* layout of a page. Nothing here decides
what the text means structurally, and nothing here knows about the
format-independent :mod:`kindle_converter.document` book model: block
geometry, fonts, and extraction order are preserved so that the future
reconstruction stages can make those decisions downstream.

Coordinate convention
---------------------
All geometry uses **PyMuPDF page space**: the origin is the **top-left**
corner of the page, the **x**-axis grows rightwards and the **y**-axis
grows *downwards*. Units are PDF **points** (1/72 inch). A bounding box is
the inclusive extent of its content: ``x1 >= x0`` and ``y1 >= y0`` always
hold, and ``width == x1 - x0``, ``height == y1 - y0``. ``page_width`` and
``page_height`` describe the page rectangle ``(0, 0, page_width,
page_height)`` in the same convention.

Page-number convention
----------------------
Page numbers are **1-based** physical PDF page indices (the first page is
1), matching the rest of the ``kindle_converter.pdf`` package.

"dict" extraction order
-----------------------
Blocks, lines, and spans are produced in the order PyMuPDF's ``"dict"``
layout yields them: the order text is drawn in the PDF content stream. This
is *not* necessarily visual reading order (multi-column pages interleave),
and sorting it is exactly the kind of reconstruction this milestone
deliberately leaves to later stages. Every retained item therefore carries
its source extraction index explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import IntFlag

import pymupdf

from .analyzer import EmptyPDFError, PDFReadError

PathLike = str | os.PathLike[str]


class FontFlags(IntFlag):
    """Decoded PyMuPDF font flag bits (``span["flags"]`` from ``"dict"``).

    The flags come straight from the PDF font descriptor and record the
    typeface's *styling capability*, not how it happens to be used in a
    span: for example a bold font always reports ``BOLD`` even for ordinary
    lowercase text, so a "bold" look must be qualified by the font name
    (e.g. containing ``Bold`` or ``Black``). See
    :func:`decode_font_flags`. Names follow PyMuPDF's documentation.
    """

    SUPERSCRIPT_OR_SERIF = 1
    ITALIC = 2
    SERIFED = 4
    MONOSPACED = 8
    BOLD = 16
    REVERSED = 32

    @classmethod
    def can_decode(cls, raw: int) -> bool:
        """Return whether ``raw`` is a known/usable raw flags integer."""
        return isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0


def decode_font_flags(raw: int | None) -> FontFlags:
    """Decode a raw PyMuPDF span ``"flags"`` int into a :class:`FontFlags`.

    The reserved/monospace legacy bit (``1 << 3``) is folded into
    ``MONOSPACED`` so the decoded flags match PyMuPDF's documented bit
    table. An unknown or invalid ``raw`` value decodes to the empty flag
    set (``FontFlags(0)``) rather than failing or inventing styles.
    """
    if raw is None or not FontFlags.can_decode(raw):
        return FontFlags(0)
    if raw & (1 << 3):
        raw |= int(FontFlags.MONOSPACED)
    return FontFlags(raw)


@dataclass(frozen=True, slots=True)
class TextSpan:
    """One run of text sharing the same font and style.

    Spans are the finest text granularity PyMuPDF exposes: consecutive
    characters drawn with the same font/size/flags at the same baseline.

    Geometry follows the module's documented convention (top-left origin,
    y grows down, points, inclusive bbox).

    ``font_size`` is a real number whenever the PDF exposes it, and is
    never fabricated. ``font_flags`` is the decoded :class:`FontFlags` of
    the raw flags integer (all-False when unknown). ``raw_flags`` keeps
    the exact integer for callers that prefer the raw bitmask, ``None``
    when the PDF did not provide it.
    """

    text: str
    bbox: tuple[float, float, float, float]
    font_name: str | None = None
    font_size: float | None = None
    font_flags: FontFlags = FontFlags(0)
    raw_flags: int | None = None
    color: int | None = None

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True, slots=True)
class TextLine:
    """A physical text line: one row of spans sharing a baseline.

    ``text`` is the concatenation of the line's span texts (in span order),
    which is the raw text PyMuPDF associates with the line. A line that
    carries no visible text may have ``len(text) == 0``.

    ``bbox`` is the line's bounding box as reported by PyMuPDF. ``direction``
    is ``(cos, sin)``: ``(1, 0)`` is normal writing, ``(-1, 0)`` is
    right-to-left. ``writing_mode`` is the PDF writing mode (``0`` =
    horizontal, ``1`` = vertical).
    """

    text: str
    bbox: tuple[float, float, float, float]
    order: int
    spans: tuple[TextSpan, ...] = ()
    direction: tuple[float, float] = (1.0, 0.0)
    writing_mode: int = 0

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True, slots=True)
class LayoutBlock:
    """A text block in PyMuPDF's ``"dict"`` layout.

    A "block" is a run of consecutive lines that are visually close together;
    it is PyMuPDF's coarsest text grouping and carries no semantic meaning.

    ``order`` is a dense, gapless index over the *text* blocks of the page
    in ``"dict"`` extraction order (0-based, text blocks only). ``number``
    is the numbering key PyMuPDF assigns to every block -- text and image --
    of the *page* in the very same stream order (each page numbers from 0);
    later stages can use it together with a page's ``image_block_count`` to
    reconstruct the full per-page stream ordering. It is ``None`` when the
    layout library omitted it.
    """

    text: str
    bbox: tuple[float, float, float, float]
    order: int
    number: int | None = None
    lines: tuple[TextLine, ...] = ()

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def span_count(self) -> int:
        """Total number of font spans across the block's lines."""
        return sum(len(line.spans) for line in self.lines)


@dataclass(frozen=True, slots=True)
class LayoutPage:
    """The layout-aware text representation of one PDF page.

    ``page_number`` is the 1-based physical PDF page index. ``page_width``
    and ``page_height`` are the page dimensions in points (top-left origin;
    the page rectangle spans ``(0, 0, page_width, page_height)``).

    ``blocks`` holds the page's *text* blocks in ``"dict"`` extraction
    order. ``image_block_count`` counts the image blocks (``"dict"``
    ``type != 0``) on the page -- the page's full content stream is
    ``image_block_count + len(blocks)`` blocks in PyMuPDF's numbering.
    """

    page_number: int
    page_width: float
    page_height: float
    blocks: tuple[LayoutBlock, ...] = ()
    image_block_count: int = 0

    @property
    def text(self) -> str:
        """All block text on the page, in extraction order, blank-joined."""
        return "\n".join(block.text for block in self.blocks)


@dataclass(frozen=True, slots=True)
class PageLayout:
    """Result of :func:`extract_page_layout`.

    ``pages`` holds one :class:`LayoutPage` per PDF page, in page order;
    ``block_count`` and ``image_block_count`` are document-wide totals (or
    ``None`` when the document has no content blocks). ``block_count ==
    sum(len(page.blocks) for page in pages)``.
    """

    pages: tuple[LayoutPage, ...] = ()
    block_count: int | None = None
    image_block_count: int | None = None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def valid_bbox(bbox: tuple[float, float, float, float]) -> bool:
    """Return whether ``bbox`` satisfies the documented geometry convention.

    ``x1 >= x0`` and ``y1 >= y0`` must both hold so that ``width``/``height``
    (``x1 - x0`` and ``y1 - y0``) are non-negative, matching the top-left
    origin, y-grown-downwards convention.
    """
    if len(bbox) != 4:
        return False
    x0, y0, x1, y1 = bbox
    return x1 >= x0 and y1 >= y0


def extract_page_layout(source: PathLike | pymupdf.Document) -> PageLayout:
    """Extract the layout-aware text representation of every PDF page.

    Parameters
    ----------
    source:
        Either a path to a PDF or an already-open PyMuPDF ``Document``. With
        a path the file is opened and closed within this call; with a
        ``Document`` the caller keeps ownership. The document is never
        mutated.

    Returns
    -------
    PageLayout
        One :class:`LayoutPage` per PDF page, in page order. Pages with no
        extracted text carry an empty ``blocks`` tuple (they are still
        present so page association survives).

    Raises
    ------
    PDFReadError
        If ``source`` is not a readable PDF or a page cannot be decoded.
    EmptyPDFError
        If the PDF opens but has zero pages.
    """
    doc = _open_document(source)
    try:
        if doc.page_count == 0:
            raise EmptyPDFError("PDF has no pages")
        pages = tuple(_extract_pages(doc))
        total_blocks = sum(len(page.blocks) for page in pages)
        total_images = sum(page.image_block_count for page in pages)
        has_blocks = total_blocks + total_images > 0
        return PageLayout(
            pages=pages,
            block_count=total_blocks if has_blocks else None,
            image_block_count=total_images if has_blocks else None,
        )
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
    """Close ``doc`` when ``extract_page_layout`` opened it itself."""
    if not isinstance(source, pymupdf.Document):
        try:
            doc.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


# --------------------------------------------------------------------------- #
# Per-page extraction
# --------------------------------------------------------------------------- #


def _extract_pages(
    doc: pymupdf.Document,
) -> list[LayoutPage]:
    """Build a :class:`LayoutPage` for every page of ``doc``."""
    pages: list[LayoutPage] = []
    for index in range(doc.page_count):
        page = doc.load_page(index)
        try:
            pages.append(_extract_page(page, index=index))
        except Exception as exc:
            raise PDFReadError(
                f"Failed to extract layout from page {index + 1} of the PDF"
            ) from exc
    return pages


def _extract_page(page: pymupdf.Page, *, index: int) -> LayoutPage:
    """Build the :class:`LayoutPage` for a single page.

    Blocks come straight from PyMuPDF's ``"dict"`` layout, which also
    drives the existing extractor: the same grouping, bboxes, and stream
    ordering as the rest of the package.
    """
    try:
        raw_blocks = page.get_text("dict")["blocks"]
    except Exception as exc:
        raise PDFReadError(
            f"Failed to build text layout for page {index + 1} of the PDF"
        ) from exc

    blocks: list[LayoutBlock] = []
    image_block_count = 0
    text_order = 0
    for raw_block in raw_blocks:
        if raw_block.get("type") != 0:
            image_block_count += 1
            continue
        blocks.append(_build_block(raw_block, text_order))
        text_order += 1

    return LayoutPage(
        page_number=index + 1,
        page_width=float(page.rect.width),
        page_height=float(page.rect.height),
        blocks=tuple(blocks),
        image_block_count=image_block_count,
    )


# --------------------------------------------------------------------------- #
# Building the typed structures from a raw dict block
# --------------------------------------------------------------------------- #


def _build_block(raw_block: dict, order: int) -> LayoutBlock:
    """Build a :class:`LayoutBlock` from one PyMuPDF text block."""
    lines = tuple(
        _build_line(raw_line, order=line_index)
        for line_index, raw_line in enumerate(raw_block.get("lines", []))
    )
    return LayoutBlock(
        text="\n".join(line.text for line in lines),
        bbox=tuple(raw_block.get("bbox", (0.0, 0.0, 0.0, 0.0))),
        order=order,
        number=raw_block.get("number"),
        lines=lines,
    )


def _build_line(raw_line: dict, *, order: int) -> TextLine:
    """Build a :class:`TextLine` from one PyMuPDF line dict."""
    raw_spans = raw_line.get("spans", [])
    spans = tuple(_build_span(raw_span) for raw_span in raw_spans)
    text = "".join(span.text for span in spans)
    dir_vec = raw_line.get("dir", (1.0, 0.0))
    return TextLine(
        text=text,
        bbox=tuple(raw_line.get("bbox", (0.0, 0.0, 0.0, 0.0))),
        order=order,
        spans=spans,
        direction=(float(dir_vec[0]), float(dir_vec[1])),
        writing_mode=int(raw_line.get("wmode", 0)),
    )


def _build_span(raw_span: dict) -> TextSpan:
    """Build a :class:`TextSpan` from one PyMuPDF span dict.

    Missing or malformed metadata fields degrade to their "unknown" value
    (``None``) instead of failing; text geometry is taken from the span's
    own bbox.
    """
    raw_flags = raw_span.get("flags", None)
    return TextSpan(
        text=raw_span.get("text", ""),
        bbox=tuple(raw_span.get("bbox", (0.0, 0.0, 0.0, 0.0))),
        font_name=_safe_font_name(raw_span),
        font_size=_safe_font_size(raw_span),
        font_flags=decode_font_flags(raw_flags),
        raw_flags=_safe_raw_flags(raw_flags),
        color=_safe_color(raw_span.get("color", None)),
    )


def _safe_font_name(raw_span: dict) -> str | None:
    """The span's font name when it is a non-empty string, else ``None``."""
    font = raw_span.get("font", None)
    if isinstance(font, str) and font.strip():
        return font
    return None


def _safe_font_size(raw_span: dict) -> float | None:
    """The span's font size when it is a finite number, else ``None``."""
    size = raw_span.get("size", None)
    if isinstance(size, (int, float)) and not isinstance(size, bool):
        size_f = float(size)
        if size_f == size_f and size_f not in (float("inf"), float("-inf")):
            return size_f
    return None


def _safe_raw_flags(raw: object) -> int | None:
    """The raw flags integer when it is a known/usable one, else ``None``."""
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    return None


def _safe_color(color: object) -> int | None:
    """The span color integer when it is a known/usable one, else ``None``."""
    if isinstance(color, int) and not isinstance(color, bool):
        return color
    return None