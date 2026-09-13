"""Conservative layout-based heading detection (Milestone 2.4).

This module consumes :mod:`kindle_converter.pdf.paragraphs` output and
classifies each reconstructed paragraph without changing its text, geometry,
source objects, page association, or reading order. Detection is deliberately
heuristic: typography, alignment, spacing, length, and local context contribute
independent evidence, and no single signal is sufficient.

The detector operates independently on each :class:`~kindle_converter.pdf.paragraphs.ParagraphPage`.
Pages remain in their input order and paragraphs remain in M2.3 order. It does
not perform OCR, semantic NLP, chapter parsing, header/footer removal, table or
image detection, multi-column reconstruction, or cross-page paragraph merging.

Heading-detection philosophy
----------------------------
The detector is **conservative**. False positives are more damaging than
failing to detect an occasional heading. A paragraph must generally require
**multiple independent signals** before being classified as a heading:

* shortness alone is insufficient
* centered alignment alone is insufficient
* font size alone is insufficient
* bold alone is insufficient

Signals are combined through a small transparent scoring model. Each signal
contributes a fixed, named weight; the final score is the sum. A paragraph is
classified as a heading only when it accumulates enough independent evidence
(``MIN_HEADING_SIGNALS`` distinct positive reasons) *and* its total score
reaches ``HEADING_SCORE_THRESHOLD``.

Typography signals
------------------
* **Font size** relative to the page's prevailing body baseline (median of
  paragraph median font sizes). A paragraph whose median font size is
  ``FONT_SIZE_RATIO_THRESHOLD``× the baseline contributes positive evidence;
  ``STRONG_FONT_SIZE_RATIO_THRESHOLD``× contributes stronger evidence.
* **Bold typography** from ``FontFlags.BOLD`` combined with a weight-like
  font name (e.g. containing "Bold" or "Black"). A bare bold flag without a
  weight-like name is weaker evidence because PDF font flags describe
  *capability*, not usage.
* **Italic typography** from ``FontFlags.ITALIC`` or an italic-like font name.
* **Font-family change** relative to the page's dominant font family.

Geometry signals
----------------
* **Centered alignment** when the paragraph's left and right margins are
  roughly equal and it does not span most of the page width.
* **Large whitespace before/after** the paragraph, relative to the page's
  prevailing line height and the median gap between neighboring paragraphs.
* **Isolation** when the paragraph has large whitespace on both sides, or
  large whitespace on one side combined with another strong cue.
* **Page-edge proximity** as weak supporting evidence.

Length signals
--------------
* **Short paragraph** (≤ ``SHORT_PARAGRAPH_CHARS``) contributes weak positive
  evidence.
* **Very short paragraph** (≤ ``VERY_SHORT_PARAGRAPH_CHARS``) contributes
  slightly stronger evidence.
* **Long paragraph** (> ``LONG_PARAGRAPH_CHARS``) is penalized as likely body
  content.

Determinism
-----------
All computations are deterministic. No randomization, timestamps, external
services, or machine-learning inference is used. Given the same input, the
same output is always produced.

Provenance
----------
The classifier never mutates ``ReconstructedParagraph``, ``TextLine``,
``LayoutBlock``, ``OrderedBlock``, ``OrderedPage``, or ``OrderedLayout``.
It references or wraps the existing immutable objects.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Iterable

from .layout import FontFlags, LayoutBlock, TextLine
from .paragraphs import ParagraphLayout, ParagraphPage, ReconstructedParagraph


# --------------------------------------------------------------------------- #
# Threshold and weight constants (centralized, deterministic, documented)
# --------------------------------------------------------------------------- #

#: Minimum paragraph median font size divided by the local body baseline that
#: contributes positive size evidence.
FONT_SIZE_RATIO_THRESHOLD = 1.25

#: Font-size ratio that contributes strong positive evidence.
STRONG_FONT_SIZE_RATIO_THRESHOLD = 1.5

#: Minimum number of independent positive reasons required for a heading.
MIN_HEADING_SIGNALS = 2

#: Score required in addition to ``MIN_HEADING_SIGNALS``.
HEADING_SCORE_THRESHOLD = 2.5

#: Paragraph text length considered short enough for weak supporting evidence.
SHORT_PARAGRAPH_CHARS = 80

#: Paragraph text length considered very short for slightly stronger evidence.
VERY_SHORT_PARAGRAPH_CHARS = 30

#: Paragraph text length that is penalized as likely body content.
LONG_PARAGRAPH_CHARS = 180

#: Minimum vertical whitespace (points) considered unusually large.
LARGE_SPACING_MIN_PT = 18.0

#: Vertical whitespace threshold multiplier relative to the prevailing line
#: height.
LARGE_SPACING_LINE_FACTOR = 1.75

#: Vertical whitespace threshold multiplier relative to the median positive
#: gap between neighboring paragraphs.
LARGE_SPACING_GAP_FACTOR = 1.25

#: Maximum distance from the page horizontal center for centered alignment.
CENTERED_ALIGNMENT_TOLERANCE_PT = 4.0

#: Maximum paragraph width fraction for centered-alignment evidence.
CENTERED_WIDTH_FRACTION = 0.6

#: Distance from a page edge used as weak supporting evidence.
PAGE_EDGE_MARGIN_PT = 72.0

#: Number of neighboring paragraphs used for a local typography baseline.
LOCAL_CONTEXT_RADIUS = 2

#: Positive evidence for a significant relative font-size increase.
FONT_SIZE_ABOVE_BASELINE_SCORE = 1.0

#: Additional evidence for a strong relative font-size increase.
STRONG_FONT_SIZE_ABOVE_BASELINE_SCORE = 0.5

#: Evidence for a large font-size contrast within a mixed-size paragraph.
MIXED_FONT_SIZE_CONTRAST_SCORE = 0.5

#: Evidence for confirmed bold typography (font flag plus a weight-like name).
BOLD_TYPOGRAPHY_SCORE = 1.0

#: Evidence for a bold flag without a weight-like font name. The flag is
#: treated as supporting evidence because PDF font flags describe capability.
UNCONFIRMED_BOLD_TYPOGRAPHY_SCORE = 0.45

#: Evidence for italic typography.
ITALIC_TYPOGRAPHY_SCORE = 0.35

#: Evidence for a paragraph font family that differs from the page baseline.
FONT_FAMILY_CHANGE_SCORE = 0.45

#: Evidence for centered alignment.
CENTERED_ALIGNMENT_SCORE = 0.65

#: Evidence for unusually large whitespace before a paragraph.
LARGE_SPACING_BEFORE_SCORE = 0.75

#: Evidence for unusually large whitespace after a paragraph.
LARGE_SPACING_AFTER_SCORE = 0.75

#: Evidence for isolation on both sides or one side with another strong cue.
ISOLATED_PARAGRAPH_SCORE = 0.5

#: Weak evidence for a short paragraph.
SHORT_PARAGRAPH_SCORE = 0.25

#: Evidence for a short multi-line paragraph that already has another cue.
MULTI_LINE_HEADING_SHAPE_SCORE = 0.25

#: Weak evidence for proximity to a page edge.
PAGE_EDGE_PROXIMITY_SCORE = 0.2

#: Penalty for a long paragraph that otherwise resembles body text.
LONG_PARAGRAPH_PENALTY = -1.0

#: Penalty for typography and geometry that are entirely body-like.
BODY_LIKE_PENALTY = -0.5


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ClassifiedParagraph:
    """A preserved M2.3 paragraph and its deterministic heading decision."""

    paragraph: ReconstructedParagraph
    is_heading: bool
    score: float
    reasons: tuple[str, ...]

    @property
    def text(self) -> str:
        return self.paragraph.text

    @property
    def page_number(self) -> int:
        return self.paragraph.page_number

    @property
    def source_lines(self) -> tuple[TextLine, ...]:
        return self.paragraph.source_lines

    @property
    def source_blocks(self) -> tuple[LayoutBlock, ...]:
        return self.paragraph.source_blocks


@dataclass(frozen=True, slots=True)
class DetectedHeading:
    """A view of one paragraph classified as a heading."""

    paragraph: ReconstructedParagraph
    score: float
    reasons: tuple[str, ...]

    @property
    def text(self) -> str:
        return self.paragraph.text

    @property
    def page_number(self) -> int:
        return self.paragraph.page_number

    @property
    def source_lines(self) -> tuple[TextLine, ...]:
        return self.paragraph.source_lines

    @property
    def source_blocks(self) -> tuple[LayoutBlock, ...]:
        return self.paragraph.source_blocks


@dataclass(frozen=True, slots=True)
class HeadingPage:
    """One M2.3 page with every paragraph classified in input order."""

    page_number: int
    paragraphs: tuple[ClassifiedParagraph, ...]

    @property
    def paragraph_count(self) -> int:
        return len(self.paragraphs)

    @property
    def heading_count(self) -> int:
        return sum(item.is_heading for item in self.paragraphs)

    @property
    def headings(self) -> tuple[DetectedHeading, ...]:
        return tuple(
            DetectedHeading(
                paragraph=item.paragraph,
                score=item.score,
                reasons=item.reasons,
            )
            for item in self.paragraphs
            if item.is_heading
        )


@dataclass(frozen=True, slots=True)
class HeadingLayout:
    """The complete heading-classification result in page order."""

    pages: tuple[HeadingPage, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def paragraph_count(self) -> int:
        return sum(page.paragraph_count for page in self.pages)

    @property
    def heading_count(self) -> int:
        return sum(page.heading_count for page in self.pages)

    @property
    def paragraphs(self) -> tuple[ClassifiedParagraph, ...]:
        return tuple(
            classified
            for page in self.pages
            for classified in page.paragraphs
        )

    @property
    def headings(self) -> tuple[DetectedHeading, ...]:
        return tuple(
            heading
            for page in self.pages
            for heading in page.headings
        )


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _ParagraphMetrics:
    """Computed typography/geometry metrics for one paragraph."""

    paragraph: ReconstructedParagraph
    bbox: tuple[float, float, float, float] | None
    font_sizes: tuple[float, ...]
    font_families: tuple[str, ...]
    median_font_size: float | None
    max_font_size: float | None
    dominant_font_family: str | None
    has_bold_flag: bool
    has_confirmed_bold: bool
    has_italic: bool
    char_count: int
    line_count: int
    centered: bool
    near_page_edge: bool


@dataclass(frozen=True, slots=True)
class _PageContext:
    """Per-page typography/geometry baseline."""

    baseline_font_size: float | None
    baseline_font_family: str | None
    median_line_height: float
    spacing_threshold: float


def _finite_number(value: object) -> float | None:
    """Return ``value`` as a finite float, or ``None`` if not usable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not isfinite(result):
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
    """Return ``bbox`` if it is a valid 4-tuple of finite numbers, else None."""
    if len(bbox) != 4:
        return None
    values = tuple(_finite_number(value) for value in bbox)
    if any(value is None for value in values):
        return None
    x0, y0, x1, y1, *_ = values
    if x1 < x0 or y1 < y0:
        return None
    return x0, y0, x1, y1


