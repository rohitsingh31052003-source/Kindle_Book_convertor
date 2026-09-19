"""Automatic cover selection for PDF -> EPUB conversion (Milestone 7.2).

M4.4 made the cover **explicit and optional**: a caller either supplied a
validated cover image or the book had none. M7.2 adds one conservative,
deterministic step between those two states: when no explicit cover was
supplied, the converter looks at the first few pages of the PDF and selects a
page to use as the cover *only* when the evidence is clear. The precedence is
therefore::

    explicit user cover  >  automatically detected cover  >  no cover

Where this lives (architecture)
-------------------------------
Cover selection is a small, dedicated service inside the **PDF layer**, for
the same reason the M3.1 analysis and the M3.5 routing live there: every
signal it needs is a property of a physical PDF page (page position, page
size, image coverage, page classification) or of the text the existing
pipeline already extracted for that page (native text for TEXT/MIXED pages,
cleaned OCR text for SCANNED/MIXED pages). It reuses the existing milestones
instead of duplicating anything:

* M3.1 :class:`~kindle_converter.pdf.models.PDFAnalysis` -- per-page
  classification, image count, image-area ratio, and native character counts;
* M3.5 :class:`~kindle_converter.pdf.processing.PageProcessingResult` --
  per-page native/OCR text exactly as the routing layer produced it (so a
  SCANNED page's native text is never resurrected: the M7.1 first-page fix is
  preserved by construction);
* M2.1 :class:`~kindle_converter.pdf.layout.PageLayout` -- page geometry;
* M3.2 :func:`~kindle_converter.pdf.renderer.render_page` -- the only
  rendering primitive used, to materialize the selected page as a cover image.

The **pipeline** (``kindle_converter.pipeline``) applies the precedence rule
by resolving the explicit cover first and only running detection when there is
no explicit cover; the **EPUB builder** keeps consuming ``Book.cover`` exactly
as M4.4 defined and never inspects a PDF. A detected cover is carried as an
ordinary :class:`~kindle_converter.document.models.Image` on ``Book.cover``,
so it takes the existing ``cover.xhtml`` / cover-image manifest path and
changes nothing about the EPUB cover contract.

Deterministic, conservative scoring
-----------------------------------
No OCR engine, image classifier, machine-learning model, network service, or
file-name heuristic is involved. Detection is a pure function of the document:

1. :func:`measure_cover_signals` measures a bounded window of early pages
   (:data:`COVER_CANDIDATE_WINDOW`) and never scans the whole document.
2. :func:`score_cover_page` applies hard rejection gates and adds documented
   weighted evidence for the pages that survive them.
3. :func:`decide_cover_page` selects the highest-scoring page only when it
   reaches :data:`COVER_CONFIDENCE_THRESHOLD` **and** leads the runner-up by
   :data:`COVER_AMBIGUITY_MARGIN`; otherwise the document gets no cover.

The first page is *not* assumed to be the cover: page position is one
weighted signal among several, and a first page that is blank, body text, a
table of contents, copyright front matter, or not image-dominated is rejected
outright. When the evidence is insufficient or ambiguous the converter falls
back to the existing M4.4 "no cover" behavior, and the EPUB is generated
exactly as it was before M7.2.
"""

from __future__ import annotations

import os
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import pymupdf

from ..document import Image
from .analyzer import PDFReadError
from .layout import LayoutPage, PageLayout
from .models import PDFAnalysis, PDFType, PageAnalysis
from .paragraphs import TextSource
from .processing import PageProcessingResult
from .renderer import (
    MAX_RENDER_DPI,
    MIN_RENDER_DPI,
    PDFRenderingError,
    render_page,
)

__all__ = [
    "COVER_AMBIGUITY_MARGIN",
    "COVER_CANDIDATE_WINDOW",
    "COVER_CONFIDENCE_THRESHOLD",
    "COVER_RENDER_DPI",
    "CoverCandidate",
    "CoverDecision",
    "CoverPageSignals",
    "CoverSelection",
    "CoverSelectionSource",
    "decide_cover_page",
    "detect_cover",
    "document_median_text_chars",
    "materialize_cover_page",
    "measure_cover_signals",
    "score_cover_page",
    "select_cover",
]

