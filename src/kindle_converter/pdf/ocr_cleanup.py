"""OCR text cleanup (Milestone 3.4).

This module consumes the M3.3 output -- :class:`OCRResult` values -- and
applies a **conservative, deterministic** text cleanup pass over the raw
recognized text. It is the ``OCRResult -> CleanedOCRResult`` boundary of the
M3.3/M3.4 pipeline:

    ocr.ocr_page(...)   or   ocr.ocr_pages(...)
              |
              v
         OCRResult                    (M3.3: raw text, no cleanup)
              |
              v
    ocr_cleanup.clean_ocr_result(...)
    ocr_cleanup.clean_ocr_pages(...)
              |
              v
       CleanedOCRResult               (M3.4: conservative cleanup)

Deliberate scope (M3.4 ends at ``raw OCR text -> cleaned OCR text``):

* **Strictly post-OCR**: this module never renders PDFs, never reopens a
  document, and never calls an OCR engine. It only transforms already
  recognized text strings.
* **No structural reconstruction**: no paragraphs, headings, lists,
  tables, chapters, or EPUB structure. Cleanup output stays a text result
  for later milestones (M3.5+) to consume.
* **Cleanup, not correction**: this module removes or normalizes
  predictable OCR artifacts with high confidence. It does **not** guess
  what the source document "probably said": no spelling correction, no
  dictionary correction, no OCR character substitution (``0 -> O``,
  ``1 -> I``, ``rn -> m``, ...), no grammar correction, no semantic
  rewriting, and no language-model/LLM correction.
* **Conservative whitespace policy**: line structure is preserved. The
  clean layer never collapses the whole document to a single run of words;
  internal spacing and indentation are kept. Only trailing line whitespace,
  pathological blank-line runs, and document-edge whitespace are touched.
* **Deferred concerns**: hyphenation repair, ligature substitution,
  repeated-header/footer removal, and page-number removal all require
  layout/document context and are intentionally **not** implemented here.

The pipeline is fully deterministic and idempotent: the same input always
yields the same output, and ``clean(clean(text)) == clean(text)``.

Transformation order (documented; the complete pipeline is idempotent):

1. validate input (``text`` must be ``str``; see :func:`clean_ocr_text`)
2. Unicode NFC normalization (canonical only, no NFKC compatibility step)
3. newline normalization (``\\r\\n`` and ``\\r`` -> ``\\n``)
4. removal of invalid control characters (C0/C1/DEL, keeping ``\\n``/``\\t``)
5. removal of trailing spaces/tabs on every line
6. collapse of pathological blank-line runs (see
   ``MAX_CONSECUTIVE_BLANK_LINES``)
7. removal of unnecessary document-edge whitespace
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .ocr import OCRResult

# --------------------------------------------------------------------------- #
# Cleanup policy constants
# --------------------------------------------------------------------------- #

#: Maximum number of consecutive blank lines kept in cleaned OCR text.
#: Pathological runs of blank lines (a common OCR artifact) are collapsed to
#: this stable maximum, while ordinary paragraph separation (one or two blank
#: lines) is preserved.
MAX_CONSECUTIVE_BLANK_LINES = 2

#: Control characters that are meaningful whitespace and are never removed by
#: the control-character pass. Every other ``Cc``-category character (NUL,
#: escape, DEL, C1 range, ...) is removed as an OCR artifact.
_CONTROL_CHARACTER_EXCEPTIONS = frozenset({"\n", "\t"})

#: Characters removed from the end of a line by the trailing-whitespace pass.
#: Deliberately limited to space and tab so that meaningful typographic
#: whitespace elsewhere is never touched.
_TRAILING_LINE_WHITESPACE = " \t"


# --------------------------------------------------------------------------- #
# Result representation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CleanedOCRResult:
    """Deterministically cleaned OCR text for one rendered page.

    ``page_number`` is the 1-based physical PDF page index carried over
    unchanged from the source :class:`~kindle_converter.pdf.ocr.OCRResult`.
    ``text`` is the cleaned text produced by :func:`clean_ocr_text`; the
    cleanup layer never mutates the source ``OCRResult``.
    """

    page_number: int
    text: str


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def clean_ocr_text(text: object) -> str:
    """Clean one raw OCR text string (deterministic, idempotent).

    Applies the fixed M3.4 pipeline in order: input validation, Unicode NFC
    normalization, newline normalization, control-character removal, trailing
    line-whitespace removal, blank-line-run collapse, and document-edge
    whitespace removal. The returned text preserves line structure and
    internal spacing/indentation.

    Parameters
    ----------
    text:
        The raw text of an :class:`OCRResult` (or any string to clean).

    Returns
    -------
    str
        The cleaned text.

    Raises
    ------
    TypeError
        If ``text`` is not a ``str`` (including ``None``).

    Examples
    --------
    >>> clean_ocr_text("hello\\r\\nworld \\r\\n\\r\\n\\r\\n")
    'hello\\nworld'
    """
    return _clean_text(text)


def clean_ocr_result(result: OCRResult) -> CleanedOCRResult:
    """Clean one :class:`OCRResult`, preserving its page number.

    The source :class:`OCRResult` is never mutated; a new immutable
    :class:`CleanedOCRResult` is returned with the 1-based page number carried
    over unchanged and the text passed through :func:`clean_ocr_text`.

    Parameters
    ----------
    result:
        An :class:`~kindle_converter.pdf.ocr.OCRResult` produced by M3.3.

    Returns
    -------
    CleanedOCRResult
        The cleaned result for the same page.

    Raises
    ------
    TypeError
        If ``result`` is not an ``OCRResult``.
    ValueError
        If the result's page number is not a valid 1-based int according to
        the project's page-number convention (``int`` >= 1).
    """
    _validate_result(result)
    return CleanedOCRResult(
        page_number=result.page_number,
        text=_clean_text(result.text),
    )


def clean_ocr_pages(
    results: Iterable[OCRResult],
) -> Iterator[CleanedOCRResult]:
    """Clean every OCR result, in order, one page at a time.

    This is the multi-page convenience layer over :func:`clean_ocr_result`,
    designed to be fed directly from :func:`ocr_pages`:

        for cleaned in clean_ocr_pages(ocr_pages(render_pages(source), engine)):
            ...

    Each result is cleaned independently; page order and page numbers are
    preserved, and page boundaries are never merged. Results are produced
    lazily as a generator, matching the M3.2/M3.3 lazy streaming design.
    Failures are never silently skipped: a bad element raises with its own
    context and stops iteration.

    Parameters
    ----------
    results:
        An iterable of :class:`OCRResult` (typically the output of
        ``ocr_pages``).

    Returns
    -------
    Iterator[CleanedOCRResult]
        One cleaned result per input result, in input order.

    Raises
    ------
    TypeError
        If ``results`` is not an iterable of ``OCRResult``.
    ValueError
        If a result's page number is not a valid 1-based int.
    """
    if isinstance(results, (str, bytes)) or not hasattr(results, "__iter__"):
        raise TypeError(
            "results must be an iterable of OCRResult, got "
            f"{type(results).__name__ if results is not None else 'None'}"
        )
    for result in results:
        yield clean_ocr_result(result)


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


def _clean_text(text: object) -> str:
    """Full deterministic cleanup pipeline over a raw OCR string."""
    if isinstance(text, bool) or not isinstance(text, str):
        raise TypeError(
            f"text must be a str, got "
            f"{type(text).__name__ if text is not None else 'None'}"
        )
    cleaned = unicodedata.normalize("NFC", text)
    cleaned = _normalize_newlines(cleaned)
    cleaned = _remove_control_characters(cleaned)
    cleaned = _strip_trailing_line_whitespace(cleaned)
    cleaned = _collapse_blank_lines(cleaned)
    return cleaned.strip()


def _normalize_newlines(text: str) -> str:
    """Normalize all newline representations to ``\\n``.

    ``\\r\\n`` and ``\\r`` are converted to ``\\n``; no other characters are
    touched, so meaningful line boundaries are preserved.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _remove_control_characters(text: str) -> str:
    """Remove invalid control characters introduced by OCR.

    Every ``Cc``-category character is removed except ``\\n`` and ``\\t``
    (:data:`_CONTROL_CHARACTER_EXCEPTIONS`). This covers NUL, escape, DEL, and
    the C1 range (U+0080..U+009F) while keeping normal whitespace and all
    meaningful Unicode characters.
    """
    return "".join(
        ch
        for ch in text
        if ch in _CONTROL_CHARACTER_EXCEPTIONS
        or unicodedata.category(ch) != "Cc"
    )


