"""Explicit page-number identification and removal (Milestone 2.10).

This module consumes the M2.7/M2.9 integrated
:class:`~kindle_converter.pdf.reconstruction.ReconstructedDocument` and
provides a dedicated, conservative, deterministic page-number *removal*
operation.

Relationship to the other milestones
------------------------------------
* M2.5 (:mod:`kindle_converter.pdf.header_footer`) *detects* repeated page
  furniture (headers, footers, and those page-number forms that repeat
  reliably enough -- across ``MIN_REPEATED_PAGES`` pages with positional
  consistency -- to be classified as furniture). M2.7's
  :func:`build_reconstructed_document` then excludes that furniture from the
  reconstructed body *by source-object identity*.
* Page numbers that M2.5's conservative furniture detector does *not* catch
  survive into the integrated body. Typical survivors are: a two-page
  ``1, 2`` bare-number sequence (M2.5 needs three pages), or a single
  explicit ``Page 7 of 20`` on a lone page (M2.5 needs repetition or a
  multi-value sequence). M2.10 is the explicit layer that finds and removes
  those remaining artifacts.

M2.10 does NOT:
* re-extract PDF text or re-sort blocks
* re-run paragraph reconstruction or heading detection
* duplicate M2.5's full furniture scoring
* mutate the source document

It reuses M2.5's page-number patterns (:func:`header_footer._page_number_key`),
geometry helpers (:func:`header_footer._paragraph_bbox`,
:func:`header_footer._in_header_region` /
:func:`header_footer._in_footer_region`,
:func:`header_footer._normalize_text` and the header/footer region constants)
so the two milestones can never disagree about what a page-number form or a
page-edge region looks like.

Detection philosophy
--------------------
The detector is deliberately narrow and conservative. It only ever acts on a
paragraph that is *both* a page-number form *and* located in a page-edge
region. No single signal is decisive for bare numbers:

1. **Explicit page-number forms** -- ``Page N``, ``Page N of M``,
   ``N / M``, ``- N -`` -- at a page edge are removed on the strength of the
   form plus geometry alone. These forms are unambiguous.
2. **Bare numbers** -- ``N`` -- are removed only when they form a monotonic
   sequence of at least :data:`MIN_BARE_SEQUENCE_PAGES` distinct values at a
   consistent page edge. A lone bare number is reported as a candidate but is
   **left in the body**: it may be a year, a list number, a measurement, etc.
3. **Geometry** -- every candidate must sit in the header band (top
   ``HEADER_REGION_FRACTION`` of the page) or the footer band (bottom
   ``FOOTER_REGION_FRACTION``). Numbers embedded in body text are untouched.
4. **Headings are never removed.** A paragraph classified as a heading by M2.4
   is skipped entirely, so chapter numbers, section numbers, and numeric
   headings (``Chapter 1``, ``1. Introduction``) are preserved.

Years (``1947``, ``2024`` -- outside the bare ``\\d{1,3}`` pattern and not an
explicit page-number form), dates, measurements, prices, ISBNs, and list
numbering therefore fall outside the detection surface and are never removed.

Immutability & provenance
-------------------------
The input :class:`ReconstructedDocument` is never mutated. Removal produces a
new document that shares every upstream object by reference (paragraphs,
headings, header/footer analysis, chapters) and only replaces the body
``pages`` with a filtered copy. Each :class:`DetectedPageNumber` retains the
source paragraph so the original element is always identifiable.

Determinism
-----------
Given identical inputs the same result is always produced: candidates are
ordered by ``(page_number, location, edge position, value, text)`` and no
randomness, timestamps, external services, OCR, NLP, or machine learning is
involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from .header_footer import (
    FOOTER_POSITION_TOLERANCE_FRACTION,
    FOOTER_REGION_FRACTION,
    HEADER_POSITION_TOLERANCE_FRACTION,
    HEADER_REGION_FRACTION,
    _finite_positive,
    _in_footer_region,
    _in_header_region,
    _normalize_text,
    _page_number_key,
    _paragraph_bbox,
)
from .paragraphs import ParagraphLayout, ReconstructedParagraph

if TYPE_CHECKING:
    from .reconstruction import (
        ElementKind,
        ReconstructedDocument,
        ReconstructedElement,
        ReconstructedPage,
    )


# --------------------------------------------------------------------------- #
# Deterministic, documented thresholds
# --------------------------------------------------------------------------- #

#: Minimum number of *distinct* values a bare-number sequence must span to be
#: removed. M2.10 relaxes M2.5's ``MIN_REPEATED_PAGES`` (3) to 2 for the
#: page-number-specific case: an explicit page-number *form* combined with a
#: two-value monotonic sequence at a consistent page edge is already strong
#: evidence, whereas M2.5's broader furniture scoring (which must also handle
#: genuinely repeated body text) conservatively requires three pages.
#: Single isolated bare numbers are never removed.
MIN_BARE_SEQUENCE_PAGES = 2


class PageNumberLocation(StrEnum):
    """Where on the page a detected page number sits."""

    FOOTER = "footer"
    HEADER = "header"


# --------------------------------------------------------------------------- #
# Public result models
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DetectedPageNumber:
    """A paragraph the page-number detector identified as a page-number
    artifact.

    ``source_paragraph`` retains the original paragraph so the detected
    element can always be traced back to the reconstructed representation it
    came from. ``confidence`` is a deterministic heuristic in ``[0, 1]``
    (not a probability): explicit forms score higher than bare numbers, which
    require sequence evidence.
    """

    #: Original (un-normalized) text of the detected paragraph.
    text: str
    #: 1-based source page number of the paragraph.
    page_number: int
    #: The parsed page-number value (e.g. ``7`` for ``"Page 7 of 20"``).
    value: int
    #: Edge region the paragraph sat in.
    location: PageNumberLocation
    #: Deterministic evidence tokens explaining the classification.
    reasons: tuple[str, ...]
    #: Deterministic heuristic confidence in ``[0, 1]``.
    confidence: float
    #: Provenance: the source paragraph, preserved by reference.
    source_paragraph: ReconstructedParagraph


@dataclass(frozen=True, slots=True)
class PageNumberRemovalResult:
    """Result of :func:`remove_page_numbers`.

    ``candidates`` is the complete, inspectable set of paragraphs the detector
    considered as page-number artifacts (every page-number form found at a
    page edge); ``removed`` is the subset that met the full evidence bar and
    were therefore excluded from ``document``. A candidate that matched a
    page-number form and sat at an edge but lacked sequence evidence (e.g. a
    lone bare number) appears in ``candidates`` but not in ``removed``.
    """

    #: A new document with page-number artifacts excluded from the body. The
    #: input document is never mutated.
    document: "ReconstructedDocument"
    #: Page-number artifacts actually removed from the body.
    removed: tuple[DetectedPageNumber, ...] = ()
    #: All page-number-form paragraphs found at a page edge (considered).
    candidates: tuple[DetectedPageNumber, ...] = ()

    @property
    def removed_count(self) -> int:
        """Number of page-number artifacts removed from the body."""
        return len(self.removed)


# --------------------------------------------------------------------------- #
# Internal working type
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _PageNumberInfo:
    """One body paragraph that matches a page-number form at a page edge."""

    element: "ReconstructedElement"
    template: str  # "n" | "page n" | "page n of M" | "n / M" | "- n -"
    value: int
    location: PageNumberLocation
    edge_fraction: float  # normalized vertical position, for consistency checks


# --------------------------------------------------------------------------- #
# Page-dimension lookup
# --------------------------------------------------------------------------- #


def _page_dimensions(
    source: "ReconstructedDocument",
) -> dict[int, tuple[float, float]]:
    """Map each page number to ``(page_width, page_height)`` for edge math.

    Dimensions come from the upstream :class:`ParagraphLayout`'s
    :class:`~kindle_converter.pdf.layout.LayoutPage` objects -- the normal
    M2.1 path carried through M2.7. Documents without a paragraph layout have
    *no* page dimensions and therefore produce no candidates: without real
    geometry, edge math would be guesswork, and guessing is exactly what this
    milestone refuses to do. (M2.7's ``build_reconstructed_document`` always
    attaches the paragraph layout, so the real pipeline always has
    dimensions.)
    """
    dims: dict[int, tuple[float, float]] = {}
    paragraphs_layout = source.paragraphs
    if isinstance(paragraphs_layout, ParagraphLayout):
        for ppage in paragraphs_layout.pages:
            page = ppage.page
            width = _finite_positive(page.page_width)
            height = _finite_positive(page.page_height)
            if width is not None and height is not None and width > 0 and height > 0:
                dims[ppage.page_number] = (width, height)
    return dims


# --------------------------------------------------------------------------- #
# Candidate collection
# --------------------------------------------------------------------------- #


def _collect_edge_candidates(
    source: "ReconstructedDocument",
    dims: dict[int, tuple[float, float]],
) -> list["_PageNumberInfo"]:
    """Collect every body paragraph that is a page-number form at a page edge.

    Headings (``ElementKind.HEADING``) are skipped so chapter/section
    headings are never removed. Returns results in document order (pages,
    then elements).
    """
    from .reconstruction import ElementKind  # local import: avoids module cycle

    infos: list[_PageNumberInfo] = []
    for page in source.pages:
        height = dims.get(page.page_number, (None, None))[1]
        if height is None or height <= 0:
            continue
        for element in page.elements:
            if element.kind is ElementKind.HEADING:
                continue
            paragraph = element.paragraph
            bbox = _paragraph_bbox(paragraph.source_lines, paragraph.source_blocks)
            if bbox is None:
                continue
            _, y0, _, y1 = bbox
            top_fraction = min(max(y0 / height, 0.0), 1.0)
            bottom_fraction = min(max(y1 / height, 0.0), 1.0)
            # Mirror M2.5's documented tie-break (`_region_for`): the header
            # band takes precedence when a paragraph straddles both bands.
            if _in_header_region(top_fraction):
                location = PageNumberLocation.HEADER
                edge_fraction = top_fraction
            elif _in_footer_region(bottom_fraction):
                location = PageNumberLocation.FOOTER
                edge_fraction = bottom_fraction
            else:
                continue  # number is in body text, not at an edge
            norm_text = _normalize_text(paragraph.text)
            key = _page_number_key(norm_text)
            if key is None:
                continue
            template, value = key
            infos.append(
                _PageNumberInfo(
                    element=element,
                    template=template,
                    value=value,
                    location=location,
                    edge_fraction=edge_fraction,
                )
            )
    return infos


# --------------------------------------------------------------------------- #
# Bare-number sequence detection
# --------------------------------------------------------------------------- #


def _form_is_bare(template: str) -> bool:
    """Whether the page-number template is a bare number (``"n"``)."""
    return template == "n"


def _detect_bare_sequence_members(
    infos: list["_PageNumberInfo"],
) -> set[int]:
    """Return element ids of bare numbers that form a qualifying sequence.

    A qualifying sequence is, per edge location (footer/header), a set of
    bare numbers on *distinct* pages whose values are monotonic (ascending or
    descending) with at least :data:`MIN_BARE_SEQUENCE_PAGES` distinct
    values, and whose normalized edge positions are consistent within the
    M2.5 positional tolerance. The whole sequence is removed or none of it
    is -- partial removal would be unsafe.
    """
    removed_ids: set[int] = set()
    for location in (PageNumberLocation.FOOTER, PageNumberLocation.HEADER):
        group = [
            info
            for info in infos
            if info.location == location and _form_is_bare(info.template)
        ]
        if len(group) < MIN_BARE_SEQUENCE_PAGES:
            continue
        # One representative occurrence per page (first encountered wins; a
        # page number occupies a single consistent position per page).
        by_page: dict[int, _PageNumberInfo] = {}
        for info in group:
            by_page.setdefault(info.element.page_number, info)
        pages = sorted(by_page)
        if len(pages) < MIN_BARE_SEQUENCE_PAGES:
            continue
        values = [by_page[p].value for p in pages]
        if len(set(values)) < MIN_BARE_SEQUENCE_PAGES:
            continue  # identical numbers -> repetition, not a sequence (M2.5's job)
        ascending = all(a <= b for a, b in zip(values, values[1:]))
        descending = all(a >= b for a, b in zip(values, values[1:]))
        if not (ascending or descending):
            continue
        positions = [by_page[p].edge_fraction for p in pages]
        tolerance = (
            FOOTER_POSITION_TOLERANCE_FRACTION
            if location is PageNumberLocation.FOOTER
            else HEADER_POSITION_TOLERANCE_FRACTION
        )
        if max(positions) - min(positions) > tolerance:
            continue
        for info in (by_page[p] for p in pages):
            removed_ids.add(id(info.element.paragraph))
    return removed_ids


# --------------------------------------------------------------------------- #
# Result construction
# --------------------------------------------------------------------------- #


def _form_name(template: str) -> str:
    """A stable, human-readable name for a page-number template."""
    if template == "n":
        return "bare_number"
    if template == "page n":
        return "page_n"
    if template.startswith("page n of"):
        return "page_n_of_total"
    if template.startswith("n /"):
        return "n_over_total"
    if template == "- n -":
        return "dashed_n"
    return "other"


def _info_sort_key(info: "_PageNumberInfo") -> tuple:
    """Deterministic ordering key for candidates/removed lists."""
    location_order = 0 if info.location is PageNumberLocation.FOOTER else 1
    return (
        info.element.page_number,
        location_order,
        round(info.edge_fraction, 6),
        info.value,
        info.element.paragraph.text,
    )


def _to_detected(info: "_PageNumberInfo", *, in_sequence: bool) -> DetectedPageNumber:
    """Convert an internal candidate to the public :class:`DetectedPageNumber`."""
    if not _form_is_bare(info.template):
        # Explicit form at an edge is unambiguous.
        confidence = 1.0
        reasons = (
            "page_number_pattern",
            f"position_in_{info.location.value}_region",
            f"form:{_form_name(info.template)}",
            "explicit_page_number_form",
        )
    elif in_sequence:
        confidence = 0.9
        reasons = (
            "page_number_pattern",
            f"position_in_{info.location.value}_region",
            "bare_number_sequence",
            "positional_consistency",
        )
    else:
        # Bare number at an edge without a sequence: considered, but kept.
        confidence = 0.5
        reasons = (
            "page_number_pattern",
            f"position_in_{info.location.value}_region",
            "form:bare_number",
            "insufficient_sequence_evidence",
        )
    return DetectedPageNumber(
        text=info.element.paragraph.text,
        page_number=info.element.page_number,
        value=info.value,
        location=info.location,
        reasons=reasons,
        confidence=confidence,
        source_paragraph=info.element.paragraph,
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def remove_page_numbers(
    source: "ReconstructedDocument",
) -> PageNumberRemovalResult:
    """Explicitly identify and remove page-number artifacts from a document.

    Consumes the M2.7/M2.9 integrated :class:`ReconstructedDocument`. Page
    numbers that survived M2.5/M2.7 -- for example a two-page ``1, 2``
    sequence (M2.5 needs three pages) or a single explicit
    ``Page 7 of 20`` at the footer (M2.5 needs repetition or a multi-value
    sequence) -- are detected using M2.5's page-number patterns and region
    geometry and excluded from the body.

    Legitimate numeric content is preserved: years (``1947``), 4-digit
    numbers, dates, measurements, prices, ISBNs, list numbering, chapter
    numbers, and headings are all outside the detection surface.

    The input is never mutated; a new document is returned. The new document
    shares the upstream paragraph/heading/header-footer/chapter objects by
    reference and only the body ``pages`` are filtered.

    Parameters
    ----------
    source:
        An integrated
        :class:`~kindle_converter.pdf.reconstruction.ReconstructedDocument`
        from :func:`~kindle_converter.pdf.reconstruction.reconstruct_layout`
        or :func:`~kindle_converter.pdf.reconstruction.build_reconstructed_document`
        (optionally with M2.9 chapters attached).

    Returns
    -------
    PageNumberRemovalResult
        The filtered document plus the removed artifacts and all candidates
        considered. ``removed_count`` is the number of body elements dropped.

    Raises
    ------
    TypeError
        If ``source`` is not a :class:`ReconstructedDocument`.
    """
    from .reconstruction import (
        ElementKind,
        ReconstructedDocument,
        ReconstructedPage,
    )

    if not isinstance(source, ReconstructedDocument):
        raise TypeError(
            f"remove_page_numbers expects a ReconstructedDocument, "
            f"got {type(source).__name__}"
        )

    dims = _page_dimensions(source)
    form_infos = _collect_edge_candidates(source, dims)

    # Explicit forms at an edge are always removed; bare numbers only when
    # they participate in a qualifying monotonic sequence.
    bare_sequence_ids = _detect_bare_sequence_members(form_infos)
    removed_ids: set[int] = {
        id(info.element.paragraph)
        for info in form_infos
        if (not _form_is_bare(info.template))
        or (id(info.element.paragraph) in bare_sequence_ids)
    }

    sorted_infos = sorted(form_infos, key=_info_sort_key)
    candidates = tuple(
        _to_detected(
            info,
            in_sequence=(id(info.element.paragraph) in bare_sequence_ids),
        )
        for info in sorted_infos
    )
    removed = tuple(
        candidate
        for candidate in candidates
        if id(candidate.source_paragraph) in removed_ids
    )

    # Build a new document without mutating the source. Only the body ``pages``
    # change; every upstream object (paragraphs, headings, header/footer
    # analysis, chapters) is shared by reference so provenance is preserved.
    new_pages = tuple(
        ReconstructedPage(
            page_number=page.page_number,
            elements=tuple(
                element
                for element in page.elements
                if id(element.paragraph) not in removed_ids
            ),
        )
        for page in source.pages
    )
    new_document = ReconstructedDocument(
        pages=new_pages,
        paragraphs=source.paragraphs,
        headings=source.headings,
        header_footer=source.header_footer,
        chapters=source.chapters,
    )

    # Invariant: every removed candidate is absent from the new body. Asserted
    # (never silently broken) so the contract is testable.
    kept_paragraph_ids = {
        id(el.paragraph) for page in new_pages for el in page.elements
    }
    for removed_id in removed_ids:
        assert removed_id not in kept_paragraph_ids

    return PageNumberRemovalResult(
        document=new_document,
        removed=removed,
        candidates=candidates,
    )