PathLike = str | os.PathLike[str]
Source = PathLike | pymupdf.Document


# --------------------------------------------------------------------------- #
# Detection constants (explicit, documented)
# --------------------------------------------------------------------------- #

#: How many pages from the front of the document are evaluated. A cover sits
#: before the body text, so only these pages are plausible candidates, and a
#: bounded window keeps detection cost independent of document length (a
#: 468-page book is inspected exactly like a 4-page one).
COVER_CANDIDATE_WINDOW = 5

#: A candidate must be image-dominated: at least this fraction of the page
#: rectangle covered by image placements. Deliberately the same threshold the
#: M3.1 analyzer uses to classify an image-dominated page, so an ordinary text
#: page with an illustration can never become a candidate.
COVER_MIN_IMAGE_AREA_RATIO = 0.5

#: Image coverage that earns the full image-dominance score. Above this the
#: page is effectively a single full-page image.
COVER_FULL_IMAGE_AREA_RATIO = 0.9

#: A candidate with *no* text at all must be (almost) entirely an image, and
#: can only be a cover when the book does carry text elsewhere. An untexted
#: page that is only half covered is far more likely to be a blank page with
#: a stray graphic than a cover, and a book with no text anywhere offers no
#: evidence that any page is a cover at all, so none is selected. (A candidate
#: that carries title-like text gets the normal
#: :data:`COVER_MIN_IMAGE_AREA_RATIO` gate.)
COVER_UNTEXTED_IMAGE_AREA_RATIO = 0.75

#: Weight of the image-dominance signal, the strongest single piece of
#: evidence. Scales linearly from the gate to a full-page image.
COVER_IMAGE_AREA_SCORE = 40.0

#: Early-document preference per window position: a cover precedes the body
#: text and is nearly always page 1, but a later page can still win (for
#: example a text title page followed by a cover plate). Position alone never
#: selects anything -- every candidate must also pass the gates below.
COVER_POSITION_SCORES: dict[int, float] = {
    1: 30.0,
    2: 12.0,
    3: 8.0,
    4: 6.0,
    5: 4.0,
}

#: Title/author-like text: short in absolute terms (a title, subtitle, author,
#: series -- not a page of prose) ...
COVER_MAX_TITLE_LIKE_CHARS = 120

#: ... and short in lines, which distinguishes a cover's few display lines
#: from a paragraph-shaped page.
COVER_MAX_TITLE_LIKE_LINES = 6

#: Score awarded for title-like text.
COVER_TITLE_LIKE_SCORE = 15.0

#: A candidate must also be short *relative to the book*: text longer than
#: this fraction of the document's median page-text length is ordinary
#: document text, not a cover. This is what stops the first page of a scanned
#: book (a page of prose) from being mistaken for a cover.
COVER_TEXT_STANDOUT_RATIO = 0.5

#: Score awarded for a portrait page. Covers are portrait; a landscape early
#: page is more likely a spread, a plate, or a table.
COVER_PORTRAIT_SCORE = 5.0

#: Body-text rejection: a candidate carrying at least this many non-whitespace
#: characters, or this many lines, is a page of the book rather than a cover.
COVER_BODY_TEXT_CHAR_THRESHOLD = 400
COVER_BODY_TEXT_LINE_THRESHOLD = 12

#: A table-of-contents page is detected from its heading ("Contents") or from
#: a majority of short entries that end in a page number.
COVER_TOC_MIN_LINES = 5
COVER_TOC_MIN_ENTRIES = 4

#: Minimum score a candidate must reach to be selected as the cover.
COVER_CONFIDENCE_THRESHOLD = 55.0

#: Minimum lead the best candidate must have over the runner-up. Two similarly
#: plausible pages mean the evidence is ambiguous, and an ambiguous document
#: receives no automatic cover rather than a coin-flip.
COVER_AMBIGUITY_MARGIN = 12.0

#: Rendering resolution used to materialize the selected page as a cover
#: image. Lower than the M3.2 OCR default (300 DPI) because the cover is a
#: single display image, not a recognition input; 200 DPI keeps a book-sized
#: page at a crisp cover resolution (about 1300 x 1700 px for A5/A4 pages).
COVER_RENDER_DPI = 200


