"""Integrated M2 reconstruction (Milestone 2.7): the integration layer.

This module is deliberately **not** another detection algorithm. It combines
the outputs of the completed M2 layers into one immutable representation that
downstream consumers (the document-model adapter and the EPUB layer) can use
without re-running or re-deriving any detection:

* M2.1 (:mod:`kindle_converter.pdf.layout`) provides the page geometry,
  blocks, lines, spans, and typography metadata.
* M2.2/M2.6 (:mod:`kindle_converter.pdf.reading_order`) provides the
  deterministic reading order, including multi-column reconstruction.
* M2.3 (:mod:`kindle_converter.pdf.paragraphs`) provides the reconstructed
  paragraphs, their boundaries, and their source lines/blocks.
* M2.4 (:mod:`kindle_converter.pdf.headings`) provides the heading
  classification of every paragraph.
* M2.5 (:mod:`kindle_converter.pdf.header_footer`) provides the
  header/footer (page furniture) classification.
* M2.9 (:mod:`kindle_converter.pdf.chapters`) provides chapter detection
  on top of the integrated representation.

Integration rules
-----------------
* **Order**: the ``M2.2/M2.6 -> M2.3`` sequence is authoritative. Elements
  are never sorted by text, heading status, geometry, or font size.
* **Headings**: M2.4's per-paragraph classification is consumed as-is. No
  heading heuristic is re-run here. Every heading maps to the single
  generic document-model level :data:`GENERIC_HEADING_LEVEL` because M2.4
  detects *whether* text is a heading, not a semantic hierarchy.
* **Page furniture**: M2.5 only detects; this module is the first place
  where that detection affects emitted content. A paragraph referenced by
  any :class:`~kindle_converter.pdf.header_footer.DetectedHeaderFooter`
  ``source_paragraphs`` tuple is excluded from the body sequence *by
  source-object identity*, never by text matching, so a body paragraph
  that merely repeats a header string survives. The M2.5 result itself is
  kept unmodified on the integrated document.
* **Conflicts**: when a paragraph is classified as a heading by M2.4 *and*
  as page furniture by M2.5, the header/footer detection wins for body
  filtering: repeated page furniture is never promoted to body content.
  The heading classification itself remains untouched in the M2.4 result.
* **Immutability**: inputs are never mutated; paragraphs are retained by
  reference so provenance (page number, source lines/blocks, source line
  orders, reading-order indices) survives to the document model.

Determinism: given the same inputs, the same integrated document is always
produced. No randomization, timestamps, external services, OCR, NLP, or
machine learning is involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, TypeAlias, Union

from ..document import (
    Book,
    BookMetadata,
    Chapter,
    Heading,
    Image,
    PageBreak,
    Paragraph,
)
from .header_footer import (
    DetectedHeaderFooter,
    HeaderFooterLayout,
    detect_headers_footers,
)
from .headings import (
    ClassifiedParagraph,
    DetectedHeading,
    HeadingLayout,
    classify_paragraphs,
)
from .images import ImageAsset, ImageExtractionResult, ImagePlacement
from .layout import LayoutBlock, LayoutPage, PageLayout
from .paragraphs import (
    ParagraphLayout,
    ReconstructedParagraph,
    reconstruct_paragraphs,
)
from .reading_order import reconstruct_read_order

if TYPE_CHECKING:
    from .chapters import ChapterDetectionResult


class ElementKind(StrEnum):
    """The downstream body role of one integrated element."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"


@dataclass(frozen=True, slots=True)
class ReconstructedElement:
    """One body element in reconstructed reading order.

    ``kind`` is an integration decision, not a new detection: it mirrors
    the M2.4 classification of ``paragraph`` (M2.4 evidence only). A
    paragraph classified as page furniture by M2.5 never becomes an
    element at all; in particular, when such a paragraph is *also*
    classified as a heading, M2.5 wins and it is excluded from the body
    sequence.
    """

    kind: ElementKind
    paragraph: ReconstructedParagraph
    heading: DetectedHeading | None = None

    @property
    def text(self) -> str:
        """The element text (the underlying paragraph text)."""
        return self.paragraph.text

    @property
    def page_number(self) -> int:
        """The 1-based source page number."""
        return self.paragraph.page_number