def _union_bbox(
    lines: tuple[TextLine, ...], blocks: tuple[LayoutBlock, ...]
) -> tuple[float, float, float, float] | None:
    """Union of the valid line bboxes (falling back to block bboxes)."""
    line_boxes = [bbox for line in lines if (bbox := _valid_bbox(line.bbox))]
    if line_boxes:
        candidates = line_boxes
    else:
        candidates = [
            bbox for block in blocks if (bbox := _valid_bbox(block.bbox))
        ]
    if not candidates:
        return None
    return (
        min(bbox[0] for bbox in candidates),
        min(bbox[1] for bbox in candidates),
        max(bbox[2] for bbox in candidates),
        max(bbox[3] for bbox in candidates),
    )


def _median(values: Iterable[float]) -> float | None:
    """Median of ``values`` (``None`` when empty)."""
    ordered = sorted(values)
    if not ordered:
        return None
    return float(median(ordered))


def _median_low(values: Iterable[float]) -> float | None:
    """Lower-median of ``values`` (``None`` when empty).

    For an even count, returns the lower of the two middle values. This is
    more robust than the arithmetic mean for establishing a body baseline
    when a page contains a few large headings among many body paragraphs.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    return float(ordered[(len(ordered) - 1) // 2])


def _collect_font_sizes(paragraph: ReconstructedParagraph) -> tuple[float, ...]:
    """All finite positive font sizes across the paragraph's spans."""
    sizes: list[float] = []
    for line in paragraph.source_lines:
        for span in line.spans:
            size = _finite_positive(span.font_size)
            if size is not None:
                sizes.append(size)
    return tuple(sizes)


