"""Conservative deterministic chapter detection (Milestone 2.9).

This module consumes the M2.7 integrated :class:`~kindle_converter.pdf.reconstruction.ReconstructedDocument`
and classifies which detected headings represent actual chapter boundaries.

The detector is deliberately conservative: it only promotes headings to chapters when
there is sufficient structural evidence, using document-level pattern analysis rather
than treating headings independently.

It does NOT:
* Re-extract PDF text or re-sort blocks
* Duplicate paragraph reconstruction or heading detection
* Mutate the source ReconstructedDocument
* Introduce ML or external dependencies
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from statistics import median
from typing import TYPE_CHECKING

from .headings import DetectedHeading
from .layout import FontFlags
from .paragraphs import ReconstructedParagraph

if TYPE_CHECKING:
    from .reconstruction import ElementKind, ReconstructedDocument, ReconstructedElement


# --------------------------------------------------------------------------- #
# Constants (centralized, deterministic, documented)
# --------------------------------------------------------------------------- #

# Minimum font-size ratio between candidate title-only chapters for consistency.
TITLE_ONLY_FONT_SIZE_RATIO_THRESHOLD = 1.1

# Minimum number of title-only headings needed for structural evidence.
MIN_TITLE_ONLY_HEADINGS = 2

# Maximum font-size variation ratio among title-only chapters for consistency.
TITLE_ONLY_MAX_FONT_SIZE_VARIATION = 1.2

# Front-matter / back-matter terms that should not be auto-classified as chapters.
FRONT_MATTER_TERMS = (
    "contents",
    "table of contents",
    "preface",
    "foreword",
    "introduction",
    "acknowledgements",
    "acknowledgments",
    "bibliography",
    "references",
    "appendix",
    "appendices",
    "index",
    "about the author",
    "colophon",
)

# Keywords that indicate a chapter/part boundary when followed by a number.
CHAPTER_KEYWORDS = ("chapter", "part")

# Word-to-number mapping for written English numbers.
_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "thousand": 1000,
}

# Roman numeral regex (case-insensitive, valid Roman numerals up to 3999).
_ROMAN_RE = re.compile(
    r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$",
    re.IGNORECASE,
)

# Arabic numeral regex (positive integers).
_ARABIC_RE = re.compile(r"^\d+$")

# Chapter pattern: "Chapter 1", "Part I", "CHAPTER ONE", etc.
# Matches keyword followed by optional separator and number (arabic/roman/word).
_CHAPTER_PATTERN_RE = re.compile(
    r"^(chapter|part)\s*[.:-]?\s*(.+)$",
    re.IGNORECASE,
)

# Number-only pattern: bare numbers that could be chapter numbers.
_NUMBER_ONLY_RE = re.compile(r"^\s*(.+?)\s*$")


# --------------------------------------------------------------------------- #
# Public result models
# --------------------------------------------------------------------------- #


class ChapterNumberType(StrEnum):
    """Type of chapter numbering detected."""

    ARABIC = "arabic"
    ROMAN = "roman"
    WORD = "word"


@dataclass(frozen=True, slots=True)
class DetectedChapter:
    """A detected chapter boundary with full provenance."""

    #: 1-based chapter order/index in the document.
    order: int

    #: The chapter heading text (preserved original).
    text: str

    #: Extracted chapter number string if detected (e.g. "1", "I", "One").
    number: str | None

    #: Type of the extracted number.
    number_type: ChapterNumberType | None

    #: 1-based page number where the chapter starts.
    page_number: int

    #: Source paragraph (M2.3 provenance) that starts this chapter.
    paragraph: ReconstructedParagraph

    #: The original heading detection result (M2.4 provenance).
    heading: DetectedHeading

    #: Reasons this heading was classified as a chapter.
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChapterDetectionResult:
    """Complete chapter detection result for a document."""

    #: Detected chapters in document order.
    chapters: tuple[DetectedChapter, ...]

    #: All heading candidates considered (in document order).
    candidates: tuple[DetectedHeading, ...]

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)


# --------------------------------------------------------------------------- #
# Internal analysis structures
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _HeadingAnalysis:
    """Internal analysis of one heading candidate."""

    element: "ReconstructedElement"
    index: int
    text: str
    norm_text: str
    page_number: int
    paragraph: ReconstructedParagraph
    heading: DetectedHeading
    median_font_size: float | None
    is_bold: bool
    is_centered: bool
    is_front_matter: bool
    chapter_keyword: str | None
    parsed_number: str | None
    parsed_number_type: ChapterNumberType | None
    parsed_number_value: int | None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: NFKC, collapse whitespace, casefold."""
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def _median_font_size(paragraph: ReconstructedParagraph) -> float | None:
    """Compute median font size from paragraph's source spans."""
    sizes: list[float] = []
    for line in paragraph.source_lines:
        for span in line.spans:
            if span.font_size is not None and span.font_size > 0:
                sizes.append(float(span.font_size))
    if not sizes:
        return None
    return float(median(sizes))