@dataclass(frozen=True, slots=True)
class ReconstructedImage:
    """One embedded image anchored in the integrated body sequence."""

    placement: ImagePlacement
    asset: ImageAsset | None = None

    @property
    def page_number(self) -> int:
        """1-based source page number (from the placement)."""
        return self.placement.page_number

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """Placement rectangle ``(x0, y0, x1, y1)`` in points."""
        return self.placement.bbox

    @property
    def top(self) -> float:
        """Top edge used for vertical merge with text."""
        return self.placement.bbox[1]

    @property
    def asset_id(self) -> str:
        """Deterministic asset identifier this placement references."""
        return self.placement.asset_id


ReconstructedBodyElement: TypeAlias = Union[ReconstructedElement, ReconstructedImage]


@dataclass(frozen=True, slots=True)
class ReconstructedPage:
    """The integrated body elements of one source page, in M2.3 order."""

    page_number: int
    elements: tuple[ReconstructedElement, ...] = ()
    images: tuple[ReconstructedImage, ...] = ()


@dataclass(frozen=True, slots=True)
class ReconstructedDocument:
    """The integrated reconstruction of a whole document."""

    pages: tuple[ReconstructedPage, ...] = ()
    paragraphs: ParagraphLayout | None = None
    headings: HeadingLayout | None = None
    header_footer: HeaderFooterLayout | None = None
    chapters: "ChapterDetectionResult | None" = None

    @property
    def page_count(self) -> int:
        """The number of source pages (including empty-after-filter pages)."""
        return len(self.pages)

    @property
    def element_count(self) -> int:
        """The total number of body elements across all pages."""
        return sum(len(page.elements) for page in self.pages)

    @property
    def elements(self) -> tuple[ReconstructedElement, ...]:
        """Every body element in page/reading order."""
        return tuple(
            element for page in self.pages for element in page.elements
        )

    @property
    def image_count(self) -> int:
        """Total number of placed images across all pages."""
        return sum(len(page.images) for page in self.pages)

    @property
    def images(self) -> tuple[ReconstructedImage, ...]:
        """Every placed image in page/extraction order."""
        return tuple(image for page in self.pages for image in page.images)



def build_reconstructed_document(
    paragraphs: ParagraphLayout,
    headings: HeadingLayout,
    header_footer: HeaderFooterLayout,
    images: ImageExtractionResult | None = None,
) -> ReconstructedDocument:
    """Combine M2.3/M2.4/M2.5 results into one body sequence.

    The ``M2.2/M2.6 -> M2.3`` order is authoritative and never re-sorted.
    M2.5 furniture is excluded by source-object identity; when a paragraph
    is both a heading (M2.4) and page furniture (M2.5), the header/footer
    detection wins and the paragraph is excluded from body content.
    Inputs are never mutated; paragraphs are retained by reference.

    ``images`` is an optional M2.13 extraction result. When provided, its
    placements are attached per page in deterministic extraction order
    (M2.3 text is untouched; images never reorder text). ``None`` (default)
    keeps exact text-only behavior for existing callers.
    """
    heading_by_id: dict[int, ClassifiedParagraph] = {}
    for page in headings.pages:
        for classified in page.paragraphs:
            heading_by_id.setdefault(id(classified.paragraph), classified)

    excluded_ids: set[int] = set()
    for item in header_footer.detected:
        for source in _furniture_source_paragraphs(item):
            excluded_ids.add(id(source))

    pages: list[ReconstructedPage] = []
    assets_by_id: dict[str, ImageAsset] = {}
    placements_by_page: dict[int, list[ImagePlacement]] = {}
    if images is not None:
        if not isinstance(images, ImageExtractionResult):
            raise TypeError(
                "build_reconstructed_document expects images as "
                f"ImageExtractionResult, got {type(images).__name__}"
            )
        assets_by_id = {a.asset_id: a for a in images.assets}
        for placement in images.placements:
            placements_by_page.setdefault(
                placement.page_number, []
            ).append(placement)
    for para_page in paragraphs.pages:
        elements: list[ReconstructedElement] = []
        for paragraph in para_page.paragraphs:
            if id(paragraph) in excluded_ids:
                continue
            classified = heading_by_id.get(id(paragraph))
            if classified is not None and classified.is_heading:
                elements.append(
                    ReconstructedElement(
                        kind=ElementKind.HEADING,
                        paragraph=paragraph,
                        heading=DetectedHeading(
                            paragraph=paragraph,
                            score=classified.score,
                            reasons=classified.reasons,
                        ),
                    )
                )
            else:
                elements.append(
                    ReconstructedElement(
                        kind=ElementKind.PARAGRAPH,
                        paragraph=paragraph,
                        heading=None,
                    )
                )
        pages.append(
            ReconstructedPage(
                page_number=para_page.page_number,
                elements=tuple(elements),
                images=tuple(
                    ReconstructedImage(
                        placement=placement,
                        asset=assets_by_id.get(placement.asset_id),
                    )
                    for placement in sorted(
                        placements_by_page.get(
                            para_page.page_number, ()
                        ),
                        key=lambda p: p.order,
                    )
                ),
            )
        )
    return ReconstructedDocument(
        pages=tuple(pages),
        paragraphs=paragraphs,
        headings=headings,
        header_footer=header_footer,
    )


