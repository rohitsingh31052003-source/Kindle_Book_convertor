"""Paragraph reconstruction from M2.2 reading order (Milestone 2.3).

This module consumes the Milestone 2.2 reading-order reconstruction
(:class:`~kindle_converter.pdf.reading_order.OrderedPage` /
:class:`~kindle_converter.pdf.reading_order.OrderedLayout`) and groups
physical text lines into logical paragraphs.

The M2.2 reading order is authoritative: this module does NOT reorder
lines. It only decides "do these consecutive lines belong to the same
paragraph?" using geometry and context.

Coordinate convention follows M2.1/M2.2: top-left origin, y grows
downwards, units are PDF points. Page numbers are 1-based.

Paragraph boundary signals
--------------------------
The decision combines multiple observable layout signals; no single
signal is decisive in isolation:

1. **Vertical gap** between consecutive lines, relative to the typical
   line height for nearby lines. A large gap (≫ normal line spacing)
   strongly suggests a paragraph boundary.

2. **Left-edge alignment**. Continuation lines normally share the left
   margin. An indented first line is a common paragraph signal, but the
   decision uses contextual geometry (nearby lines' margins) rather than
   a single comparison.

3. **First-line indentation**. An explicit indent relative to the
   prevailing left margin of the preceding lines supports a new
   paragraph. The reverse (preceding lines indented, new line at margin)
   also supports a boundary.

4. **Continuation-line indentation**. Lines indented relative to the
   first line are treated as continuations (e.g. hanging indents, lists).

 5. **Line width behavior**. Line width is contextual geometry only. A
    final short line is normal and does not create a boundary; a line that
    is shorter or longer than its siblings without other signals does not
    force a split. Line width is never used as a standalone boundary signal.

 6. **LayoutBlock boundaries**. LayoutBlock identity is retained as
    provenance context but is not independently treated as a paragraph
    boundary: one block may contain multiple paragraphs, and multiple
    blocks may form one paragraph. The decision is based on line geometry.

 7. **Centering/alignment**. An isolated centered line is not blindly
    merged into left-aligned body text.

 8. **Short lines**. Short text length alone does not force a paragraph
    boundary. A short line is treated as isolated only when combined with
    additional geometric evidence: centered positioning, significant
    vertical isolation, or proximity to the page edge.

 9. **Page boundaries**. By policy, a page boundary is always a paragraph
    boundary (conservative; cross-page continuation is a later milestone).

Text joining
------------
When multiple lines form one paragraph:
* word boundaries and punctuation are preserved
* a single space is inserted between lines (the "dict" layout drops
  inter-word spaces at line breaks)
* leading indent on the first line (up to 4 spaces) is stripped
* no semantic rewriting is performed

Hyphenation
-----------
Dehyphenation is a conservative line-break heuristic. A line-end hyphen
is removed only when there is strong evidence the hyphen was introduced
solely by line wrapping: the lower line must start with a lowercase
letter, the joined form must not contain a legitimate compound-hyphen
pattern, and the overall geometry suggests a single word split across
lines. Legitimate hyphenated compounds (e.g. "well-known") are
preserved. When in doubt, the original hyphen is kept; no dictionary or
NLP-based word validation is used.

Provenance
----------
Every reconstructed paragraph retains:
* the page number
* the source :class:`~kindle_converter.pdf.layout.TextLine` objects
* the source :class:`~kindle_converter.pdf.layout.LayoutBlock` objects
* the original M2.1 extraction order indices
* the M2.2 reading-order indices
* the :class:`TextSource` of the text (native PDF text vs OCR text)

OCR paragraphs (Milestone 3.6)
------------------------------
:func:`reconstruct_ocr_paragraphs` reconstructs paragraphs from cleaned OCR
text (:class:`~kindle_converter.pdf.ocr_cleanup.CleanedOCRResult`) with no
layout geometry available. Paragraph boundaries come from the only
deterministic signal OCR preserves: blank lines. OCR paragraphs reuse the
same conservative single-space/dehyphenation joining rule as native
paragraphs, carry ``TextSource.OCR``, and keep **empty** source
lines/blocks/order provenance -- no bbox, font, or reading-order metadata is
fabricated for OCR text.

This module is independent of the format-independent
:mod:`kindle_converter.document` model. The domain :class:`Paragraph`
is a simple text container; PDF-specific geometry and provenance stay
in the reconstruction layer. A later integration step maps
:class:`ReconstructedParagraph` to the domain model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .layout import LayoutBlock, LayoutPage, TextLine
from .reading_order import OrderedLayout, OrderedPage


# --------------------------------------------------------------------------- #
# Tolerance and threshold constants (centralized, deterministic, documented)
# --------------------------------------------------------------------------- #

#: Minimum vertical gap (as a multiple of the local median line height)
#: that signals a paragraph boundary. A value of 0.6 means a gap larger
#: than 60 % of the typical line height starts a new paragraph. Normal
#: wrapped lines have a small or negative gap (slight overlap); a blank
#: line produces a gap of roughly one line height.
PARAGRAPH_GAP_FACTOR = 0.6

#: Minimum left-indent (points) relative to the prevailing left margin
#: that signals a new paragraph. An indented first line is a classic
#: typeset paragraph signal. The prevailing margin is computed from the
#: preceding lines in the current candidate paragraph.
PARAGRAPH_INDENT_PT = 12.0

#: Maximum left-indent (points) for a continuation line relative to the
#: first line's left edge. Continuation lines (hanging indents, lists)
#: may be indented further right without starting a new paragraph.
CONTINUATION_INDENT_PT = 36.0

#: A line whose text length is at most this many characters is a candidate
#: for "indisputably isolated" (page number, footer). Short text length
#: alone does NOT force a paragraph boundary; additional contextual
#: evidence (centering, vertical isolation, page-edge proximity) is
#: required to treat a short line as isolated.
SHORT_LINE_THRESHOLD = 8

#: Fraction of page width that a line must span to be considered
#: "full-width". A centered line spanning less than this fraction is
#: treated as isolated and not merged into body text.
CENTERED_WIDTH_FRACTION = 0.6

#: Horizontal alignment tolerance (points). Two left edges within this
#: distance are considered aligned.
ALIGN_TOLERANCE_PT = 2.0

#: Proximity to the page top or bottom (points) used as supporting evidence
#: that a short line is a page-level element (page number, header) rather
#: than body text. A short line near a page edge is isolated; a short line
#: in the body is not.
PAGE_EDGE_MARGIN_PT = 72.0

#: Vertical overlap tolerance (points) for floating-point noise
#: absorption when comparing line baselines. Matches M2.2's
#: ROW_OVERLAP_TOLERANCE_PT.
LINE_OVERLAP_TOLERANCE_PT = 2.0

#: Regex to detect a line-end hyphen that is likely a wrapping artifact.
#: Matches a hyphen preceded by a letter and not followed by a letter
#: on the same line (i.e. the hyphen is the last non-space character).
HYPHEN_LINE_END_RE = re.compile(r"[A-Za-z]-\s*$")

#: Regex to detect a legitimate compound hyphen (letter on both sides).
COMPOUND_HYPHEN_RE = re.compile(r"[A-Za-z]-[A-Za-z]")

#: Maximum leading spaces to strip from a paragraph's first line.
MAX_LEADING_SPACES_STRIP = 4

#: Whitespace collapse regex (matches any run of whitespace).
RE_SPACE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


class TextSource(StrEnum):
    """Provenance of a reconstructed paragraph's text.

    ``NATIVE`` means the text came from the PDF's native text layer through
    the M2.1 layout path. ``OCR`` means the text came from OCR
    recognition (M3.3/M3.4/M3.5). The two sources are never merged or
    guessed here; every paragraph carries the source it actually came from.
    """

    NATIVE = "native"
    OCR = "ocr"


@dataclass(frozen=True, slots=True)
class ReconstructedParagraph:
    """A logical paragraph reconstructed from physical text lines.

    This is a PDF-specific intermediate representation. It retains enough
    provenance to trace back to the source layout without contaminating the
    format-independent document model with PDF geometry.
    """

    #: The reflowed paragraph text (joined lines, normalized whitespace).
    text: str

    #: The 1-based PDF page number this paragraph originates from.
    page_number: int

    #: The source :class:`~kindle_converter.pdf.layout.TextLine` objects
    #: that were joined to form this paragraph, in M2.2 reading order.
    source_lines: tuple[TextLine, ...]

    #: The source :class:`~kindle_converter.pdf.layout.LayoutBlock` objects
    #: that contributed lines to this paragraph, in M2.2 reading order
    #: (duplicates removed, preserving first occurrence order).
    source_blocks: tuple[LayoutBlock, ...]

    #: The M2.1 extraction order indices of the source lines
    #: (block.order, line.order pairs), in reading order.
    source_line_orders: tuple[tuple[int, int], ...]

    #: The M2.2 reading-order indices of the source lines within the page.
    reading_order_indices: tuple[int, ...]

    #: Whether this paragraph ends at a page boundary (the last line on
    #: its page). Always a paragraph boundary by policy.
    ends_at_page_boundary: bool = False

    #: Provenance of the paragraph text: native PDF text (M2.1 layout) or
    #: OCR-recognized text (M3.3/M3.4/M3.5). Never inferred from content;
    #: always the source the text actually came from. OCR paragraphs carry
    #: empty ``source_lines``/``source_blocks`` provenance.
    source: TextSource = TextSource.NATIVE

    @property
    def line_count(self) -> int:
        return len(self.source_lines)

    @property
    def block_count(self) -> int:
        return len(self.source_blocks)


@dataclass(frozen=True, slots=True)
class ParagraphPage:
    """One page's reconstructed paragraphs in M2.2 reading order."""

    page: LayoutPage
    paragraphs: tuple[ReconstructedParagraph, ...]

    @property
    def page_number(self) -> int:
        return self.page.page_number

    @property
    def paragraph_count(self) -> int:
        return len(self.paragraphs)