# --------------------------------------------------------------------------- #
# Text-shape helpers (deterministic, no natural-language processing)
# --------------------------------------------------------------------------- #

#: A table-of-contents heading: "Contents", "Table of Contents", "CONTENTS.".
_TOC_HEADING = re.compile(r"^(?:table\s+of\s+)?contents\b[\s.:]*$", re.IGNORECASE)

#: A table-of-contents entry: a short label separated from a trailing page
#: number by spaces, dots, or leader characters.
_TOC_ENTRY = re.compile(
    r"^(?P<label>\S.*?)[\s.\u00b7\u2024\u2026]+(?P<page>\d{1,4}|[ivxlcdm]{1,7})$",
    re.IGNORECASE,
)

#: Phrases that mark a copyright/title page (front matter) rather than a
#: cover. Deliberately restricted to unambiguous legal/publishing markers so a
#: genuine cover (which may name a publisher) is never rejected by accident.
_FRONT_MATTER_MARKERS = (
    "copyright",
    "all rights reserved",
    "isbn",
    "library of congress",
    "first published",
    "printed in",
    "no part of this publication",
)


def _text_lines(text: str) -> tuple[str, ...]:
    """The non-empty, stripped lines of ``text``."""
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _non_ws_count(text: str) -> int:
    """The number of non-whitespace characters in ``text``."""
    return sum(1 for char in text if not char.isspace())


def _is_toc_like(text: str, *, lines: tuple[str, ...] | None = None) -> bool:
    """Whether ``text`` looks like a table-of-contents page.

    True when the first line is a contents heading, or when the page carries
    enough short entries that end in a page number (with spaces, dots, or
    leader characters before it). Ordinary prose is unaffected: its lines do
    not end in a page number.
    """
    page_lines = _text_lines(text) if lines is None else lines
    if not page_lines:
        return False
    if _TOC_HEADING.match(page_lines[0]):
        return True
    if len(page_lines) < COVER_TOC_MIN_LINES:
        return False
    entries = sum(1 for line in page_lines if _TOC_ENTRY.match(line))
    return entries >= COVER_TOC_MIN_ENTRIES and entries * 2 >= len(page_lines)


def _is_front_matter_like(text: str) -> bool:
    """Whether ``text`` carries copyright/title-page rather than cover text."""
    lowered = text.lower()
    return any(marker in lowered for marker in _FRONT_MATTER_MARKERS)


# --------------------------------------------------------------------------- #
# Candidate signals
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CoverPageSignals:
    """One page's cover-relevant, deterministic signals (immutable).

    Every field is either a property of the physical page (geometry, image
    coverage, classification) or a shape measure of the text the existing
    pipeline already produced for that page. Nothing here is inferred from a
    file name, a timestamp, an environment property, or a network resource.

    ``page_width``/``page_height`` are PDF points (from the M2.1 layout).
    ``image_count`` and ``image_area_ratio`` come from the M3.1 analysis.
    ``text_source`` records where the measured text came from -- native text,
    cleaned OCR text, or ``None`` when the page carries no text at all --
    mirroring the M3.5 routing contract (a SCANNED page's text is always its
    OCR text). ``text_char_count`` is the number of non-whitespace characters
    and ``text_line_count`` the number of non-empty lines. ``is_blank``,
    ``is_toc_like``, and ``is_front_matter_like`` are the explicit text-shape
    classifications documented on :func:`_is_toc_like` and
    :func:`_is_front_matter_like`.
    """

    page_number: int
    page_width: float
    page_height: float
    classification: PDFType
    image_count: int
    image_area_ratio: float
    text_source: TextSource | None
    text_char_count: int
    text_line_count: int
    is_blank: bool
    is_toc_like: bool
    is_front_matter_like: bool

    @property
    def is_portrait(self) -> bool:
        """Whether the page is portrait (or square)."""
        return self.page_height >= self.page_width

    @property
    def is_image_dominated(self) -> bool:
        """Whether images cover at least the candidate gate fraction."""
        return (
            self.image_count > 0
            and self.image_area_ratio >= COVER_MIN_IMAGE_AREA_RATIO
        )

    @property
    def has_title_like_text(self) -> bool:
        """Whether the page's text is short enough to be cover text."""
        return (
            0 < self.text_char_count <= COVER_MAX_TITLE_LIKE_CHARS
            and self.text_line_count <= COVER_MAX_TITLE_LIKE_LINES
        )


