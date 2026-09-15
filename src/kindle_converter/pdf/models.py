"""Data model for the PDF analysis result.

These types describe what a PDF contains *as a physical document*, before any
extraction or OCR work happens. They deliberately do not reference the
format-independent ``kindle_converter.document`` book model:

* analysis happens before any book structure is known, and
* PDF analysis is about physical page content, while the book model is about
  logical structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PDFType(Enum):
    """High-level, first-pass classification of a PDF.

    Values
    ------
    TEXT
        Meaningful text on essentially all (or a large majority of) pages.
    SCANNED
        Little or no meaningful text; pages are primarily image-based.
    MIXED
        A substantial mixture of text pages and image-based pages.

    The classification is deliberately a deterministic heuristic used to
    choose an extraction strategy, not a perfect OCR-vs-text detector.
    """

    TEXT = "text"
    SCANNED = "scanned"
    MIXED = "mixed"


@dataclass(slots=True)
class PageAnalysis:
    """Per-page content summary produced by the analyzer.

    ``char_count`` is the number of non-whitespace characters in the page's
    longest text line -- the feature used for the ``has_meaningful_text``
    decision (0 when the page has no extractable text).

    ``text`` holds the raw extracted text for the *first* page of the
    document only; on every other page it is the empty string (see
    :class:`PDFAnalysis`). Consumers must not assume raw text is present
    on every page.

    ``text_block_count`` is the number of distinct text blocks on the
    page (blocks of type 0 from PyMuPDF ``"dict"`` layout). It provides
    evidence for why a page was classified a certain way without relying
    on OCR.

    ``image_count`` is the number of embedded image placements on the
    page, obtained from ``page.get_image_info()``.

    ``image_area_ratio`` is the fraction of the page rectangle covered
    by image placements (total image bbox area divided by page area).
    It is a deterministic geometric measure used to decide whether a page
    is image-dominated. 0.0 means no images; 1.0 means images cover the
    entire page.

    ``classification`` is the page-level content classification
    (``PDFType.TEXT``, ``PDFType.SCANNED``, or ``PDFType.MIXED``) derived
    from meaningful-text presence and image coverage. It is independent
    of OCR and depends only on PDF-native information.
    """

    page_number: int
    has_image: bool
    has_meaningful_text: bool
    char_count: int
    text: str = ""
    text_block_count: int = 0
    image_count: int = 0
    image_area_ratio: float = 0.0
    classification: PDFType = PDFType.TEXT


@dataclass(slots=True)
class PDFAnalysis:
    """Structured result of analyzing a PDF.

    ``document_type`` and ``pages`` are deterministic functions of the page
    content alone: analyzing the same PDF twice yields identical results.

    ``text_density`` is the average number of non-whitespace characters in
    the longest text line, taken over all pages (the mean of
    ``PageAnalysis.char_count``). Text-based pages typically score well above
    the meaningful-text threshold; scanned pages score near zero. See the
    ``analyzer`` module for the exact definition and thresholds.

    To bound memory use on large books, only the first page's raw text is
    retained (in ``pages[0].text``); all other per-page records carry the
    counts and flags but an empty ``text`` string.
    """

    page_count: int
    text_page_count: int
    image_page_count: int
    text_density: float
    document_type: PDFType
    pages: list[PageAnalysis] = field(default_factory=list)