def _collect_font_families(paragraph: ReconstructedParagraph) -> tuple[str, ...]:
    """All non-empty font names across the paragraph's spans."""
    families: list[str] = []
    for line in paragraph.source_lines:
        for span in line.spans:
            name = span.font_name
            if name and name.strip():
                families.append(name.strip())
    return tuple(families)


def _dominant_font_family(families: tuple[str, ...]) -> str | None:
    """The most frequent font family (ties broken by first occurrence)."""
    if not families:
        return None
    counts = Counter(families)
    # Counter.most_common is deterministic for equal counts? No -- it
    # preserves insertion order for ties, which is deterministic here
    # because families is built in a fixed order.
    return counts.most_common(1)[0][0]


def _is_bold_font_name(name: str) -> bool:
    """Whether a font name suggests a bold/black weight."""
    lower = name.lower()
    return any(token in lower for token in ("bold", "black", "heavy", "demibold"))


def _is_italic_font_name(name: str) -> bool:
    """Whether a font name suggests an italic/oblique style."""
    lower = name.lower()
    return any(token in lower for token in ("italic", "oblique"))


def _paragraph_metrics(
    paragraph: ReconstructedParagraph,
    page_width: float,
    page_height: float,
) -> _ParagraphMetrics:
    """Compute typography/geometry metrics for one paragraph."""
    bbox = _union_bbox(paragraph.source_lines, paragraph.source_blocks)
    font_sizes = _collect_font_sizes(paragraph)
    font_families = _collect_font_families(paragraph)

    median_size = _median(font_sizes)
    max_size = max(font_sizes) if font_sizes else None
    dominant_family = _dominant_font_family(font_families)

    has_bold_flag = False
    has_confirmed_bold = False
    has_italic = False
    for line in paragraph.source_lines:
        for span in line.spans:
            flags = span.font_flags
            if FontFlags.BOLD in flags:
                has_bold_flag = True
                name = span.font_name or ""
                if _is_bold_font_name(name):
                    has_confirmed_bold = True
            if FontFlags.ITALIC in flags or _is_italic_font_name(span.font_name or ""):
                has_italic = True

    char_count = len(paragraph.text)
    line_count = paragraph.line_count

    # Centered alignment: left and right margins roughly equal, and the
    # paragraph does not span most of the page width.
    centered = False
    if bbox is not None and page_width > 0:
        left_margin = bbox[0]
        right_margin = page_width - bbox[2]
        width = bbox[2] - bbox[0]
        if width <= CENTERED_WIDTH_FRACTION * page_width:
            if abs(left_margin - right_margin) <= CENTERED_ALIGNMENT_TOLERANCE_PT:
                centered = True

    # Page-edge proximity: paragraph bbox near the top or bottom of the page.
    near_page_edge = False
    if bbox is not None:
        if bbox[1] < PAGE_EDGE_MARGIN_PT:
            near_page_edge = True
        if page_height - bbox[3] < PAGE_EDGE_MARGIN_PT:
            near_page_edge = True

    return _ParagraphMetrics(
        paragraph=paragraph,
        bbox=bbox,
        font_sizes=font_sizes,
        font_families=font_families,
        median_font_size=median_size,
        max_font_size=max_size,
        dominant_font_family=dominant_family,
        has_bold_flag=has_bold_flag,
        has_confirmed_bold=has_confirmed_bold,
        has_italic=has_italic,
        char_count=char_count,
        line_count=line_count,
        centered=centered,
        near_page_edge=near_page_edge,
    )