def _page_text(result: PageProcessingResult) -> tuple[str, TextSource | None]:
    """The text of ``result`` for shape analysis, and where it came from.

    The M3.5 routing contract is honoured exactly: a SCANNED page's text is
    its cleaned OCR text (its native text is ``None`` by contract), a
    TEXT/MIXED page's text is its native text when that path produced any, and
    OCR text is only consulted when no native text exists. Nothing is
    concatenated and no native text is ever recovered for a SCANNED page, so
    the M7.1 scanned-first-page fix cannot be undone here.
    """
    if result.classification is PDFType.SCANNED:
        text = result.ocr_text or ""
        return text, (TextSource.OCR if text else None)
    native = result.native_text or ""
    if native.strip():
        return native, TextSource.NATIVE
    ocr = result.ocr_text or ""
    return ocr, (TextSource.OCR if ocr else None)


def _signals_for_page(
    page: PageAnalysis, layout_page: LayoutPage, result: PageProcessingResult
) -> CoverPageSignals:
    """Measure the cover signals of one analyzed/processed page."""
    text, source = _page_text(result)
    lines = _text_lines(text)
    char_count = _non_ws_count(text)
    return CoverPageSignals(
        page_number=page.page_number,
        page_width=float(layout_page.page_width),
        page_height=float(layout_page.page_height),
        classification=page.classification,
        image_count=int(page.image_count),
        image_area_ratio=float(page.image_area_ratio),
        text_source=source,
        text_char_count=char_count,
        text_line_count=len(lines),
        is_blank=char_count == 0 and page.image_count == 0,
        is_toc_like=_is_toc_like(text, lines=lines),
        is_front_matter_like=_is_front_matter_like(text) if text else False,
    )


def measure_cover_signals(
    analysis: PDFAnalysis,
    results: Sequence[PageProcessingResult],
    *,
    layout: PageLayout,
    window: int = COVER_CANDIDATE_WINDOW,
) -> tuple[CoverPageSignals, ...]:
    """Measure cover signals for the bounded early-page window.

    ``analysis`` is the M3.1 analysis, ``results`` the M3.5 routing results,
    and ``layout`` the M2.1 page layout (which carries the page geometry the
    analysis does not). Only the first ``window`` pages are measured, so
    detection never scans a whole book.

    Raises
    ------
    TypeError
        If ``analysis``, ``results``, or ``layout`` are not the expected
        types.
    ValueError
        If ``window`` is not a positive integer, or the inputs do not all
        describe the same number of pages.
    """
    if not isinstance(analysis, PDFAnalysis):
        raise TypeError(
            "analysis must be a PDFAnalysis (from analyze_pdf), got "
            + type(analysis).__name__
        )
    if not isinstance(layout, PageLayout):
        raise TypeError(
            "layout must be a PageLayout (from extract_page_layout), got "
            + type(layout).__name__
        )
    _validate_results(results, len(analysis.pages))
    if len(layout.pages) != len(analysis.pages):
        raise ValueError(
            f"layout has {len(layout.pages)} pages but the analysis describes "
            f"{len(analysis.pages)}"
        )
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError(f"window must be a positive int, got {window!r}")
    pages = list(analysis.pages)[:window]
    layout_pages = list(layout.pages)[:window]
    page_results = list(results)[:window]
    return tuple(
        _signals_for_page(page, layout_page, result)
        for page, layout_page, result in zip(pages, layout_pages, page_results)
    )


def document_median_text_chars(results: Sequence[PageProcessingResult]) -> float:
    """The median non-whitespace character count over the book's text pages.

    Pages that produced no text are excluded (they carry no text evidence),
    and an entirely text-free document returns ``0.0``. The value is the
    reference the "short relative to the book" signal compares against, and it
    is a deterministic function of the document's already-extracted text.
    """
    lengths: list[int] = []
    for result in results:
        text = _page_text(result)[0]
        count = _non_ws_count(text)
        if count > 0:
            lengths.append(count)
    if not lengths:
        return 0.0
    return float(statistics.median(lengths))