@dataclass(frozen=True, slots=True)
class ParagraphLayout:
    """The whole document's reconstructed paragraphs in per-page reading order."""

    pages: tuple[ParagraphPage, ...]

    @property
    def paragraph_count(self) -> int:
        return sum(p.paragraph_count for p in self.pages)


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _collect_lines_in_reading_order(ordered_page: OrderedPage) -> list[TextLine]:
    """Collect all text lines from an OrderedPage in M2.2 reading order.

    Lines are emitted block by block (in OrderedPage.blocks order), and
    within each block in the block's line order (which is the M2.1 line
    order within that block).
    """
    lines: list[TextLine] = []
    for ob in ordered_page.blocks:
        lines.extend(ob.block.lines)
    return lines


def _median_line_height(lines: list[TextLine]) -> float:
    """Return the median height of ``lines`` (1.0 if empty)."""
    if not lines:
        return 1.0
    heights = sorted(line.height for line in lines)
    middle = len(heights) // 2
    if len(heights) % 2 == 1:
        return heights[middle]
    return (heights[middle - 1] + heights[middle]) / 2.0


def _prevailing_left_margin(lines: list[TextLine]) -> float:
    """Compute the prevailing left margin from a list of lines.

    Uses the median of line x0 values (robust against an indented first
    line or an outlier).
    """
    if not lines:
        return 0.0
    x0s = sorted(line.x0 for line in lines)
    middle = len(x0s) // 2
    if len(x0s) % 2 == 1:
        return x0s[middle]
    return (x0s[middle - 1] + x0s[middle]) / 2.0