def _page_context(
    paragraphs: tuple[ReconstructedParagraph, ...],
    page_width: float,
    page_height: float,
) -> _PageContext:
    """Establish a per-page typography/geometry baseline.

    The baseline font size is the lower-median of paragraph median font
    sizes, which is robust against a few large headings among many body
    paragraphs. The baseline font family is the most frequent family across
    all paragraphs. The spacing threshold is derived from the median line
    height and the median positive gap between neighboring paragraphs.
    """
    if not paragraphs:
        return _PageContext(
            baseline_font_size=None,
            baseline_font_family=None,
            median_line_height=1.0,
            spacing_threshold=LARGE_SPACING_MIN_PT,
        )

    # Collect paragraph median font sizes.
    para_medians: list[float] = []
    all_families: list[str] = []
    line_heights: list[float] = []
    for para in paragraphs:
        metrics = _paragraph_metrics(para, page_width, page_height)
        if metrics.median_font_size is not None:
            para_medians.append(metrics.median_font_size)
        all_families.extend(metrics.font_families)
        for line in para.source_lines:
            height = _finite_positive(line.height)
            if height is not None:
                line_heights.append(height)

    baseline_size = _median_low(para_medians)
    baseline_family = _dominant_font_family(tuple(all_families))
    median_line_height = _median(line_heights) or 1.0

    # Compute the median positive gap between consecutive paragraphs.
    gaps: list[float] = []
    prev_bbox: tuple[float, float, float, float] | None = None
    for para in paragraphs:
        metrics = _paragraph_metrics(para, page_width, page_height)
        bbox = metrics.bbox
        if bbox is not None and prev_bbox is not None:
            gap = bbox[1] - prev_bbox[3]
            if gap > 0:
                gaps.append(gap)
        if bbox is not None:
            prev_bbox = bbox

    median_gap = _median(gaps)
    spacing_threshold = max(
        LARGE_SPACING_MIN_PT,
        LARGE_SPACING_LINE_FACTOR * median_line_height,
    )
    if median_gap is not None:
        spacing_threshold = max(
            spacing_threshold,
            LARGE_SPACING_GAP_FACTOR * median_gap,
        )

    return _PageContext(
        baseline_font_size=baseline_size,
        baseline_font_family=baseline_family,
        median_line_height=median_line_height,
        spacing_threshold=spacing_threshold,
    )