def _validate_results(
    results: object, page_count: int
) -> Sequence[PageProcessingResult]:
    """Return ``results`` validated as the contiguous ``1..N`` page sequence."""
    if not isinstance(results, Sequence):
        raise TypeError(
            "results must be a sequence of PageProcessingResult (from "
            "process_pages), got "
            + (type(results).__name__ if results is not None else "None")
        )
    if len(results) != page_count:
        raise ValueError(
            f"results has {len(results)} pages but the analysis describes "
            f"{page_count}"
        )
    for index, result in enumerate(results):
        if not isinstance(result, PageProcessingResult):
            raise TypeError(
                "results must contain only PageProcessingResult values, got "
                f"{type(result).__name__} at index {index}"
            )
        if result.page_number != index + 1:
            raise ValueError(
                "results must be the contiguous 1..N page sequence in order; "
                f"expected page {index + 1} at index {index}, got "
                f"{result.page_number}"
            )
    return results  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Scoring and selection (pure functions of the measured signals)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CoverCandidate:
    """The deterministic scoring outcome for one measured page.

    ``eligible`` is ``True`` when the page passed every rejection gate and its
    score reached :data:`COVER_CONFIDENCE_THRESHOLD`. ``reason`` always
    explains the outcome in one short, stable sentence -- the rejection that
    applied, or the score and the signals that produced it. Rejected pages
    carry a score of ``0.0``: they never compete with eligible candidates.
    """

    page_number: int
    score: float
    eligible: bool
    reason: str


@dataclass(frozen=True, slots=True)
class CoverDecision:
    """The outcome of comparing the scored candidates (immutable).

    ``page_number`` is ``None`` when no cover is selected -- either because no
    page was eligible or because the two best candidates were too close to
    call. ``score`` is the winning (or best) score, ``reason`` the
    deterministic explanation, and ``candidates`` the per-page decisions that
    produced this result.
    """

    page_number: int | None
    score: float
    reason: str
    candidates: tuple[CoverCandidate, ...] = ()

    @property
    def detected(self) -> bool:
        """Whether a cover page was selected."""
        return self.page_number is not None


def _rejected(signals: CoverPageSignals, reason: str) -> CoverCandidate:
    """A rejected candidate for ``signals`` with ``reason``."""
    return CoverCandidate(
        page_number=signals.page_number, score=0.0, eligible=False, reason=reason
    )


def _body_text_like(signals: CoverPageSignals) -> bool:
    """Whether the page carries a page-of-the-book amount of text."""
    return (
        signals.text_char_count >= COVER_BODY_TEXT_CHAR_THRESHOLD
        or signals.text_line_count >= COVER_BODY_TEXT_LINE_THRESHOLD
    )


def _not_sparse_for_the_book(
    signals: CoverPageSignals, document_median_chars: float
) -> bool:
    """Whether the candidate's text is ordinary document text.

    ``document_median_chars`` is the book's median page-text length (0.0 when
    the book has no text to compare against, in which case the comparison is
    skipped: there is no evidence to call the text ordinary).
    """
    if document_median_chars <= 0:
        return False
    return (
        signals.text_char_count > COVER_TEXT_STANDOUT_RATIO * document_median_chars
    )