def _is_bold_paragraph(paragraph: ReconstructedParagraph) -> bool:
    """Check if paragraph has confirmed bold typography."""
    for line in paragraph.source_lines:
        for span in line.spans:
            flags = span.font_flags
            if flags & FontFlags.BOLD:
                name = span.font_name or ""
                if any(token in name.lower() for token in ("bold", "black", "heavy", "demibold")):
                    return True
    return False


def _parse_written_number(text: str) -> int | None:
    """Parse written English number (e.g. 'twenty one' -> 21)."""
    words = text.lower().split()
    if not words:
        return None

    # Handle compound numbers like "twenty one", "one hundred", etc.
    total = 0
    current = 0
    for word in words:
        if word in ("and",):
            continue
        if word in _WORD_NUMBERS:
            value = _WORD_NUMBERS[word]
            if value >= 100:
                if current == 0:
                    current = 1
                current *= value
                if value >= 1000:
                    total += current
                    current = 0
            else:
                current += value
        elif "-" in word:
            parts = word.split("-")
            for part in parts:
                if part in _WORD_NUMBERS:
                    current += _WORD_NUMBERS[part]
        else:
            return None
    total += current
    return total if total > 0 else None


def _parse_roman(text: str) -> int | None:
    """Parse Roman numeral to integer (case-insensitive)."""
    if not _ROMAN_RE.match(text):
        return None
    roman_map = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    text = text.upper()
    total = 0
    prev = 0
    for ch in reversed(text):
        value = roman_map[ch]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    return total


def _extract_number(text: str) -> tuple[str | None, ChapterNumberType | None, int | None]:
    """Extract number from text, returning (number_str, number_type, value)."""
    # Try arabic first
    if _ARABIC_RE.match(text):
        val = int(text)
        return (text, ChapterNumberType.ARABIC, val)

    # Try Roman
    roman_val = _parse_roman(text)
    if roman_val is not None:
        return (text, ChapterNumberType.ROMAN, roman_val)

    # Try written English
    word_val = _parse_written_number(text)
    if word_val is not None:
        return (text, ChapterNumberType.WORD, word_val)

    return (None, None, None)


def _parse_chapter_heading(text: str) -> tuple[str | None, str | None, ChapterNumberType | None, int | None]:
    """Parse a heading for chapter keyword + number.
    
    Returns: (keyword, number_str, number_type, number_value) or (None, None, None, None)
    """
    norm = _normalize_text(text)
    match = _CHAPTER_PATTERN_RE.match(norm)
    if not match:
        return (None, None, None, None)

    keyword = match.group(1).lower()
    # Extract number from original text to preserve casing
    orig_match = re.match(r"^(chapter|part)\s*[.:-]?\s*(.+)$", text, re.IGNORECASE)
    if orig_match:
        orig_remainder = orig_match.group(2).strip()
    else:
        orig_remainder = match.group(2).strip()

    if not orig_remainder:
        return (keyword, None, None, None)

    # Extract just the number part from the beginning of the remainder
    # Handle "1: The Beginning", "I. Title", "One: Introduction", etc.
    num_str, num_type, num_val = _extract_number(orig_remainder)
    if num_str is None:
        # Try matching just the leading number/roman/word part before any separator
        for pattern, ntype in [
            (r"^\d+", ChapterNumberType.ARABIC),
            (r"^[IVXLCDM]+$", ChapterNumberType.ROMAN),
        ]:
            m = re.match(pattern, orig_remainder, re.IGNORECASE)
            if m:
                num_text = m.group(0)
                parsed = _extract_number(num_text)
                if parsed[1] is not None:
                    num_str = num_text
                    num_type = ntype
                    num_val = parsed[2]
                    break
        # Also try written number at the start
        if num_str is None:
            words = orig_remainder.split()
            if words and words[0].lower() in _WORD_NUMBERS:
                parsed = _extract_number(words[0])
                if parsed[1] is not None:
                    num_str = words[0]
                    num_type = ChapterNumberType.WORD
                    num_val = parsed[2]
    return (keyword, num_str, num_type, num_val)