def _spacing_before(
    idx: int,
    paragraphs: tuple[ReconstructedParagraph, ...],
    page_width: float,
    page_height: float,
) -> float | None:
    """Vertical whitespace (points) before the paragraph at ``idx``, or None.

    The paragraph position is determined by its actual index in the
    supplied M2.3 sequence, never by value equality.
    """
    if idx == 0:
        return None
    prev_metrics = _paragraph_metrics(paragraphs[idx - 1], page_width, page_height)
    curr_metrics = _paragraph_metrics(paragraphs[idx], page_width, page_height)
    if prev_metrics.bbox is None or curr_metrics.bbox is None:
        return None
    gap = curr_metrics.bbox[1] - prev_metrics.bbox[3]
    return max(0.0, gap)


def _spacing_after(
    idx: int,
    paragraphs: tuple[ReconstructedParagraph, ...],
    page_width: float,
    page_height: float,
) -> float | None:
    """Vertical whitespace (points) after the paragraph at ``idx``, or None.

    The paragraph position is determined by its actual index in the
    supplied M2.3 sequence, never by value equality.
    """
    if idx == len(paragraphs) - 1:
        return None
    curr_metrics = _paragraph_metrics(paragraphs[idx], page_width, page_height)
    next_metrics = _paragraph_metrics(paragraphs[idx + 1], page_width, page_height)
    if curr_metrics.bbox is None or next_metrics.bbox is None:
        return None
    gap = next_metrics.bbox[1] - curr_metrics.bbox[3]
    return max(0.0, gap)