def ordered_body_elements(
    page: ReconstructedPage,
) -> tuple[ReconstructedBodyElement, ...]:
    """Merge one page's text + images into a deterministic body sequence.

    Text keeps its M2.2/M2.6 reading order among itself; images keep their
    extraction order among themselves; the two subsequences are interleaved
    by vertical position (element top ``y0`` vs image bbox top). Ties break
    toward text first, then by extraction order, so the merge is total and
    stable. This is the V1 image/text relative-placement representation: an
    image between two paragraphs appears between them in this sequence.
    M2.3 paragraph reconstruction and M2.4/M2.5/M2.9 layers are untouched.
    """
    keyed: list[
        tuple[float, int, int, ReconstructedBodyElement]
    ] = []
    for index, element in enumerate(page.elements):
        keyed.append((_element_top(element), 0, index, element))
    for index, image in enumerate(page.images):
        keyed.append((image.top, 1, index, image))
    keyed.sort(key=lambda item: (item[0], item[1], item[2]))
    return tuple(item[3] for item in keyed)


def _element_top(element: ReconstructedElement) -> float:
    """Return the top ``y0`` of an element's first source line."""
    lines = element.paragraph.source_lines
    if lines:
        try:
            return float(lines[0].bbox[1])
        except Exception:
            pass
    return float("-inf")


def _furniture_source_paragraphs(
    item: DetectedHeaderFooter,
) -> tuple[ReconstructedParagraph, ...]:
    """Return the source paragraphs claimed by one furniture candidate."""
    return tuple(item.source_paragraphs)


def deduplicate_layout(layout: PageLayout) -> PageLayout:
    """Drop byte-identical vertically-overlapping lines (M1 compat).

    Some PDFs draw the same line twice at the same position to fake a
    bold weight. The M1 extractor kept a single occurrence
    (:func:`extractor._overlaps`); the M2 layout representation preserves
    both draws, so the integration boundary restores the M1 de-duplication
    here instead of touching any detector. Only a line whose text is
    byte-identical to the previously kept line *and* whose vertical extent
    intersects it is dropped; identical lines stacked at different
    baselines (stanzas, table rows) are all kept. Empty blocks left behind
    are removed; page order and geometry are otherwise untouched.
    """
    pages: list[LayoutPage] = []
    for page in layout.pages:
        blocks: list[LayoutBlock] = []
        previous_text: str | None = None
        previous_bbox: tuple[float, float, float, float] | None = None
        for block in page.blocks:
            kept = []
            for line in block.lines:
                if (
                    previous_text is not None
                    and previous_bbox is not None
                    and line.text == previous_text
                    and previous_bbox[1] < line.bbox[3]
                    and line.bbox[1] < previous_bbox[3]
                ):
                    continue
                kept.append(line)
                previous_text = line.text
                previous_bbox = line.bbox
            if kept:
                blocks.append(
                    LayoutBlock(
                        text="\n".join(line.text for line in kept),
                        bbox=block.bbox,
                        order=block.order,
                        number=block.number,
                        lines=tuple(kept),
                    )
                )
        pages.append(
            LayoutPage(
                page_number=page.page_number,
                page_width=page.page_width,
                page_height=page.page_height,
                blocks=tuple(blocks),
                image_block_count=page.image_block_count,
            )
        )
    return PageLayout(pages=tuple(pages))