def score_cover_page(
    signals: CoverPageSignals, *, document_median_chars: float
) -> CoverCandidate:
    """Score one measured page as a cover candidate (pure, deterministic).

    The gates are applied first and in a fixed order, so the rejection reason
    for a page is always the same: blank pages, pages that are not
    image-dominated, an untexted page that is not (almost) the whole page, a
    page in a book that offers no text evidence at all, a
    table-of-contents page, copyright/title-page front matter, a page with a
    page-of-prose amount of text, text longer than
    :data:`COVER_MAX_TITLE_LIKE_CHARS`, and text that is not sparse relative
    to the book are all rejected without a score.

    Surviving pages accumulate documented weighted evidence:

    * image dominance -- up to :data:`COVER_IMAGE_AREA_SCORE` points, scaling
      linearly to a full-page image;
    * early position -- :data:`COVER_POSITION_SCORES` points;
    * title/author-like text -- :data:`COVER_TITLE_LIKE_SCORE` points;
    * portrait orientation -- :data:`COVER_PORTRAIT_SCORE` points.

    The total is rounded to one decimal place and compared against
    :data:`COVER_CONFIDENCE_THRESHOLD`; a page below it is not eligible.
    """
    if not isinstance(signals, CoverPageSignals):
        raise TypeError(
            "signals must be a CoverPageSignals, got " + type(signals).__name__
        )
    if document_median_chars < 0:
        raise ValueError(
            "document_median_chars must be non-negative, got "
            f"{document_median_chars!r}"
        )

    page_number = signals.page_number
    if signals.is_blank:
        return _rejected(signals, f"page {page_number} is blank")
    if not signals.is_image_dominated:
        return _rejected(
            signals,
            f"page {page_number} is not image-dominated (image area ratio "
            f"{signals.image_area_ratio:.2f} < {COVER_MIN_IMAGE_AREA_RATIO:.2f})",
        )
    if signals.text_char_count == 0:
        if document_median_chars <= 0:
            return _rejected(
                signals,
                f"page {page_number} has no text and the book has no text "
                "evidence at all",
            )
        if signals.image_area_ratio < COVER_UNTEXTED_IMAGE_AREA_RATIO:
            return _rejected(
                signals,
                f"page {page_number} has no text and no full-page image (image "
                f"area ratio {signals.image_area_ratio:.2f} < "
                f"{COVER_UNTEXTED_IMAGE_AREA_RATIO:.2f})",
            )
    if signals.is_toc_like:
        return _rejected(
            signals, f"page {page_number} carries table-of-contents-like text"
        )
    if signals.is_front_matter_like:
        return _rejected(
            signals,
            f"page {page_number} carries copyright/title-page front matter",
        )
    if _body_text_like(signals):
        return _rejected(
            signals,
            f"page {page_number} carries body-text-like text "
            f"({signals.text_char_count} characters, "
            f"{signals.text_line_count} lines)",
        )
    if signals.text_char_count > COVER_MAX_TITLE_LIKE_CHARS:
        return _rejected(
            signals,
            f"page {page_number} carries too much text for a cover "
            f"({signals.text_char_count} characters > "
            f"{COVER_MAX_TITLE_LIKE_CHARS})",
        )
    if _not_sparse_for_the_book(signals, document_median_chars):
        return _rejected(
            signals,
            f"page {page_number} text is too long to be cover text "
            f"({signals.text_char_count} characters vs a median page text of "
            f"{document_median_chars:.0f})",
        )

    image_points = COVER_IMAGE_AREA_SCORE * min(
        1.0, signals.image_area_ratio / COVER_FULL_IMAGE_AREA_RATIO
    )
    position_points = COVER_POSITION_SCORES.get(page_number, 0.0)
    title_points = COVER_TITLE_LIKE_SCORE if signals.has_title_like_text else 0.0
    portrait_points = COVER_PORTRAIT_SCORE if signals.is_portrait else 0.0
    score = round(
        image_points + position_points + title_points + portrait_points, 1
    )
    eligible = score >= COVER_CONFIDENCE_THRESHOLD
    if eligible:
        reason = (
            f"page {page_number} is a confident cover candidate "
            f"(score {score:.1f})"
        )
    else:
        reason = (
            f"page {page_number} scored {score:.1f}, below the confidence "
            f"threshold {COVER_CONFIDENCE_THRESHOLD:.1f}"
        )
    return CoverCandidate(
        page_number=page_number, score=score, eligible=eligible, reason=reason
    )