def _strip_trailing_line_whitespace(text: str) -> str:
    """Remove trailing spaces/tabs from every line.

    Only space and tab are removed (see :data:`_TRAILING_LINE_WHITESPACE`);
    intentional internal spacing and indentation are preserved.
    """
    return "\n".join(
        line.rstrip(_TRAILING_LINE_WHITESPACE) for line in text.split("\n")
    )


def _collapse_blank_lines(text: str) -> str:
    """Collapse pathological runs of blank lines.

    A blank line is a line without any non-whitespace character. Runs longer
    than :data:`MAX_CONSECUTIVE_BLANK_LINES` are reduced to that maximum;
    ordinary paragraph separation (up to the maximum) is preserved unchanged.
    """
    collapsed: list[str] = []
    blank_run = 0
    for line in text.split("\n"):
        if not line.strip():
            blank_run += 1
            if blank_run <= MAX_CONSECUTIVE_BLANK_LINES:
                collapsed.append("")
        else:
            blank_run = 0
            collapsed.append(line)
    return "\n".join(collapsed)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _validate_result(result: object) -> OCRResult:
    """Return ``result`` as a validated :class:`OCRResult`.

    Non-``OCRResult`` inputs raise :class:`TypeError`; page numbers that do not
    follow the project's 1-based ``int`` convention raise :class:`ValueError`.
    """
    if not isinstance(result, OCRResult):
        raise TypeError(
            "result must be an OCRResult (produced by ocr_page/ocr_pages), "
            f"got "
            f"{type(result).__name__ if result is not None else 'None'}"
        )
    page_number = result.page_number
    if isinstance(page_number, bool) or not isinstance(page_number, int):
        raise ValueError(
            f"page_number must be an int, got "
            f"{type(page_number).__name__}"
        )
    if page_number < 1:
        raise ValueError(
            f"page_number must be >= 1, got {page_number}"
        )
    return result