def _classify_paragraph(
    idx: int,
    paragraph: ReconstructedParagraph,
    paragraphs: tuple[ReconstructedParagraph, ...],
    context: _PageContext,
    page_width: float,
    page_height: float,
) -> ClassifiedParagraph:
    """Classify one paragraph using typography, geometry, and context.

    ``idx`` is the paragraph's actual position in the supplied M2.3
    sequence; it is used for neighbor-based spacing calculations so that
    value-identical paragraphs at different positions are never confused.

    The scoring model is transparent and deterministic. Each independent
    signal contributes a fixed named weight. A paragraph is a heading only
    when it accumulates at least ``MIN_HEADING_SIGNALS`` distinct positive
    reasons *and* its total score reaches ``HEADING_SCORE_THRESHOLD``.
    """
    metrics = _paragraph_metrics(paragraph, page_width, page_height)
    reasons: list[str] = []
    score = 0.0

    # --- Font-size evidence (relative to page baseline) ---
    if metrics.median_font_size is not None and context.baseline_font_size is not None:
        ratio = metrics.median_font_size / context.baseline_font_size
        if ratio >= STRONG_FONT_SIZE_RATIO_THRESHOLD:
            score += FONT_SIZE_ABOVE_BASELINE_SCORE + STRONG_FONT_SIZE_ABOVE_BASELINE_SCORE
            reasons.append("font_size_strongly_above_body_baseline")
        elif ratio >= FONT_SIZE_RATIO_THRESHOLD:
            score += FONT_SIZE_ABOVE_BASELINE_SCORE
            reasons.append("font_size_above_body_baseline")

    # --- Mixed font-size contrast within the paragraph ---
    if (
        metrics.max_font_size is not None
        and metrics.median_font_size is not None
        and metrics.median_font_size > 0
        and metrics.max_font_size / metrics.median_font_size
        >= STRONG_FONT_SIZE_RATIO_THRESHOLD
    ):
        score += MIXED_FONT_SIZE_CONTRAST_SCORE
        reasons.append("mixed_font_size_contrast")

    # --- Bold typography ---
    if metrics.has_confirmed_bold:
        score += BOLD_TYPOGRAPHY_SCORE
        reasons.append("bold_typography")
    elif metrics.has_bold_flag:
        score += UNCONFIRMED_BOLD_TYPOGRAPHY_SCORE
        reasons.append("bold_font_flag")

    # --- Italic typography ---
    if metrics.has_italic:
        score += ITALIC_TYPOGRAPHY_SCORE
        reasons.append("italic_typography")

    # --- Font-family change ---
    if (
        metrics.dominant_font_family is not None
        and context.baseline_font_family is not None
        and metrics.dominant_font_family != context.baseline_font_family
    ):
        score += FONT_FAMILY_CHANGE_SCORE
        reasons.append("font_family_change")

    # --- Centered alignment ---
    if metrics.centered:
        score += CENTERED_ALIGNMENT_SCORE
        reasons.append("centered_alignment")

    # --- Large whitespace before/after ---
    spacing_before = _spacing_before(idx, paragraphs, page_width, page_height)
    spacing_after = _spacing_after(idx, paragraphs, page_width, page_height)

    large_before = (
        spacing_before is not None
        and spacing_before >= context.spacing_threshold
    )
    large_after = (
        spacing_after is not None
        and spacing_after >= context.spacing_threshold
    )

    if large_before:
        score += LARGE_SPACING_BEFORE_SCORE
        reasons.append("large_spacing_before")
    if large_after:
        score += LARGE_SPACING_AFTER_SCORE
        reasons.append("large_spacing_after")

    # --- Isolation (large whitespace on both sides, or one side + another cue) ---
    if large_before and large_after:
        score += ISOLATED_PARAGRAPH_SCORE
        reasons.append("isolated_paragraph")
    elif (large_before or large_after) and (
        metrics.centered
        or metrics.has_confirmed_bold
        or (
            metrics.median_font_size is not None
            and context.baseline_font_size is not None
            and metrics.median_font_size / context.baseline_font_size
            >= FONT_SIZE_RATIO_THRESHOLD
        )
    ):
        # One side of large whitespace combined with another strong cue.
        # The font-size cue requires a genuinely elevated relative size,
        # not merely the presence of font metadata.
        score += ISOLATED_PARAGRAPH_SCORE
        reasons.append("isolated_paragraph")

    # --- Length evidence ---
    if metrics.char_count <= VERY_SHORT_PARAGRAPH_CHARS:
        score += SHORT_PARAGRAPH_SCORE
        reasons.append("very_short_paragraph")
    elif metrics.char_count <= SHORT_PARAGRAPH_CHARS:
        score += SHORT_PARAGRAPH_SCORE
        reasons.append("short_paragraph")

    # --- Multi-line heading shape (short multi-line paragraph with another cue) ---
    if (
        metrics.line_count > 1
        and metrics.char_count <= SHORT_PARAGRAPH_CHARS
        and (metrics.centered or metrics.has_confirmed_bold or large_before or large_after)
    ):
        score += MULTI_LINE_HEADING_SHAPE_SCORE
        reasons.append("multi_line_heading_shape")

    # --- Page-edge proximity (weak supporting evidence) ---
    if metrics.near_page_edge:
        score += PAGE_EDGE_PROXIMITY_SCORE
        reasons.append("page_edge_proximity")

    # --- Penalties ---
    if metrics.char_count > LONG_PARAGRAPH_CHARS:
        score += LONG_PARAGRAPH_PENALTY
        reasons.append("long_paragraph")

    # Body-like penalty: ordinary typography and geometry with no positive
    # evidence at all.
    if not reasons:
        score += BODY_LIKE_PENALTY
        reasons.append("body_like_typography")

    # --- Classification decision ---
    positive_reasons = [r for r in reasons if not r.startswith("body_like")]
    is_heading = (
        len(positive_reasons) >= MIN_HEADING_SIGNALS
        and score >= HEADING_SCORE_THRESHOLD
    )

    return ClassifiedParagraph(
        paragraph=paragraph,
        is_heading=is_heading,
        score=score,
        reasons=tuple(reasons),
    )