def decide_cover_page(candidates: Sequence[CoverCandidate]) -> CoverDecision:
    """Choose at most one candidate page, else return no cover.

    The highest score wins; ties go to the earlier page. A selection is only
    made when the best candidate reaches
    :data:`COVER_CONFIDENCE_THRESHOLD` *and* leads the runner-up by at least
    :data:`COVER_AMBIGUITY_MARGIN`; otherwise the document gets no automatic
    cover and the M4.4 "no cover" behavior stands. Ambiguity is therefore
    resolved in favour of *not* guessing.
    """
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, CoverCandidate):
            raise TypeError(
                "candidates must contain only CoverCandidate values, got "
                f"{type(candidate).__name__} at index {index}"
            )
    decisions = tuple(candidates)
    if not decisions:
        return CoverDecision(
            None, 0.0, "no cover candidate pages were measured", decisions
        )
    eligible = tuple(
        candidate for candidate in decisions if candidate.eligible
    )
    if not eligible:
        return CoverDecision(
            None,
            0.0,
            f"no page in the first {len(decisions)} pages reached the cover "
            f"confidence threshold {COVER_CONFIDENCE_THRESHOLD:.1f}",
            decisions,
        )
    ranked = sorted(
        eligible, key=lambda candidate: (-candidate.score, candidate.page_number)
    )
    best = ranked[0]
    if len(ranked) > 1 and best.score - ranked[1].score < COVER_AMBIGUITY_MARGIN:
        runner_up = ranked[1]
        return CoverDecision(
            None,
            best.score,
            f"ambiguous cover evidence: page {best.page_number} (score "
            f"{best.score:.1f}) and page {runner_up.page_number} (score "
            f"{runner_up.score:.1f}) are too close to choose",
            decisions,
        )
    return CoverDecision(
        best.page_number,
        best.score,
        f"page {best.page_number} selected as the cover (score {best.score:.1f})",
        decisions,
    )


# --------------------------------------------------------------------------- #
# Cover resolution (precedence, materialization, public entry points)
# --------------------------------------------------------------------------- #


class CoverSelectionSource(StrEnum):
    """Where a resolved cover came from (Milestone 7.2)."""

    NONE = "none"
    EXPLICIT = "explicit"
    AUTOMATIC = "automatic"


@dataclass(frozen=True, slots=True)
class CoverSelection:
    """The resolved cover decision for one conversion (immutable).

    ``source`` records which branch of the M7.2 precedence produced the
    result: ``EXPLICIT`` (the caller supplied a cover, which always wins),
    ``AUTOMATIC`` (a PDF page was confidently detected as a cover and
    materialized as ``image``), or ``NONE`` (no cover; ``reason`` explains
    why, and the EPUB is produced exactly as a coverless M4.4 book was).
    ``page_number`` and ``score`` describe the detected page when there is
    one. Nothing here is exposed through the GUI: the public conversion
    behavior is simply explicit cover, automatic cover, or no cover.
    """

    source: CoverSelectionSource
    image: Image | None = None
    page_number: int | None = None
    score: float = 0.0
    reason: str = ""

    @property
    def has_cover(self) -> bool:
        """Whether a cover image will reach the EPUB."""
        return self.image is not None

    @classmethod
    def explicit(cls, image: Image) -> "CoverSelection":
        """The caller-supplied cover, which always takes precedence."""
        if not isinstance(image, Image):
            raise TypeError(
                "an explicit cover must be a kindle_converter.document.Image, "
                "got " + type(image).__name__
            )
        return cls(
            CoverSelectionSource.EXPLICIT,
            image=image,
            reason="explicit cover supplied by the caller",
        )

    @classmethod
    def automatic(
        cls, *, image: Image, page_number: int, score: float, reason: str
    ) -> "CoverSelection":
        """A cover materialized from a confidently detected PDF page."""
        if not isinstance(image, Image):
            raise TypeError(
                "an automatic cover must be a kindle_converter.document.Image, "
                "got " + type(image).__name__
            )
        return cls(
            CoverSelectionSource.AUTOMATIC,
            image=image,
            page_number=page_number,
            score=score,
            reason=reason,
        )

    @classmethod
    def none(cls, reason: str) -> "CoverSelection":
        """No cover: the pre-M7.2 coverless EPUB output."""
        return cls(CoverSelectionSource.NONE, reason=reason)


def _validate_render_dpi(render_dpi: object) -> int | float:
    """Validate ``render_dpi`` against the M3.2 renderer's accepted range.

    Mirrors the routing layer's DPI validation so an invalid value fails
    eagerly and identically here, instead of being swallowed by the
    optional-cover fallback in :func:`materialize_cover_page`.
    """
    if isinstance(render_dpi, bool) or not isinstance(render_dpi, (int, float)):
        raise ValueError(
            f"render_dpi must be a number, got {type(render_dpi).__name__}"
        )
    value = float(render_dpi)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"render_dpi must be a finite number, got {render_dpi!r}")
    if not MIN_RENDER_DPI <= value <= MAX_RENDER_DPI:
        raise ValueError(
            f"render_dpi must be between {MIN_RENDER_DPI} and "
            f"{MAX_RENDER_DPI}, got {render_dpi}"
        )
    return render_dpi