def _is_front_matter(text: str) -> bool:
    """Check if text matches a known front/back matter term."""
    norm = _normalize_text(text)
    return norm in FRONT_MATTER_TERMS


def _parse_number_only(text: str) -> tuple[str | None, ChapterNumberType | None, int | None]:
    """Parse a bare number heading (no chapter keyword).
    
    Returns (number_str, number_type, number_value) or (None, None, None).
    """
    norm = _normalize_text(text)
    # Try arabic
    if _ARABIC_RE.match(norm):
        val = int(norm)
        return (norm, ChapterNumberType.ARABIC, val)
    # Try Roman
    roman_val = _parse_roman(norm)
    if roman_val is not None:
        return (norm, ChapterNumberType.ROMAN, roman_val)
    # Try written English
    word_val = _parse_written_number(norm)
    if word_val is not None:
        return (norm, ChapterNumberType.WORD, word_val)
    return (None, None, None)


def _analyze_headings(document: "ReconstructedDocument") -> tuple[_HeadingAnalysis, ...]:
    """Analyze all body headings in the document."""
    from .reconstruction import ElementKind

    analyses: list[_HeadingAnalysis] = []
    for idx, element in enumerate(document.elements):
        if element.kind is not ElementKind.HEADING:
            continue
        if element.heading is None:
            continue

        para = element.paragraph
        page_width = 595.0  # default; could get from layout if needed
        # Try to get actual page width from source blocks
        for block in para.source_blocks:
            if block.bbox[2] > page_width:
                page_width = block.bbox[2]
                break

        heading = element.heading
        text = element.text
        norm = _normalize_text(text)
        font_size = _median_font_size(para)
        is_bold = _is_bold_paragraph(para)
        # centered detection from heading reasons
        is_centered = "centered_alignment" in heading.reasons

        is_fm = _is_front_matter(text)
        keyword, num_str, num_type, num_val = _parse_chapter_heading(text)
        # If no chapter keyword, try parsing as a bare number heading
        if keyword is None:
            num_str, num_type, num_val = _parse_number_only(text)

        analyses.append(
            _HeadingAnalysis(
                element=element,
                index=idx,
                text=text,
                norm_text=norm,
                page_number=element.page_number,
                paragraph=para,
                heading=heading,
                median_font_size=font_size,
                is_bold=is_bold,
                is_centered=is_centered,
                is_front_matter=is_fm,
                chapter_keyword=keyword,
                parsed_number=num_str,
                parsed_number_type=num_type,
                parsed_number_value=num_val,
            )
        )
    return tuple(analyses)


# --------------------------------------------------------------------------- #
# Chapter classification
# --------------------------------------------------------------------------- #