def _is_centered_line(line: TextLine, page_width: float) -> bool:
    """Return whether ``line`` appears centered on the page.

    A line is centered if its left and right margins are roughly equal
    and it does not span most of the page width.
    """
    left_margin = line.x0
    right_margin = page_width - line.x1
    if line.width >= CENTERED_WIDTH_FRACTION * page_width:
        return False
    return abs(left_margin - right_margin) <= ALIGN_TOLERANCE_PT * 2


def _is_short_line(line: TextLine) -> bool:
    """Return whether ``line`` has short text (a candidate for isolation)."""
    return len(line.text.strip()) <= SHORT_LINE_THRESHOLD


def _lines_aligned(left_a: float, left_b: float) -> bool:
    """Return whether two left edges are aligned within tolerance."""
    return abs(left_a - left_b) <= ALIGN_TOLERANCE_PT


def _vertical_gap(upper: TextLine, lower: TextLine) -> float:
    """Vertical gap from bottom of ``upper`` to top of ``lower`` (>= 0)."""
    return max(0.0, lower.y0 - upper.y1)


def _likely_wrapping_hyphen(upper_text: str, lower_text: str) -> bool:
    """Heuristic: does ``upper_text`` end with a wrapping hyphen?

    Conservative: only true when upper ends with a letter+hyphen and
    lower starts with a lowercase letter (common in English wrapping).
    Does not trigger for ALL-CAPS or mixed-case compounds.
    """
    upper_stripped = upper_text.rstrip()
    lower_stripped = lower_text.lstrip()
    if not (upper_stripped.endswith("-") and lower_stripped):
        return False
    if not HYPHEN_LINE_END_RE.search(upper_stripped):
        return False
    # Lower starts with lowercase -> likely continuation of a word.
    if lower_stripped[0].islower():
        # Additional guard: the joined form should not contain a
        # legitimate compound hyphen pattern.
        joined = upper_stripped.rstrip("-") + lower_stripped
        if COMPOUND_HYPHEN_RE.search(joined):
            return False
        return True
    return False