def materialize_cover_page(
    source: Source,
    page_number: int,
    *,
    render_dpi: int | float = COVER_RENDER_DPI,
) -> Image | None:
    """Render ``page_number`` of ``source`` into a cover image, or ``None``.

    The selected page is rasterized with the existing M3.2
    :func:`~kindle_converter.pdf.renderer.render_page` at
    :data:`COVER_RENDER_DPI` and encoded as PNG, so the cover shows the page
    exactly as it is displayed (image, vector art, and any text drawn over it)
    and needs no second encoder or quality parameter. The packaged bytes are
    therefore a deterministic function of the page.

    A cover is optional enrichment (M4.4), so a page that cannot be rasterized
    or encoded yields ``None`` -- and hence no cover -- instead of failing the
    conversion. ``page_number`` and ``render_dpi`` are validated eagerly, so
    caller mistakes are not silently converted into "no cover".
    """
    if isinstance(page_number, bool) or not isinstance(page_number, int):
        raise ValueError(
            f"page_number must be an int, got {type(page_number).__name__}"
        )
    if page_number < 1:
        raise ValueError(f"page_number must be >= 1, got {page_number}")
    _validate_render_dpi(render_dpi)
    try:
        rendered = render_page(source, page_number, dpi=render_dpi)
        data = rendered.image.tobytes("png")
    except (PDFReadError, PDFRenderingError, ValueError, RuntimeError):
        return None
    return Image(data=data, content_type="image/png")


def detect_cover(
    source: Source,
    analysis: PDFAnalysis,
    results: Sequence[PageProcessingResult],
    *,
    layout: PageLayout,
    window: int = COVER_CANDIDATE_WINDOW,
    render_dpi: int | float = COVER_RENDER_DPI,
) -> CoverSelection:
    """Detect and materialize a cover page, or return a no-cover selection.

    ``source`` is the PDF (a path or an open PyMuPDF document) that the
    already-computed ``analysis`` (M3.1), ``results`` (M3.5), and ``layout``
    (M2.1) describe. Detection owns no OCR engine and never re-reads page
    text: it consumes exactly what those milestones produced, so it cannot
    change routing, reconstruction, or reading order.

    The result is an :class:`CoverSelection`: an ``AUTOMATIC`` selection with
    a materialized PNG cover, or a ``NONE`` selection whose ``reason`` records
    why no page was confidently selected. Detection is deterministic -- the
    same document always produces the same decision, independent of machine
    state and network availability.
    """
    signals = measure_cover_signals(
        analysis, results, layout=layout, window=window
    )
    median_chars = document_median_text_chars(results)
    candidates = tuple(
        score_cover_page(signal, document_median_chars=median_chars)
        for signal in signals
    )
    decision = decide_cover_page(candidates)
    if decision.page_number is None:
        return CoverSelection.none(decision.reason)
    image = materialize_cover_page(
        source, decision.page_number, render_dpi=render_dpi
    )
    if image is None:
        return CoverSelection.none(
            f"the selected cover page {decision.page_number} could not be "
            "rendered"
        )
    return CoverSelection.automatic(
        image=image,
        page_number=decision.page_number,
        score=decision.score,
        reason=decision.reason,
    )


def select_cover(
    source: Source,
    analysis: PDFAnalysis,
    results: Sequence[PageProcessingResult],
    *,
    layout: PageLayout,
    explicit: Image | None = None,
    window: int = COVER_CANDIDATE_WINDOW,
    render_dpi: int | float = COVER_RENDER_DPI,
) -> CoverSelection:
    """Resolve the cover following the M7.2 precedence rule.

    ``explicit`` (an already-validated M4.4 cover ``Image``) always wins: when
    it is present no detection runs at all, so an explicit cover can never be
    overridden and M4.4 behavior is preserved exactly. Without an explicit
    cover, :func:`detect_cover` decides between an automatically detected
    cover and no cover.
    """
    if explicit is not None:
        return CoverSelection.explicit(explicit)
    return detect_cover(
        source,
        analysis,
        results,
        layout=layout,
        window=window,
        render_dpi=render_dpi,
    )