def _classify_explicit_chapters(analyses: tuple[_HeadingAnalysis, ...]) -> list[int]:
    """Classify explicit chapter-pattern headings (keyword + number).
    
    Returns indices of analyses that are explicit chapters.
    """
    explicit_chapters: list[int] = []
    numbered_chapters: list[tuple[int, int]] = []  # (analysis_idx, number_value)

    for i, a in enumerate(analyses):
        if a.is_front_matter:
            continue
        if a.chapter_keyword is not None and a.parsed_number_value is not None:
            numbered_chapters.append((i, a.parsed_number_value))

    # Check if numbered chapters form a monotonic increasing sequence
    if len(numbered_chapters) >= 2:
        values = [v for _, v in numbered_chapters]
        # Allow non-decreasing (handles "Chapter 1", "Chapter 1" duplicate)
        is_monotonic = all(values[i] <= values[i + 1] for i in range(len(values) - 1))
        # Require at least one strict increase
        has_increase = any(values[i] < values[i + 1] for i in range(len(values) - 1))
        if is_monotonic and has_increase:
            # Strong sequence evidence: all matching keyword+number in monotonic sequence are chapters
            for i, _ in numbered_chapters:
                explicit_chapters.append(i)
            return explicit_chapters

    # If no strong sequence, fall back: any single keyword+number is a chapter
    # (conservative: requires explicit "Chapter N" text)
    for i, a in enumerate(analyses):
        if a.is_front_matter:
            continue
        if a.chapter_keyword is not None and a.parsed_number_value is not None:
            explicit_chapters.append(i)

    return explicit_chapters


def _classify_number_sequence_chapters(analyses: tuple[_HeadingAnalysis, ...]) -> list[int]:
    """Classify number-only headings that form a monotonic sequence.
    
    Returns indices of analyses that are number-sequence chapters.
    """
    number_chapters: list[int] = []
    number_candidates: list[tuple[int, int, ChapterNumberType]] = []  # (idx, value, type)

    for i, a in enumerate(analyses):
        if a.is_front_matter:
            continue
        if a.chapter_keyword is not None:
            continue  # handled by explicit chapter path
        # Check if heading text is primarily a number
        if a.parsed_number is not None:
            # The entire norm_text should be the number (or number + punctuation)
            # Allow "1." or "I." etc. by checking if norm_text starts with number
            if a.norm_text.startswith(a.parsed_number):
                number_candidates.append((i, a.parsed_number_value, a.parsed_number_type))

    if len(number_candidates) < MIN_TITLE_ONLY_HEADINGS:
        return number_chapters

    # Group by number type (arabic/roman/word) - don't mix types
    by_type: dict[ChapterNumberType, list[tuple[int, int]]] = {}
    for idx, val, ntype in number_candidates:
        if val is None:
            continue
        by_type.setdefault(ntype, []).append((idx, val))

    for ntype, candidates in by_type.items():
        if len(candidates) < MIN_TITLE_ONLY_HEADINGS:
            continue
        values = [v for _, v in candidates]
        is_monotonic = all(values[i] <= values[i + 1] for i in range(len(values) - 1))
        has_increase = any(values[i] < values[i + 1] for i in range(len(values) - 1))
        if is_monotonic and has_increase:
            for idx, _ in candidates:
                number_chapters.append(idx)

    return number_chapters


def _classify_title_only_chapters(analyses: tuple[_HeadingAnalysis, ...], explicit_indices: set[int], number_seq_indices: set[int]) -> list[int]:
    """Classify title-only chapters (no keyword, no number) with structural evidence.
    
    Returns indices of analyses that are title-only chapters.
    """
    title_only_chapters: list[int] = []

    # Find candidates that are not already classified and have no keyword/number
    candidates = [
        (i, a) for i, a in enumerate(analyses)
        if i not in explicit_indices and i not in number_seq_indices
        and not a.is_front_matter
        and a.chapter_keyword is None
        and a.parsed_number is None
    ]

    if len(candidates) < MIN_TITLE_ONLY_HEADINGS:
        return title_only_chapters

    # Check typography consistency: font size, bold, centered
    font_sizes = [a.median_font_size for _, a in candidates if a.median_font_size is not None]
    if not font_sizes:
        return title_only_chapters

    min_size = min(font_sizes)
    max_size = max(font_sizes)
    if max_size / min_size > TITLE_ONLY_MAX_FONT_SIZE_VARIATION:
        return title_only_chapters  # Too much variation

    # Require consistent typography signal: all bold, or all centered,
    # or very tight font-size consistency (handles mixed styling)
    bold_count = sum(1 for _, a in candidates if a.is_bold)
    centered_count = sum(1 for _, a in candidates if a.is_centered)
    consistent_bold = bold_count == len(candidates)
    consistent_centered = centered_count == len(candidates)
    tight_font = (max_size - min_size) / min_size <= 0.15 if min_size > 0 else False
    if not (consistent_bold or consistent_centered or tight_font):
        return title_only_chapters

    # Check they're separated by body content (not adjacent)
    # In the document.elements order, chapters should have paragraphs between them
    separated = True
    for j in range(len(candidates) - 1):
        idx1 = candidates[j][1].index
        idx2 = candidates[j + 1][1].index
        if idx2 == idx1 + 1:
            separated = False
            break

    if not separated:
        return title_only_chapters

    # All checks passed: these are title-only chapters
    for i, _ in candidates:
        title_only_chapters.append(i)

    return title_only_chapters