def _join_texts(texts: list[str]) -> str:
    """Join a sequence of line texts into a single paragraph string.

    Handles:
    * single space between lines
    * conservative dehyphenation at line breaks
    * stripping leading indent (up to 4 spaces) from first line
    * whitespace collapse

    Shared by native M2.3 paragraphs (:func:`_join_lines`) and OCR
    paragraphs (:func:`reconstruct_ocr_paragraphs`) so both sources use the
    identical textual joining rule.
    """
    if not texts:
        return ""

    parts: list[str] = []
    separators: list[str] = []  # Separator before each part (default " ")
    for i, text in enumerate(texts):
        if i == 0:
            # Strip leading paragraph indent (up to 4 spaces).
            text = re.sub(r"^ {1,4}", "", text)
        separators.append(" ")
        if i > 0:
            prev_text = parts[-1]
            # Conservative dehyphenation: if the previous line ended with a
            # wrapping hyphen and this line continues the word, join directly
            # (no space) and strip the hyphen.
            if _likely_wrapping_hyphen(prev_text, text):
                prev_stripped = prev_text.rstrip().rstrip("-")
                parts[-1] = prev_stripped
                text = text.lstrip()
                separators[-1] = ""  # No space after dehyphenation
        parts.append(text)

    # Join parts with their separators.
    result = parts[0]
    for i in range(1, len(parts)):
        result += separators[i] + parts[i]
    return RE_SPACE.sub(" ", result).strip()


def _join_lines(lines: list[TextLine]) -> str:
    """Join a sequence of lines into a single paragraph string."""
    return _join_texts([line.text for line in lines])


def _is_short_line_isolated(
    upper: TextLine,
    lower: TextLine,
    gap_threshold: float,
    page_width: float,
    page_height: float,
) -> bool:
    """Return whether a short ``lower`` line should be isolated.

    Short text length alone is not sufficient: a short body line like
    "It was." must stay in its paragraph. A short line is only treated as
    isolated when combined with additional geometric evidence:

    * centered positioning (a lone centered short line is typically a
      title or page number), or
    * significant vertical isolation (a gap larger than the paragraph
      threshold), or
    * proximity to the page top or bottom edge (within
      :data:`PAGE_EDGE_MARGIN_PT`).
    """
    if not _is_short_line(lower):
        return False
    if _is_centered_line(lower, page_width):
        return True
    if _vertical_gap(upper, lower) > gap_threshold:
        return True
    if lower.y0 < PAGE_EDGE_MARGIN_PT:
        return True
    if page_height - lower.y1 < PAGE_EDGE_MARGIN_PT:
        return True
    return False


