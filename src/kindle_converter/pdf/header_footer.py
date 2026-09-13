"""Conservative layout-based header/footer detection (Milestone 2.5).

This module consumes the M2.3 paragraph reconstruction output
(:class:`~kindle_converter.pdf.paragraphs.ParagraphLayout` /
:class:`~kindle_converter.pdf.paragraphs.ParagraphPage`) and *classifies*
repeated page-level elements as running headers or footers. It is a
**detection layer only**:

* nothing is removed or rewritten in the PDF representation
* M2.2 reading order and M2.3 paragraph order remain authoritative and are
  never modified (paragraphs are only *read*)
* EPUB integration is out of scope for this milestone

Detection philosophy
--------------------
The detector is a **conservative, deterministic, multi-signal heuristic**.
No single signal is decisive:

* proximity to the top or bottom edge alone is insufficient
* short text alone is insufficient
* repeated text alone is insufficient (body text can legitimately repeat)

A paragraph is only classified as page furniture when a combination of
independent evidence supports the classification:

1. **Repetition across distinct pages** - the same normalized text occurs on
   ``MIN_REPEATED_PAGES`` or more different pages.
2. **Page-edge region** - every occurrence sits in the header region (top
   ``HEADER_REGION_FRACTION`` of the page) or the footer region (bottom
   ``FOOTER_REGION_FRACTION`` of the page), using page-height-normalized
   geometry so mixed page sizes compare fairly.
3. **Positional consistency** - the normalized vertical positions do not
   deviate more than ``HEADER_POSITION_TOLERANCE_FRACTION`` /
   ``FOOTER_POSITION_TOLERANCE_FRACTION`` from each other. Wide variation is
   treated as ambiguous and is never confidently classified.
4. **Short text** - a candidate whose median length exceeds
   ``MAX_HEADER_FOOTER_CHARS`` is rejected as body content.
5. **Heuristic confidence** - the combined evidence must reach
   ``HEADER_FOOTER_CONFIDENCE_THRESHOLD``.

Text normalization
------------------
Text is compared through a normalized key: Unicode NFKC normalization,
whitespace collapsing, and case folding. The *original* text is preserved in
the output for provenance. ``"THE GREAT GATSBY"``, ``"The Great Gatsby"`` and
``"  the   great gatsby "`` therefore compare equal, while structurally
similar but different text (``"Chapter 1"`` vs ``"Chapter 2"``) does not.
No fuzzy or semantic matching is performed.

Page numbers
------------
Page numbers are an important footer case and are also detected as headers
when they reliably repeat near the top. Two deterministic mechanisms exist:

* **Exact repetition** - an identical numeric paragraph (e.g. ``"7"``)
  repeated at a consistent edge position is a candidate.
* **Page-number sequences** - a *varied* sequence such as ``1, 2, 3``,
  ``Page 1, Page 2, Page 3``, ``- 1 -, - 2 -, - 3 -`` or
  ``3 / 250, 4 / 250, 5 / 250`` is recognized as one page-number pattern
  when the values form a monotonic sequence of at least two distinct values
  at a consistent edge position. Only narrow, deterministic regular
  expressions are used; arbitrary changing text is never generalized into a
  repeated pattern.

Positional independence
-----------------------
Header and footer regions are bucketed independently. The same text may
therefore legitimately produce *two* independent candidates (a top
occurrence and a bottom occurrence); they are never collapsed, and each
occurrence belongs to exactly one candidate.

Determinism
-----------
All arithmetic and ordering are deterministic. Detected candidates are
ordered by first page number, then normalized vertical position, then
normalized horizontal position, then original source order. Given the same
input, the same output is always produced. No randomness, timestamps,
external services, or machine learning is used.

Provenance
----------
Every detected item retains its source :class:`ReconstructedParagraph`
objects, their page numbers, and (through M2.3) their source lines and
blocks. Source objects are never mutated.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from statistics import median
from typing import Iterable

from .layout import LayoutBlock, TextLine
from .paragraphs import ParagraphLayout, ParagraphPage, ReconstructedParagraph
# --------------------------------------------------------------------------- #
# Thresholds, tolerances and evidence weights (centralized, documented)
# --------------------------------------------------------------------------- #

#: Minimum number of *distinct pages* a candidate must occur on before it can
#: be classified as page furniture. Multiple occurrences on one page never
#: count as repeated-page evidence.
MIN_REPEATED_PAGES = 3

#: Top fraction of page height treated as the header region. A paragraph is a
#: header-region candidate when its top edge falls within this band.
HEADER_REGION_FRACTION = 0.15

#: Bottom fraction of page height treated as the footer region. A paragraph is
#: a footer-region candidate when its bottom edge falls within this band.
FOOTER_REGION_FRACTION = 0.15

#: Maximum spread of normalized header positions (fraction of page height)
#: tolerated within one candidate. Wider variation is treated as ambiguous.
HEADER_POSITION_TOLERANCE_FRACTION = 0.03

#: Maximum spread of normalized footer positions (fraction of page height)
#: tolerated within one candidate.
FOOTER_POSITION_TOLERANCE_FRACTION = 0.03

#: Number of distinct pages at which the repetition evidence saturates.
#: Three pages is the minimum; five or more is treated as strongly repeated.
REPETITION_SATURATION_PAGES = 5

#: Median paragraph length (characters) up to which text is considered *short*
#: for the weak isolation evidence.
SHORT_HEADER_FOOTER_CHARS = 60

#: Median paragraph length (characters) beyond which a candidate is rejected
#: as body content rather than page furniture.
MAX_HEADER_FOOTER_CHARS = 160

#: Minimum heuristic confidence required for a candidate to be classified.
HEADER_FOOTER_CONFIDENCE_THRESHOLD = 0.5

#: Weight of the repetition evidence in the confidence score.
REPETITION_SCORE_WEIGHT = 0.45

#: Weight of the positional-consistency evidence in the confidence score.
POSITION_CONSISTENCY_SCORE_WEIGHT = 0.30

#: Weight of the isolation (short, single-line) evidence in the confidence
#: score.
ISOLATION_SCORE_WEIGHT = 0.15

#: Weight of the page-number-pattern evidence in the confidence score.
PAGE_NUMBER_SCORE_WEIGHT = 0.10
# --------------------------------------------------------------------------- #
# Public data structures
# --------------------------------------------------------------------------- #


class HeaderFooterType(StrEnum):
    """The kind of repeated page furniture."""

    HEADER = "header"
    FOOTER = "footer"


@dataclass(frozen=True, slots=True)
class DetectedHeaderFooter:
    """One detected repeated header or footer element.

    ``text`` preserves the *original* text of the earliest source paragraph.
    ``pages`` is the sorted tuple of distinct pages the element occurs on.
    ``confidence`` is a deterministic heuristic score in ``[0.0, 1.0]`` (not
    a statistical probability) combining repetition, positional consistency,
    isolation and page-number evidence. ``reasons`` lists the evidence tokens
    that contributed. ``source_paragraphs`` preserves full M2.3 provenance in
    document order.
    """

    text: str
    kind: HeaderFooterType
    pages: tuple[int, ...]
    confidence: float
    reasons: tuple[str, ...]
    source_paragraphs: tuple[ReconstructedParagraph, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def first_page(self) -> int:
        return self.pages[0]

    @property
    def last_page(self) -> int:
        return self.pages[-1]


@dataclass(frozen=True, slots=True)
class HeaderFooterPage:
    """One input page and the detected furniture on it."""

    page_number: int
    detected: tuple[DetectedHeaderFooter, ...]

    @property
    def detected_count(self) -> int:
        return len(self.detected)


@dataclass(frozen=True, slots=True)
class HeaderFooterLayout:
    """The complete header/footer detection result.

    ``pages`` contains one :class:`HeaderFooterPage` per *input* page, in
    input order, so page association and order survive. ``detected`` is the
    flattened, deterministically ordered tuple of every detected element.
    """

    pages: tuple[HeaderFooterPage, ...]
    detected: tuple[DetectedHeaderFooter, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def detected_count(self) -> int:
        return len(self.detected)


@dataclass(frozen=True, slots=True)
class ClassifiedHeaderFooterParagraph:
    """Paragraph-level classification for downstream reconstruction.

    Every M2.3 paragraph maps to exactly one of these records, in paragraph
    order. ``kind`` is the furniture type when the paragraph is part of a
    detected element, otherwise ``None``; ``score`` mirrors the parent
    candidate's heuristic confidence (``0.0`` when not detected).
    """

    paragraph: ReconstructedParagraph
    kind: HeaderFooterType | None
    score: float
    reasons: tuple[str, ...]
# --------------------------------------------------------------------------- #
# Internal data structures
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Occurrence:
    """One paragraph viewed as a potential furniture occurrence."""

    paragraph: ReconstructedParagraph
    page_number: int
    order_index: int
    top_fraction: float
    bottom_fraction: float
    x_center_fraction: float
    char_count: int
    line_count: int
    norm_text: str
    region: HeaderFooterType | None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """Internal candidate before conversion to a public result."""

    text: str
    kind: HeaderFooterType
    occurrences: tuple[_Occurrence, ...]
    pages: tuple[int, ...]
    position: float
    x_center: float
    min_order_index: int
    confidence: float
    reasons: tuple[str, ...]
    page_number_like: bool


# --------------------------------------------------------------------------- #
# Page-number patterns (narrow, deterministic)
# --------------------------------------------------------------------------- #

_PAGE_NUM_OF_RE = re.compile(r"^page\s+(\d{1,5})\s+of\s+(\d{1,6})$")
_PAGE_NUM_PAGE_RE = re.compile(r"^page\s+(\d{1,5})$")
_PAGE_NUM_SLASH_RE = re.compile(r"^(\d{1,5})\s*/\s*(\d{1,6})$")
_PAGE_NUM_DASHED_RE = re.compile(r"^-+\s*(\d{1,5})\s*-+$")
_PAGE_NUM_BARE_RE = re.compile(r"^(\d{1,3})$")
# --------------------------------------------------------------------------- #
# Geometry and normalization helpers
# --------------------------------------------------------------------------- #


def _finite_number(value: object) -> float | None:
    """Return ``value`` as a finite float, or ``None`` when unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _finite_positive(value: object) -> float | None:
    """Return ``value`` as a finite positive float, or ``None``."""
    result = _finite_number(value)
    if result is None or result <= 0:
        return None
    return result