def _build_detected_chapter(
    a: _HeadingAnalysis,
    order: int,
    reasons: tuple[str, ...]
) -> DetectedChapter:
    """Build a DetectedChapter from analysis."""
    return DetectedChapter(
        order=order,
        text=a.text,
        number=a.parsed_number,
        number_type=a.parsed_number_type,
        page_number=a.page_number,
        paragraph=a.paragraph,
        heading=a.heading,
        reasons=reasons,
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def detect_chapters(source: "ReconstructedDocument") -> ChapterDetectionResult:
    """Detect chapter boundaries in a reconstructed document.

    Consumes the M2.7 integrated ReconstructedDocument (with heading detection
    and header/footer filtering already applied) and determines which headings
    represent actual chapter starts.

    Parameters
    ----------
    source:
        A ReconstructedDocument from :func:`~kindle_converter.pdf.reconstruction.reconstruct_layout`
        or :func:`~kindle_converter.pdf.reconstruction.build_reconstructed_document`.

    Returns
    -------
    ChapterDetectionResult
        Contains detected chapters with full provenance and all candidates considered.

    Raises
    ------
    TypeError
        If source is not a ReconstructedDocument.
    """
    from .reconstruction import ReconstructedDocument

    if not isinstance(source, ReconstructedDocument):
        raise TypeError(
            f"detect_chapters expects a ReconstructedDocument, "
            f"got {type(source).__name__}"
        )

    # Analyze all body headings
    analyses = _analyze_headings(source)

    if not analyses:
        return ChapterDetectionResult(chapters=(), candidates=tuple(a.heading for a in analyses))

    # Run classification passes (conservative: explicit > number sequence > title-only)
    explicit_indices = set(_classify_explicit_chapters(analyses))
    number_seq_indices = set(_classify_number_sequence_chapters(analyses))
    title_only_indices = set(_classify_title_only_chapters(analyses, explicit_indices, number_seq_indices))

    # Combine in document order
    all_chapter_indices = sorted(explicit_indices | number_seq_indices | title_only_indices)

    chapters: list[DetectedChapter] = []
    for order, idx in enumerate(all_chapter_indices, start=1):
        a = analyses[idx]
        reasons: list[str] = []

        if idx in explicit_indices:
            reasons.append("explicit_chapter_pattern")
            if a.chapter_keyword:
                reasons.append(f"keyword_{a.chapter_keyword}")
            if a.parsed_number_type:
                reasons.append(f"number_{a.parsed_number_type.value}")

        if idx in number_seq_indices:
            reasons.append("number_sequence")
            if a.parsed_number_type:
                reasons.append(f"number_{a.parsed_number_type.value}")

        if idx in title_only_indices:
            reasons.append("title_only_structural")

        chapters.append(_build_detected_chapter(a, order, tuple(reasons)))

    return ChapterDetectionResult(
        chapters=tuple(chapters),
        candidates=tuple(a.heading for a in analyses),
    )


# --------------------------------------------------------------------------- #
# Convenience: simple chapter detection from ReconstructedDocument
# --------------------------------------------------------------------------- #


def detect_chapters_simple(source: "ReconstructedDocument") -> tuple[DetectedChapter, ...]:
    """Simplified API returning only the detected chapters tuple."""
    return detect_chapters(source).chapters