def _same_paragraph(
    upper: TextLine,
    lower: TextLine,
    gap_threshold: float,
    prevailing_margin: float,
    page_width: float,
    page_height: float,
    first_line_x0: float | None,
    in_centered_run: bool,
) -> bool:
    """Decide whether ``lower`` continues the paragraph started by ``upper``.

    Returns True if they belong to the same paragraph, False if a boundary
    is detected. The decision uses multiple signals; any signal that
    strongly indicates a boundary returns False.
    """
    # 1. Short line isolation (contextual: requires additional evidence).
    if _is_short_line_isolated(upper, lower, gap_threshold, page_width, page_height):
        return False

    # 2. Large vertical gap.
    gap = _vertical_gap(upper, lower)
    if gap > gap_threshold:
        return False

    # 3. Centered line isolation.
    lower_centered = _is_centered_line(lower, page_width)
    if lower_centered and not in_centered_run:
        # A centered line after non-centered content starts a new paragraph.
        return False
    if in_centered_run and not lower_centered:
        # Body text after centered content starts a new paragraph.
        return False

    # 4. First-line indentation relative to prevailing margin.
    # If this is the first line after a boundary (first_line_x0 is None),
    # we don't apply the indent check here; the caller handles paragraph
    # start logic. But if we're already in a paragraph, an indented line
    # relative to the prevailing margin signals a new paragraph.
    if first_line_x0 is not None:
        indent = lower.x0 - prevailing_margin
        if indent >= PARAGRAPH_INDENT_PT:
            return False

        # 5. Continuation indent: if the first line was indented and this
        # line returns to the margin, that's a new paragraph signal.
        if first_line_x0 - prevailing_margin >= PARAGRAPH_INDENT_PT:
            # First line was indented; if this line aligns with the margin,
            # it's likely a new paragraph (reverse indent pattern).
            if _lines_aligned(lower.x0, prevailing_margin):
                return False

        # 6. Excessive continuation indent (beyond normal hanging indent).
        if lower.x0 - first_line_x0 > CONTINUATION_INDENT_PT:
            return False

    return True


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def reconstruct_page_paragraphs(ordered_page: OrderedPage) -> ParagraphPage:
    """Reconstruct paragraphs for a single page from M2.2 reading order.

    Parameters
    ----------
    ordered_page:
        An :class:`OrderedPage` from :func:`reconstruct_read_order`. The
        block order is the authoritative reading order; this function does
        not re-sort.

    Returns
    -------
    ParagraphPage
        The page's paragraphs in reading order, with full provenance.
    """
    lines = _collect_lines_in_reading_order(ordered_page)
    if not lines:
        return ParagraphPage(page=ordered_page.page, paragraphs=())

    page_width = ordered_page.page_width
    page_height = ordered_page.page_height
    median_height = _median_line_height(lines)
    gap_threshold = PARAGRAPH_GAP_FACTOR * median_height

    paragraphs: list[ReconstructedParagraph] = []
    current_lines: list[TextLine] = []
    current_blocks: list[LayoutBlock] = []
    current_line_orders: list[tuple[int, int]] = []
    current_reading_indices: list[int] = []
    first_line_x0: float | None = None
    in_centered_run = False

    # Map each line to its source block and reading-order index.
    line_to_block: dict[TextLine, LayoutBlock] = {}
    line_to_reading_idx: dict[TextLine, int] = {}
    reading_idx = 0
    for ob in ordered_page.blocks:
        for line in ob.block.lines:
            line_to_block[line] = ob.block
            line_to_reading_idx[line] = reading_idx
            reading_idx += 1

    for line in lines:
        if not current_lines:
            # Starting a new paragraph.
            current_lines.append(line)
            block = line_to_block[line]
            if block not in current_blocks:
                current_blocks.append(block)
            current_line_orders.append((block.order, line.order))
            current_reading_indices.append(line_to_reading_idx[line])
            first_line_x0 = line.x0
            in_centered_run = _is_centered_line(line, page_width)
            continue

        # Decide whether to continue or break.
        prevailing_margin = _prevailing_left_margin(current_lines)
        if _same_paragraph(
            current_lines[-1],
            line,
            gap_threshold,
            prevailing_margin,
            page_width,
            page_height,
            first_line_x0,
            in_centered_run,
        ):
            # Continue current paragraph.
            current_lines.append(line)
            block = line_to_block[line]
            if block not in current_blocks:
                current_blocks.append(block)
            current_line_orders.append((block.order, line.order))
            current_reading_indices.append(line_to_reading_idx[line])
            # Update centered run state.
            if _is_centered_line(line, page_width):
                in_centered_run = True
        else:
            # Finalize current paragraph.
            text = _join_lines(current_lines)
            paragraphs.append(
                ReconstructedParagraph(
                    text=text,
                    page_number=ordered_page.page_number,
                    source_lines=tuple(current_lines),
                    source_blocks=tuple(current_blocks),
                    source_line_orders=tuple(current_line_orders),
                    reading_order_indices=tuple(current_reading_indices),
                    ends_at_page_boundary=False,
                )
            )
            # Start new paragraph.
            current_lines = [line]
            block = line_to_block[line]
            current_blocks = [block]
            current_line_orders = [(block.order, line.order)]
            current_reading_indices = [line_to_reading_idx[line]]
            first_line_x0 = line.x0
            in_centered_run = _is_centered_line(line, page_width)

    # Finalize last paragraph on the page.
    if current_lines:
        text = _join_lines(current_lines)
        paragraphs.append(
            ReconstructedParagraph(
                text=text,
                page_number=ordered_page.page_number,
                source_lines=tuple(current_lines),
                source_blocks=tuple(current_blocks),
                source_line_orders=tuple(current_line_orders),
                reading_order_indices=tuple(current_reading_indices),
                ends_at_page_boundary=True,  # Page boundary = paragraph boundary
            )
        )

    return ParagraphPage(page=ordered_page.page, paragraphs=tuple(paragraphs))


