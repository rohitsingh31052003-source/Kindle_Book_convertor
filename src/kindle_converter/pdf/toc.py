"""Chapter-based table of contents generation (Milestone 2.11).

This module consumes the M2.9 chapter-detection output
(:class:`~kindle_converter.pdf.chapters.ChapterDetectionResult`) and produces
a small, immutable, deterministic table-of-contents representation
(:class:`TableOfContents`) that downstream consumers (such as a future EPUB
integration) can use.

The layer deliberately does NOT rediscover anything:

* It never re-runs chapter detection.
* It never scans raw PDF blocks.
* It never independently infers chapter boundaries or re-sorts content.
* It never re-classifies headings or duplicates heading detection.

M2.9 is the source of truth for chapter boundaries. Each detected chapter
becomes one top-level (level-1) TOC entry, in the exact order M2.9 already
established, with the exact chapter title/text and page number preserved.

Determinism: given the same :class:`ChapterDetectionResult`, the same
:class:`TableOfContents` is always produced. No randomization, timestamps,
filesystem ordering, sets, external services, or object memory addresses are
involved. The source result is never mutated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .chapters import ChapterDetectionResult, DetectedChapter


# --------------------------------------------------------------------------- #
# Constants (centralized, deterministic, documented)
# --------------------------------------------------------------------------- #

#: The single TOC level used for every chapter entry in V1. M2.4/M2.7 expose
#: generic headings only (``GENERIC_HEADING_LEVEL == 1``) and M2.9 exposes
#: chapter boundaries, not a heading hierarchy, so every detected chapter maps
#: to the same top level.
TOC_CHAPTER_LEVEL = 1


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TOCEntry:
    """One immutable table-of-contents entry.

    ``order`` and ``page_number`` are copied from the detected chapter so a
    consumer never has to reach back into ``chapter`` for ordering or page
    information. ``level`` is 1 for every V1 chapter entry because M2.9 does
    not expose a heading hierarchy. ``chapter`` preserves the M2.9 source
    chapter by reference (full provenance, never mutated). ``identifier`` is
    reserved for future EPUB anchor integration and is ``None`` in V1: the
    document and EPUB layers do not yet provide stable, book-level chapter
    anchors, so no identifier is invented here.
    """

    #: The chapter title/text exactly as M2.9 detected it.
    title: str

    #: The 1-based chapter order established by M2.9.
    order: int

    #: The 1-based source page number where the chapter starts.
    page_number: int

    #: The TOC level. Always :data:`TOC_CHAPTER_LEVEL` (1) for V1.
    level: int = TOC_CHAPTER_LEVEL

    #: The M2.9 DetectedChapter that produced this entry (provenance).
    chapter: "DetectedChapter | None" = None

    #: Reserved for future anchor/identifier integration (always None in V1).
    identifier: str | None = None


@dataclass(frozen=True, slots=True)
class TableOfContents:
    """An immutable, deterministic chapter-based table of contents.

    ``entries`` preserves the M2.9 detected-chapter order exactly. The
    container is deliberately minimal: an ordered, immutable sequence of
    :class:`TOCEntry` objects that a future EPUB layer can consume without
    knowing anything about EbookLib or EPUB implementation details.
    """

    entries: tuple[TOCEntry, ...] = ()

    @property
    def entry_count(self) -> int:
        """The number of TOC entries (equal to the number of chapters)."""
        return len(self.entries)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def generate_toc(chapters: "ChapterDetectionResult") -> TableOfContents:
    """Generate a chapter-based table of contents from M2.9 output.

    Consumes :func:`~kindle_converter.pdf.chapters.detect_chapters` output
    directly and never re-runs chapter detection. Each detected chapter
    becomes one level-1 entry in its detected order, with its title, page
    number, and source chapter preserved. A result with zero (or no) chapters
    yields a valid empty :class:`TableOfContents` -- no placeholder entries
    are manufactured.

    Parameters
    ----------
    chapters:
        A :class:`~kindle_converter.pdf.chapters.ChapterDetectionResult` from
        :func:`~kindle_converter.pdf.chapters.detect_chapters`.

    Returns
    -------
    TableOfContents
        An immutable, ordered, deterministic table of contents.

    Raises
    ------
    TypeError
        If ``chapters`` is not a :class:`ChapterDetectionResult`.
    """
    from .chapters import ChapterDetectionResult

    if not isinstance(chapters, ChapterDetectionResult):
        raise TypeError(
            f"generate_toc expects a ChapterDetectionResult, "
            f"got {type(chapters).__name__}"
        )

    entries = tuple(
        TOCEntry(
            title=chapter.text,
            order=chapter.order,
            page_number=chapter.page_number,
            level=TOC_CHAPTER_LEVEL,
            chapter=chapter,
            identifier=None,
        )
        for chapter in chapters.chapters
    )
    return TableOfContents(entries=entries)