def _classify_page(page: ParagraphPage) -> HeadingPage:
    """Classify every paragraph on one page in M2.3 order."""
    paragraphs = page.paragraphs
    context = _page_context(paragraphs, page.page.page_width, page.page.page_height)
    classified = tuple(
        _classify_paragraph(
            idx,
            para,
            paragraphs,
            context,
            page.page.page_width,
            page.page.page_height,
        )
        for idx, para in enumerate(paragraphs)
    )
    return HeadingPage(page_number=page.page_number, paragraphs=classified)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def classify_paragraphs(
    source: ParagraphPage | ParagraphLayout,
) -> HeadingPage | HeadingLayout:
    """Classify every paragraph in a page or whole document.

    Parameters
    ----------
    source:
        Either a :class:`~kindle_converter.pdf.paragraphs.ParagraphPage`
        (single page) or a
        :class:`~kindle_converter.pdf.paragraphs.ParagraphLayout` (whole
        document) from
        :func:`~kindle_converter.pdf.paragraphs.reconstruct_paragraphs`.

    Returns
    -------
    HeadingPage | HeadingLayout
        The classification result. Every paragraph is preserved in M2.3
        order; ``is_heading`` marks the detected headings. The source
        objects are never mutated.
    """
    if isinstance(source, ParagraphLayout):
        pages = tuple(_classify_page(p) for p in source.pages)
        return HeadingLayout(pages=pages)
    return _classify_page(source)


def detect_headings(
    source: ParagraphPage | ParagraphLayout,
) -> HeadingPage | HeadingLayout:
    """Detect headings in a page or whole document.

    This is a convenience wrapper around :func:`classify_paragraphs` that
    returns the same classification result. The name emphasizes the
    heading-detection purpose; the result preserves every paragraph
    (including non-headings) for downstream reconstruction.

    Parameters
    ----------
    source:
        Either a :class:`~kindle_converter.pdf.paragraphs.ParagraphPage`
        (single page) or a
        :class:`~kindle_converter.pdf.paragraphs.ParagraphLayout` (whole
        document) from
        :func:`~kindle_converter.pdf.paragraphs.reconstruct_paragraphs`.

    Returns
    -------
    HeadingPage | HeadingLayout
        The classification result with every paragraph preserved in M2.3
        order.
    """
    return classify_paragraphs(source)