def reconstruct_paragraphs(
    source: OrderedPage | OrderedLayout,
) -> ParagraphPage | ParagraphLayout:
    """Reconstruct paragraphs from M2.2 reading order.

    Parameters
    ----------
    source:
        Either an :class:`OrderedPage` (single page) or an
        :class:`OrderedLayout` (whole document) from
        :func:`~kindle_converter.pdf.reading_order.reconstruct_read_order`.

    Returns
    -------
    ParagraphPage | ParagraphLayout
        The reconstructed paragraphs preserving the M2.2 reading order.
        The source objects are never mutated; the returned views reference
        the original M2.1 layout objects.
    """
    if isinstance(source, OrderedLayout):
        pages = tuple(reconstruct_page_paragraphs(p) for p in source.pages)
        return ParagraphLayout(pages=pages)
    return reconstruct_page_paragraphs(source)


def reconstruct_ocr_paragraphs(
    text: str, page_number: int
) -> tuple[ReconstructedParagraph, ...]:
    """Reconstruct paragraphs from cleaned OCR text (Milestone 3.6).

    OCR output carries no layout geometry, so the only deterministic
    boundary signal available is the line structure preserved by the M3.4
    cleanup layer: a blank line (or a run of blank lines) between non-empty
    lines is a paragraph boundary. Consecutive non-blank lines form one
    paragraph and are joined with the same conservative single-space /
    dehyphenation rule as native M2.3 paragraphs
    (:func:`reconstruct_page_paragraphs`), so OCR text is never flattened
    to a single unstructured run of words.

    The paragraphs are returned in page order and carry
    ``TextSource.OCR`` with **empty provenance**: OCR recognition provides
    no bbox, font, block, line-order, or reading-order metadata, and none
    is fabricated here (``source_lines``/``source_blocks``/
    ``source_line_orders``/``reading_order_indices`` are all empty).
    Domain punctuation and wording are preserved exactly as OCR produced
    them; nothing is de-duplicated, merged, or semantically rewritten.

    Parameters
    ----------
    text:
        The cleaned OCR text for one page, with its line structure
        preserved (typically ``CleanedOCRResult.text``).
    page_number:
        The 1-based PDF page number, carried unchanged into every
        reconstructed paragraph.

    Returns
    -------
    tuple[ReconstructedParagraph, ...]
        One paragraph per blank-line-separated group of non-empty lines.
        Whitespace-only paragraphs are never emitted; a page with no
        non-empty lines returns an empty tuple.

    Raises
    ------
    TypeError
        If ``text`` is not a ``str``.
    ValueError
        If ``page_number`` is not a positive integer.
    """
    if not isinstance(text, str):
        raise TypeError(
            "text must be a str (cleaned OCR text for one page), got "
            f"{type(text).__name__ if text is not None else 'None'}"
        )
    if isinstance(page_number, bool) or not isinstance(page_number, int):
        raise ValueError(
            f"page_number must be an int, got {type(page_number).__name__}"
        )
    if page_number < 1:
        raise ValueError(f"page_number must be >= 1, got {page_number}")

    paragraphs: list[ReconstructedParagraph] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line)
            continue
        if current:
            _append_ocr_paragraph(paragraphs, current, page_number)
            current = []
    if current:
        _append_ocr_paragraph(paragraphs, current, page_number)
    return tuple(paragraphs)


def _append_ocr_paragraph(
    paragraphs: list[ReconstructedParagraph],
    lines: list[str],
    page_number: int,
) -> None:
    """Append one OCR paragraph for ``lines`` (skipping whitespace-only)."""
    joined = _join_texts(lines)
    if not joined.strip():
        return
    paragraphs.append(
        ReconstructedParagraph(
            text=joined,
            page_number=page_number,
            source_lines=(),
            source_blocks=(),
            source_line_orders=(),
            reading_order_indices=(),
            ends_at_page_boundary=False,
            source=TextSource.OCR,
        )
    )