def _valid_bbox(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    """Return ``bbox`` when it is a valid 4-tuple of finite numbers."""
    if len(bbox) != 4:
        return None
    values = tuple(_finite_number(value) for value in bbox)
    if any(value is None for value in values):
        return None
    x0, y0, x1, y1, *_ = values
    if x1 < x0 or y1 < y0:
        return None
    return x0, y0, x1, y1


def _paragraph_bbox(
    lines: tuple[TextLine, ...],
    blocks: tuple[LayoutBlock, ...],
) -> tuple[float, float, float, float] | None:
    """Union of the valid line bboxes (falling back to block bboxes)."""
    line_boxes = [bbox for line in lines if (bbox := _valid_bbox(line.bbox))]
    if line_boxes:
        candidates = line_boxes
    else:
        candidates = [bbox for block in blocks if (bbox := _valid_bbox(block.bbox))]
    if not candidates:
        return None
    return (
        min(bbox[0] for bbox in candidates),
        min(bbox[1] for bbox in candidates),
        max(bbox[2] for bbox in candidates),
        max(bbox[3] for bbox in candidates),
    )


def _clamp01(value: float) -> float:
    """Clamp a normalized fraction into ``[0.0, 1.0]``."""
    return min(max(value, 0.0), 1.0)


def _normalize_text(text: str) -> str:
    """Build the comparison key for a paragraph's text.

    Applies Unicode NFKC normalization, collapses/allows surrounding
    whitespace, and case-folds. The result is used only for grouping and
    comparison; the original text is preserved for output.
    """
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def _median(values: Iterable[float]) -> float:
    """Median of ``values`` (``0.0`` when empty)."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return float(median(ordered))


def _in_header_region(top_fraction: float) -> bool:
    """Whether a normalized top position falls in the header band."""
    return top_fraction <= HEADER_REGION_FRACTION


def _in_footer_region(bottom_fraction: float) -> bool:
    """Whether a normalized bottom position falls in the footer band."""
    return bottom_fraction >= 1.0 - FOOTER_REGION_FRACTION


def _region_for(
    top_fraction: float, bottom_fraction: float
) -> HeaderFooterType | None:
    """Region label for one occurrence.

    The header band takes precedence when a paragraph straddles both bands
    (a full-page block); such an occurrence is treated as a header-region
    occurrence, which is the documented deterministic tie-break.
    """
    if _in_header_region(top_fraction):
        return HeaderFooterType.HEADER
    if _in_footer_region(bottom_fraction):
        return HeaderFooterType.FOOTER
    return None


def _tolerance_for(kind: HeaderFooterType) -> float:
    """Positional tolerance for a given furniture kind."""
    if kind is HeaderFooterType.HEADER:
        return HEADER_POSITION_TOLERANCE_FRACTION
    return FOOTER_POSITION_TOLERANCE_FRACTION


def _region_position(occ: _Occurrence) -> float:
    """Normalized vertical position used for consistency/sorting."""
    if occ.region is HeaderFooterType.HEADER:
        return occ.top_fraction
    if occ.region is HeaderFooterType.FOOTER:
        return occ.bottom_fraction
    raise ValueError("occurrence has no region")
# --------------------------------------------------------------------------- #
# Page-number parsing
# --------------------------------------------------------------------------- #


def _page_number_key(norm_text: str) -> tuple[str, int] | None:
    """Return ``(template, value)`` when ``norm_text`` is a page-number form.

    The template keeps the *static* parts concrete (e.g. ``"page n of 250"``)
    so only genuinely similar forms group together; the varying page value is
    captured separately for sequence checks.
    """
    match = _PAGE_NUM_OF_RE.match(norm_text)
    if match:
        return (f"page n of {match.group(2)}", int(match.group(1)))
    match = _PAGE_NUM_PAGE_RE.match(norm_text)
    if match:
        return ("page n", int(match.group(1)))
    match = _PAGE_NUM_SLASH_RE.match(norm_text)
    if match:
        return (f"n / {match.group(2)}", int(match.group(1)))
    match = _PAGE_NUM_DASHED_RE.match(norm_text)
    if match:
        return ("- n -", int(match.group(1)))
    match = _PAGE_NUM_BARE_RE.match(norm_text)
    if match:
        return ("n", int(match.group(1)))
    return None


def _page_number_like(norm_text: str) -> bool:
    """Whether ``norm_text`` itself looks like a page-number form."""
    return _page_number_key(norm_text) is not None


# --------------------------------------------------------------------------- #
# Evidence and scoring
# --------------------------------------------------------------------------- #


def _repetition_evidence(distinct_pages: int) -> float:
    """Repetition evidence from the number of distinct pages."""
    return min(distinct_pages / REPETITION_SATURATION_PAGES, 1.0)


def _consistency_evidence(spread: float, tolerance: float) -> float:
    """Positional-consistency evidence (``1.0`` perfect, ``0.0`` at tolerance)."""
    return max(0.0, 1.0 - min(spread / tolerance, 1.0))


def _isolation_evidence(occurrences: tuple[_Occurrence, ...]) -> float:
    """Weak isolation evidence from shortness and single-line shape."""
    single_line = all(occ.line_count == 1 for occ in occurrences)
    short = _median(occ.char_count for occ in occurrences) <= SHORT_HEADER_FOOTER_CHARS
    if single_line and short:
        return 1.0
    if single_line or short:
        return 0.5
    return 0.0
def _compute_confidence(
    repetition: float,
    consistency: float,
    isolation: float,
    page_number: float,
) -> float:
    """Combine evidence into a deterministic heuristic confidence in ``[0, 1]``.

    Repetition and positional consistency dominate the score; isolation and
    page-number structure are secondary. The result is rounded to four
    decimal places and is a heuristic score, not a probability.
    """
    return round(
        REPETITION_SCORE_WEIGHT * repetition
        + POSITION_CONSISTENCY_SCORE_WEIGHT * consistency
        + ISOLATION_SCORE_WEIGHT * isolation
        + PAGE_NUMBER_SCORE_WEIGHT * page_number,
        4,
    )


def _build_reasons(
    kind: HeaderFooterType,
    occurrences: tuple[_Occurrence, ...],
    page_number_like: bool,
    sequence: bool,
) -> tuple[str, ...]:
    """Deterministic evidence tokens for one candidate."""
    reasons = [
        "repeated_across_pages",
        f"position_in_{kind.value}_region",
        "positional_consistency",
    ]
    if page_number_like:
        reasons.append("page_number_pattern")
    if sequence:
        reasons.append("page_number_sequence")
    if _median(occ.char_count for occ in occurrences) <= SHORT_HEADER_FOOTER_CHARS:
        reasons.append("short_text")
    if all(occ.line_count == 1 for occ in occurrences):
        reasons.append("single_line")
    return tuple(reasons)


# --------------------------------------------------------------------------- #
# Candidate construction
# --------------------------------------------------------------------------- #
def _make_candidate(
    kind: HeaderFooterType,
    occurrences: Iterable[_Occurrence],
    page_number_like: bool = False,
    sequence: bool = False,
) -> _Candidate | None:
    """Turn region-bucketed occurrences into a candidate (or ``None``).

    Applies the conservative gates in order: minimum distinct pages, region
    tolerance on vertical positions, median length cap, and the heuristic
    confidence threshold. Returns ``None`` when any gate fails.
    """
    ordered = sorted(occurrences, key=lambda occ: (occ.page_number, occ.order_index))
    pages = tuple(sorted({occ.page_number for occ in ordered}))
    if len(pages) < MIN_REPEATED_PAGES:
        return None

    positions = [_region_position(occ) for occ in ordered]
    spread = max(positions) - min(positions)
    tolerance = _tolerance_for(kind)
    if spread > tolerance:
        return None

    char_counts = [occ.char_count for occ in ordered]
    if _median(char_counts) > MAX_HEADER_FOOTER_CHARS:
        return None

    repetition = _repetition_evidence(len(pages))
    consistency = _consistency_evidence(spread, tolerance)
    isolation = _isolation_evidence(tuple(ordered))
    page_number_evidence = 1.0 if page_number_like else 0.0
    confidence = _compute_confidence(
        repetition, consistency, isolation, page_number_evidence
    )
    if confidence < HEADER_FOOTER_CONFIDENCE_THRESHOLD:
        return None

    return _Candidate(
        text=ordered[0].paragraph.text,
        kind=kind,
        occurrences=tuple(ordered),
        pages=pages,
        position=_median(positions),
        x_center=_median(occ.x_center_fraction for occ in ordered),
        min_order_index=min(occ.order_index for occ in ordered),
        confidence=confidence,
        reasons=_build_reasons(kind, tuple(ordered), page_number_like, sequence),
        page_number_like=page_number_like,
    )
def _exact_text_candidates(
    groups: dict[str, list[_Occurrence]],
) -> list[_Candidate]:
    """Candidates from groups of identical normalized text.

    Header and footer buckets of a group are evaluated independently, so the
    same text may yield both a header and a footer candidate without ever
    collapsing or double-counting an occurrence.
    """
    candidates: list[_Candidate] = []
    for norm_text in sorted(groups):
        group = groups[norm_text]
        page_number_like = _page_number_like(norm_text)
        for kind in (HeaderFooterType.HEADER, HeaderFooterType.FOOTER):
            bucket = [occ for occ in group if occ.region is kind]
            if not bucket:
                continue
            candidate = _make_candidate(
                kind, bucket, page_number_like=page_number_like, sequence=False
            )
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _page_number_sequence_candidates(
    occurrences: list[_Occurrence],
    claimed_paragraph_ids: set[int],
) -> list[_Candidate]:
    """Candidates from varied page-number sequences (e.g. ``1, 2, 3``).

    Only narrow, deterministic page-number forms participate, and only when
    the sequence is monotonic with at least two distinct values. Occurrences
    already claimed by an exact-text candidate are excluded so nothing is
    double-counted.
    """
    groups: dict[str, list[_Occurrence]] = {}
    for occ in occurrences:
        if id(occ.paragraph) in claimed_paragraph_ids:
            continue
        key = _page_number_key(occ.norm_text)
        if key is None:
            continue
        template, _value = key
        groups.setdefault(template, []).append(occ)

    candidates: list[_Candidate] = []
    for template in sorted(groups):
        group = groups[template]
        for kind in (HeaderFooterType.HEADER, HeaderFooterType.FOOTER):
            bucket = [occ for occ in group if occ.region is kind]
            if not bucket:
                continue
            ordered = sorted(bucket, key=lambda occ: (occ.page_number, occ.order_index))
            values = [int(_page_number_key(occ.norm_text)[1]) for occ in ordered]
            if max(values) - min(values) < 1:
                # No variation: an identical placeholder is exact-text
                # territory, not a sequence.
                continue
            nondecreasing = all(a <= b for a, b in zip(values, values[1:]))
            nonincreasing = all(a >= b for a, b in zip(values, values[1:]))
            if not (nondecreasing or nonincreasing):
                # Arbitrary changing numbers are deliberately not merged.
                continue
            candidate = _make_candidate(
                kind, bucket, page_number_like=True, sequence=True
            )
            if candidate is not None:
                candidates.append(candidate)
    return candidates
# --------------------------------------------------------------------------- #
# Occurrence collection
# --------------------------------------------------------------------------- #


def _collect_occurrences(
    pages: tuple[ParagraphPage, ...],
) -> list[_Occurrence]:
    """Collect normalized occurrences in document order.

    Pages whose dimensions are missing/not positive cannot be normalized and
    are skipped (documented conservative behavior). Paragraphs without valid
    geometry are likewise ignored; both still advance the global order index
    so output ordering stays deterministic and stable.
    """
    occurrences: list[_Occurrence] = []
    order_index = 0
    for page in pages:
        page_width = _finite_positive(page.page.page_width)
        page_height = _finite_positive(page.page.page_height)
        if page_width is None or page_height is None:
            order_index += len(page.paragraphs)
            continue
        for para in page.paragraphs:
            bbox = _paragraph_bbox(para.source_lines, para.source_blocks)
            if bbox is None:
                order_index += 1
                continue
            x0, y0, x1, y1 = bbox
            top_fraction = _clamp01(y0 / page_height)
            bottom_fraction = _clamp01(y1 / page_height)
            x_center_fraction = _clamp01((x0 + x1) / (2.0 * page_width))
            occurrences.append(
                _Occurrence(
                    paragraph=para,
                    page_number=page.page_number,
                    order_index=order_index,
                    top_fraction=top_fraction,
                    bottom_fraction=bottom_fraction,
                    x_center_fraction=x_center_fraction,
                    char_count=len(para.text),
                    line_count=len(para.source_lines),
                    norm_text=_normalize_text(para.text),
                    region=_region_for(top_fraction, bottom_fraction),
                )
            )
            order_index += 1
    return occurrences
# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def _coerce_pages(
    source: ParagraphPage | ParagraphLayout,
) -> tuple[ParagraphPage, ...]:
    """Normalize the input to a tuple of pages."""
    if isinstance(source, ParagraphLayout):
        return source.pages
    if isinstance(source, ParagraphPage):
        return (source,)
    raise TypeError(
        "source must be a ParagraphLayout or ParagraphPage, "
        f"got {type(source).__name__}"
    )


def _candidate_sort_key(candidate: _Candidate) -> tuple[object, ...]:
    """Deterministic candidate ordering: page, vertical, horizontal, source."""
    return (
        candidate.pages[0],
        candidate.position,
        candidate.x_center,
        candidate.min_order_index,
    )


def _to_detected(candidate: _Candidate) -> DetectedHeaderFooter:
    """Convert an internal candidate into the public immutable result."""
    return DetectedHeaderFooter(
        text=candidate.text,
        kind=candidate.kind,
        pages=candidate.pages,
        confidence=candidate.confidence,
        reasons=candidate.reasons,
        source_paragraphs=tuple(occ.paragraph for occ in candidate.occurrences),
    )


def detect_headers_footers(
    source: ParagraphPage | ParagraphLayout,
) -> HeaderFooterLayout:
    """Detect repeated page headers and footers in a paragraph layout.

    Parameters
    ----------
    source:
        The M2.3 paragraph reconstruction output for a single page
        (:class:`~kindle_converter.pdf.paragraphs.ParagraphPage`) or a whole
        document (:class:`~kindle_converter.pdf.paragraphs.ParagraphLayout`).
        Page and paragraph order are preserved and never mutated.

    Returns
    -------
    HeaderFooterLayout
        One :class:`HeaderFooterPage` per input page (in input order) plus
        the flattened, deterministically ordered tuple of every detected
        element. ``detected`` is empty when no conservative candidate is
        found.

    Raises
    ------
    TypeError
        If ``source`` is not a ``ParagraphPage`` or ``ParagraphLayout``.
    """
    pages = _coerce_pages(source)

    occurrences = _collect_occurrences(pages)

    groups: dict[str, list[_Occurrence]] = {}
    for occ in occurrences:
        groups.setdefault(occ.norm_text, []).append(occ)

    exact_candidates = _exact_text_candidates(groups)
    claimed_paragraph_ids = {
        id(occ.paragraph)
        for candidate in exact_candidates
        for occ in candidate.occurrences
    }
    sequence_candidates = _page_number_sequence_candidates(
        occurrences, claimed_paragraph_ids
    )

    candidates = exact_candidates + sequence_candidates
    candidates.sort(key=_candidate_sort_key)

    detected = tuple(_to_detected(candidate) for candidate in candidates)

    layout_pages = tuple(
        HeaderFooterPage(
            page_number=page.page_number,
            detected=tuple(
                item for item in detected if page.page_number in item.pages
            ),
        )
        for page in pages
    )
    return HeaderFooterLayout(pages=layout_pages, detected=detected)


def classify_header_footer_paragraphs(
    source: ParagraphPage | ParagraphLayout,
) -> tuple[HeaderFooterLayout, tuple[ClassifiedHeaderFooterParagraph, ...]]:
    """Classify every paragraph as detected furniture or not.

    Convenience wrapper around :func:`detect_headers_footers` that also
    produces one :class:`ClassifiedHeaderFooterParagraph` per M2.3 paragraph
    (in page/paragraph order) so downstream stages can see *why* a paragraph
    was or was not detected.

    Parameters
    ----------
    source:
        The M2.3 paragraph reconstruction output (single page or document).

    Returns
    -------
    tuple[HeaderFooterLayout, tuple[ClassifiedHeaderFooterParagraph, ...]]
        The layout result, followed by one classification record per input
        paragraph in M2.3 order.
    """
    layout = detect_headers_footers(source)

    info: dict[int, tuple[HeaderFooterType | None, float, tuple[str, ...]]] = {}
    for item in layout.detected:
        for para in item.source_paragraphs:
            info[id(para)] = (item.kind, item.confidence, item.reasons)

    pages = _coerce_pages(source)
    classified = tuple(
        ClassifiedHeaderFooterParagraph(
            paragraph=para,
            kind=info.get(id(para), (None, 0.0, ()))[0],
            score=info.get(id(para), (None, 0.0, ()))[1],
            reasons=info.get(id(para), (None, 0.0, ()))[2],
        )
        for page in pages
        for para in page.paragraphs
    )
    return layout, classified