def reconstruct_layout(
    layout: PageLayout,
    images: ImageExtractionResult | None = None,
) -> tuple[
    ReconstructedDocument, ParagraphLayout, HeadingLayout, HeaderFooterLayout
]:
    """Run the full M2 stack over an M2.1 layout.

    Returns the integrated :class:`ReconstructedDocument` followed by the
    untouched intermediate results (M2.3 paragraph layout, M2.4 heading
    layout, M2.5 header/footer layout) so callers keep access to the
    original detections and provenance.

    The returned ReconstructedDocument also includes M2.9 chapter detection
    results in its ``chapters`` field.

    ``images`` is an optional M2.13 extraction result attached per page by
    :func:`build_reconstructed_document` (default ``None`` keeps text-only
    behavior).

    Raises
    ------
    TypeError
        If ``layout`` is not a
        :class:`~kindle_converter.pdf.layout.PageLayout`.
    """
    if not isinstance(layout, PageLayout):
        raise TypeError(
            f"reconstruct_layout expects a PageLayout, "
            f"got {type(layout).__name__}"
        )
    ordered = reconstruct_read_order(layout)
    paragraph_layout = reconstruct_paragraphs(ordered)
    heading_layout = classify_paragraphs(paragraph_layout)
    furniture = detect_headers_footers(paragraph_layout)
    document = build_reconstructed_document(
        paragraph_layout, heading_layout, furniture, images=images
    )
    # M2.9: Chapter detection on the integrated representation
    from .chapters import detect_chapters

    chapters = detect_chapters(document)
    # Reconstruct document with chapters attached (immutable, so create new)
    document = ReconstructedDocument(
        pages=document.pages,
        paragraphs=document.paragraphs,
        headings=document.headings,
        header_footer=document.header_footer,
        chapters=chapters,
    )
    return document, paragraph_layout, heading_layout, furniture


#: Generic EPUB heading level for every M2.4 heading. M2.4 detects
#: *whether* a paragraph is a heading, not a semantic hierarchy, so every
#: heading maps to ``Heading(level=1)``.
GENERIC_HEADING_LEVEL = 1


def reconstructed_document_to_book(
    document: ReconstructedDocument, metadata: BookMetadata
) -> Book:
    """Adapt an integrated reconstruction to the document-model ``Book``.

    All body content goes into a single chapter titled with the metadata
    title (mirroring M1). Every page boundary becomes one ``PageBreak``
    marker placed before the page's elements, headings become ``Heading``
    blocks at :data:`GENERIC_HEADING_LEVEL`, and paragraphs become
    ``Paragraph`` blocks in reconstructed reading order. Whitespace-only
    paragraphs are dropped; page furniture is already excluded by the
    integrated representation, so it never reaches the document model.
    The heading level is a single generic level: M2.4 detects *whether* a
    paragraph is a heading, not a semantic hierarchy, so no chapter or
    section structure is inferred here.

    M2.13 images ride along via :func:`ordered_body_elements`: each
    :class:`ReconstructedImage` becomes a document-model :class:`Image`
    block (with ``content_type``/``alt_text`` provenance, carrying the
    resolved bytes) at its merged position. Documents without images emit
    exactly the historical text-only block sequence.
    """
    chapter = Chapter(title=metadata.title or "")
    for page_index, page in enumerate(document.pages):
        if page_index > 0:
            chapter.blocks.append(PageBreak())
        for item in ordered_body_elements(page):
            if isinstance(item, ReconstructedImage):
                chapter.blocks.append(_reconstructed_image_to_block(item))
                continue
            text = item.paragraph.text
            if not text.strip():
                continue
            if item.kind is ElementKind.HEADING:
                chapter.blocks.append(
                    Heading(text=text, level=GENERIC_HEADING_LEVEL)
                )
            else:
                chapter.blocks.append(Paragraph(text=text))
    return Book(metadata=metadata, chapters=[chapter])


def _reconstructed_image_to_block(item: ReconstructedImage) -> Image:
    """Convert one placed image to a document-model :class:`Image` block."""
    asset = item.asset
    placement = item.placement
    if asset is not None:
        return Image(
            data=asset.data,
            content_type=asset.mime_type,
            alt_text=(
                f"{asset.asset_id} p{placement.page_number} "
                f"xref{placement.xref}"
            ),
        )
    return Image(
        data=b"",
        content_type=None,
        alt_text=(
            f"{placement.asset_id} p{placement.page_number} "
            f"xref{placement.xref}"
        ),
    )
